#!/usr/bin/env python3
"""Validate a StarunWiki knowledge pack and build its deterministic corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any

import yaml

from .errors import StarunWikiError
from .pack import KnowledgePack, load_pack, repository_root


DEFAULT_PACK = load_pack("deep-sky")
ROOT = DEFAULT_PACK.root  # v0.2 compatibility for callers that inspected ROOT.
DEFAULT_OUTPUT = repository_root() / ".knowledge-catalog" / "retrieval-corpus.jsonl"
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
REQUIRED_FIELDS = {
    "type", "title", "description", "tags", "status", "generated", "review",
    "stale_after", "applies_to", "sources",
}
VALID_STATUSES = {"draft", "stable", "deprecated"}


class CatalogError(StarunWikiError):
    pass


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def parse_frontmatter(path: Path, pack: KnowledgePack = DEFAULT_PACK) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        raise CatalogError(f"缺少 YAML frontmatter：{path.relative_to(pack.root)}")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise CatalogError(f"frontmatter 未闭合：{path.relative_to(pack.root)}")
    try:
        metadata = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        raise CatalogError(f"frontmatter 解析失败：{path.relative_to(pack.root)}：{exc}") from exc
    if not isinstance(metadata, dict):
        raise CatalogError(f"frontmatter 必须是对象：{path.relative_to(pack.root)}")
    return metadata, normalize_text(text[end + 5 :])


def page_slug(path: Path, pack: KnowledgePack = DEFAULT_PACK) -> str:
    domain = pack.category_slugs().get(path.parent.name)
    if not domain:
        raise CatalogError(f"未定义公开领域 slug：{path.parent.name}")
    stem = unicodedata.normalize("NFKC", path.stem).lower()
    stem = re.sub(r"[^a-z0-9\u3400-\u9fff]+", "-", stem).strip("-")
    if not stem:
        raise CatalogError(f"无法生成 slug：{path.relative_to(pack.root)}")
    return f"concept/{domain}/{stem}"


def formal_pages(pack: KnowledgePack = DEFAULT_PACK) -> list[Path]:
    configured = pack.category_slugs()
    unknown = sorted(
        path.name for path in pack.root.iterdir()
        if path.is_dir() and re.match(r"^[0-9]{2}-", path.name) and path.name not in configured
    )
    if unknown:
        raise CatalogError("存在未分类的正式目录：" + ", ".join(unknown))
    pages: list[Path] = []
    for category in pack.categories:
        root = pack.resolve(category.path, f"category.{category.path}", must_exist=True)
        if not root.is_dir() or not (root / "index.md").is_file():
            raise CatalogError(f"正式目录或索引缺失：{category.path}/index.md")
        pages.extend(path for path in sorted(root.glob("*.md")) if path.name != "index.md")
    return pages


def resolve_internal_link(source: Path, raw_target: str, pack: KnowledgePack = DEFAULT_PACK) -> Path | None:
    target = urllib.parse.unquote(raw_target.strip()).split("#", 1)[0].split("?", 1)[0]
    if not target or target.startswith(("http://", "https://", "mailto:")):
        return None
    resolved = pack.root / target.lstrip("/") if target.startswith("/") else source.parent / target
    if target.endswith("/") or resolved.is_dir():
        resolved = resolved / "index.md"
    candidate = resolved.resolve()
    pack_root = pack.root.resolve()
    if candidate != pack_root and pack_root not in candidate.parents:
        raise CatalogError(f"内部链接逃逸知识包目录：{raw_target}")
    return candidate


def validate_root_index(pack: KnowledgePack = DEFAULT_PACK) -> None:
    metadata, _ = parse_frontmatter(pack.resolve(pack.index_path, "content.index", must_exist=True), pack)
    if metadata != {"okf_version": "0.2"}:
        raise CatalogError("知识包 index.md frontmatter 必须且只能声明 okf_version: '0.2'")


def build_records(pack: KnowledgePack = DEFAULT_PACK) -> list[dict[str, Any]]:
    pack.validate()
    validate_root_index(pack)
    pages = formal_pages(pack)
    page_set = {path.resolve() for path in pages}
    all_markdown = {
        path.resolve() for path in pack.root.rglob("*.md")
        if not any(part in {"releases", "tools", "profile"} for part in path.relative_to(pack.root).parts)
    }
    records: list[dict[str, Any]] = []
    slugs: set[str] = set()
    titles: set[str] = set()

    for path in pages:
        relative = path.relative_to(pack.root).as_posix()
        metadata, body = parse_frontmatter(path, pack)
        missing = sorted(field for field in REQUIRED_FIELDS if metadata.get(field) in (None, ""))
        if missing:
            raise CatalogError(f"{relative} 缺少必填字段：{', '.join(missing)}")
        if metadata.get("status") not in VALID_STATUSES:
            raise CatalogError(f"{relative} status 非法：{metadata.get('status')}")
        generated = metadata.get("generated")
        if not isinstance(generated, dict) or not generated.get("by") or not generated.get("at"):
            raise CatalogError(f"{relative} generated 必须包含 by/at")
        review = metadata.get("review") or {}
        if not isinstance(review, dict) or not review.get("state"):
            raise CatalogError(f"{relative} review.state 不能为空")
        applies_to = metadata.get("applies_to") or {}
        if not isinstance(applies_to, dict):
            raise CatalogError(f"{relative} applies_to 必须是对象")
        for field in ("系统", "条件", "不适用"):
            values = applies_to.get(field)
            if not isinstance(values, list) or not values or not all(str(value).strip() for value in values):
                raise CatalogError(f"{relative} applies_to.{field} 必须是非空列表")
        verified = metadata.get("verified")
        if verified:
            if not isinstance(verified, dict):
                raise CatalogError(f"{relative} verified 必须是包含 by/at/scope 的对象")
            verifier = str(verified.get("by") or "")
            if not verifier.startswith("human:"):
                raise CatalogError(f"{relative} verified.by 必须使用 human: Actor")
            if not verified.get("at") or not verified.get("scope"):
                raise CatalogError(f"{relative} verified 必须包含 at/scope")
        sources = metadata.get("sources")
        if not isinstance(sources, list) or not sources:
            raise CatalogError(f"{relative} sources 必须是非空列表")
        source_ids: list[str] = []
        for source in sources:
            if not isinstance(source, dict) or not source.get("id") or not source.get("resource"):
                raise CatalogError(f"{relative} 的每个 source 必须包含 id/resource")
            source_ids.append(str(source["id"]))
        if len(source_ids) != len(set(source_ids)):
            raise CatalogError(f"{relative} 存在重复 source id")

        title = str(metadata["title"]).strip()
        if title in titles:
            raise CatalogError(f"正式页标题重复：{title}")
        titles.add(title)
        slug = page_slug(path, pack)
        if slug in slugs:
            raise CatalogError(f"正式页 slug 重复：{slug}")
        slugs.add(slug)

        links: list[str] = []
        for _, raw_target in MARKDOWN_LINK_RE.findall(body):
            target = resolve_internal_link(path, raw_target, pack)
            if target is None:
                continue
            if target not in all_markdown and not target.exists():
                raise CatalogError(f"{relative} 存在断链：{raw_target}")
            if target in page_set:
                links.append(target.relative_to(pack.root).as_posix())

        records.append({
            "access": "public_candidate",
            "category": path.parent.name,
            "content_withheld": False,
            "description": str(metadata["description"]).strip(),
            "display_name": title,
            "entry_name": relative,
            "links": sorted(set(links)),
            "review_state": str(review["state"]),
            "slug": slug,
            "source_ids": sorted(source_ids),
            "stale_after": str(metadata.get("stale_after") or "") or None,
            "status": str(metadata["status"]),
            "tags": sorted(str(tag) for tag in metadata.get("tags") or []),
            "text": body,
            "type": str(metadata["type"]),
            "verified": bool(verified),
            "verified_at": str(verified.get("at")) if verified else None,
            "verified_by": str(verified.get("by")) if verified else None,
            "verified_scope": str(verified.get("scope")) if verified else None,
        })
    return records


def render(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)


def corpus_summary(pack: KnowledgePack = DEFAULT_PACK) -> tuple[str, dict[str, Any]]:
    records = build_records(pack)
    content = render(records)
    return content, {
        "pack_id": pack.pack_id,
        "page_count": len(records),
        "needs_human_review": sum(row["review_state"] == "needs-human-review" for row in records),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", default="deep-sky")
    parser.add_argument("--write", action="store_true", help="写入确定性 retrieval-corpus.jsonl")
    parser.add_argument("--check", action="store_true", help="验证现有 corpus 未漂移")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    pack = load_pack(args.pack)
    content, summary = corpus_summary(pack)
    if args.check and (not args.output.is_file() or args.output.read_text(encoding="utf-8") != content):
        raise CatalogError(f"检索语料缺失或已漂移：{args.output}")
    if args.write:
        write_atomic(args.output, content)
    summary.update({"bundle_root": str(pack.root), "output": str(args.output), "written": args.write})
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except StarunWikiError as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        raise SystemExit(2)
