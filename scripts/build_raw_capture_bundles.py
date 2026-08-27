#!/usr/bin/env python3
"""Render non-public, text-only official-source captures from JSONL crawler output.

The generated files belong to the local ``raw/`` evidence layer.  They are
deliberately outside the formal numbered directories, so the public catalogue
builder cannot publish or index them.  The command never downloads media and
never removes old captures; an unsuccessful source is represented explicitly
instead of being silently skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "raw" / "official-captures" / "2026-08-27-smart-telescope"


class CaptureError(RuntimeError):
    pass


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_jsonl(path: Path, site: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise CaptureError(f"输入不存在：{path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CaptureError(f"{path}:{line_number} 不是 JSON：{exc}") from exc
        if not isinstance(row, dict):
            raise CaptureError(f"{path}:{line_number} 不是对象")
        row["site"] = str(row.get("site") or site)
        row["_input"] = str(path)
        row["_line"] = line_number
        rows.append(row)
    return rows


def value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        candidate = row.get(key)
        if candidate is None:
            continue
        if isinstance(candidate, list):
            return ", ".join(str(item).strip() for item in candidate if str(item).strip())
        text = str(candidate).strip()
        if text:
            return text
    return ""


def raw_text_value(row: dict[str, Any]) -> str:
    """Return crawler text exactly as supplied so its source hash remains checkable."""
    for key in ("raw_text", "text"):
        candidate = row.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return ""


def safe_name(value_: str) -> str:
    normalized = re.sub(r"[^a-z0-9\u3400-\u9fff]+", "-", value_.lower()).strip("-")
    return normalized[:72] or "untitled"


def status_for(row: dict[str, Any], text: str) -> str:
    explicit = value(row, "capture_status", "extraction_status", "extract_status", "status")
    if explicit:
        return explicit
    return "captured" if text else "missing-content"


def source_id(row: dict[str, Any], url: str) -> str:
    site = safe_name(value(row, "site") or "official")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"{site}-{digest}"


def markdown_page(row: dict[str, Any], ordinal: int) -> tuple[str, str, dict[str, Any]]:
    url = value(row, "canonical_url", "url")
    title = value(row, "title") or "未能解析标题"
    captured_text = raw_text_value(row)
    text = normalize(captured_text) if captured_text else ""
    category = value(row, "category_path", "category", "category_name")
    scope = value(row, "product_scope", "device_scope", "product_or_version_scope", "applies_to")
    source_role = value(row, "source_role")
    is_official = not source_role or source_role == "official-documentation"
    updated_at = value(row, "source_updated_at", "official_updated_at", "updated_at", "update_time")
    fetched_at = value(row, "fetched_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    capture_status = status_for(row, text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
    captured_digest = hashlib.sha256(captured_text.encode("utf-8")).hexdigest() if captured_text else ""
    crawler_digest = value(row, "content_hash", "text_sha256", "sha256").removeprefix("sha256:")
    if crawler_digest and digest and crawler_digest not in {captured_digest, digest}:
        raise CaptureError(f"正文哈希不匹配：{url or title}")
    site = value(row, "site") or "official"
    source = source_id(row, url or f"{site}:{ordinal}:{title}")
    filename = f"{ordinal:03d}-{safe_name(title)}-{source.rsplit('-', 1)[-1][:8]}.md"

    source_metadata = (
        {"official_updated_at": updated_at or None}
        if is_official
        else {"source_role": source_role, "source_updated_at": updated_at or None}
    )
    metadata = {
        "raw_schema_version": 1,
        "type": "official-source-capture" if is_official else "web-source-capture",
        "title": title,
        "site": site,
        "source_id": source,
        "canonical_url": url,
        "category_path": category or None,
        "applies_to": scope or None,
        **source_metadata,
        "fetched_at": fetched_at,
        "rights": value(row, "rights") or "unknown",
        "usage": value(row, "usage") or "local-evidence-capture; public-link-only",
        "access": "restricted",
        "content_withheld": True,
        "media_copied": False,
        "capture_status": capture_status,
        "text_sha256": digest or None,
        "captured_text_sha256": captured_digest or None,
        "crawler_text_sha256": crawler_digest or None,
        "crawler_input": Path(str(row["_input"])).name,
        "crawler_line": row["_line"],
    }
    frontmatter = "\n".join(
        f"{key}: {json.dumps(item, ensure_ascii=False)}" for key, item in metadata.items() if item is not None
    )
    if is_official:
        body = [
            "---",
            frontmatter,
            "---",
            "",
            f"# {title}",
            "",
            "## 捕获范围",
            "",
            "- 此页是仅供本地溯源的官网文本捕获，不进入公开语料、公开搜索或 LLM Wiki。",
            "- 未下载或嵌入图片、视频、PDF、附件及其他媒体。",
            "- `rights: unknown`；公开端只可使用派生结论和原始链接，不能把本页作为可公开转载正文。",
            f"- 文本提取方式：`{capture_status}`。",
            "",
            "## 来源元数据",
            "",
            f"- 规范 URL：{url or '未取得'}",
            f"- 官网分类：{category or '未取得'}",
            f"- 适用产品 / 版本：{scope or '未取得'}",
            f"- 官网更新时间：{updated_at or '页面未显示或未取得'}",
            f"- 抓取时间：{fetched_at}",
            f"- 捕获状态：{capture_status}",
            "",
        ]
        evidence_heading = "## 官网可见文本捕获"
    else:
        body = [
            "---",
            frontmatter,
            "---",
            "",
            f"# {title}",
            "",
            "## 捕获范围",
            "",
            "- 此页是仅供本地溯源的网页文本捕获，不进入公开语料、公开搜索或 LLM Wiki。",
            "- 未下载或嵌入图片、视频、PDF、附件及其他媒体。",
            "- `rights: unknown`；公开端只可使用派生结论和原始链接，不能把本页作为可公开转载正文。",
            f"- 文本提取方式：`{capture_status}`。",
            "",
            "## 来源元数据",
            "",
            f"- 规范 URL：{url or '未取得'}",
            f"- 来源角色：{source_role}",
            f"- 来源分类：{category or '未取得'}",
            f"- 适用产品 / 版本：{scope or '未取得'}",
            f"- 来源页面更新时间：{updated_at or '页面未显示或未取得'}",
            f"- 抓取时间：{fetched_at}",
            f"- 捕获状态：{capture_status}",
            "",
        ]
        evidence_heading = "## 网页可见文本捕获"
    if capture_status == "ocr-captured":
        body.extend(
            [
                "> 本页正文由临时加载的官网教程画面经 OCR 提取；未保留媒体文件。OCR 文字可能有识别误差，使用前须回到官网画面人工核对。",
                "",
            ]
        )
    body.extend([evidence_heading, ""])
    if text:
        body.append(text.rstrip())
    else:
        body.extend(
            [
                "未取得可用文本。保留此记录是为了让后续采集能定位失败页面，而不是将其误记为内容为空。",
                "",
                f"采集器状态：{capture_status}",
            ]
        )
    return filename, "\n".join(body).rstrip() + "\n", metadata


def manifest_row(filename: str, metadata: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "site",
        "source_id",
        "title",
        "canonical_url",
        "category_path",
        "applies_to",
        "official_updated_at",
        "fetched_at",
        "rights",
        "usage",
        "access",
        "content_withheld",
        "media_copied",
        "capture_status",
        "text_sha256",
    )
    row = {"path": filename, **{field: metadata.get(field) for field in fields}}
    if metadata.get("source_role"):
        row["source_role"] = metadata["source_role"]
        row["source_updated_at"] = metadata.get("source_updated_at")
    return row


def render_index(rows: list[dict[str, Any]], generated_at: str) -> str:
    count_by_site = Counter(str(row.get("site") or "official") for row in rows)
    count_by_status = Counter(str(row.get("capture_status") or "unknown") for row in rows)
    contains_non_official = any(row.get("source_role") for row in rows)
    title = "# 网页原始文本捕获" if contains_non_official else "# 智能望远镜与深空后期官网原始文本捕获"
    scope = (
        "本目录是本地、非公开的证据捕获层。它先于正式知识页保存网页可见文本、URL、产品范围、抓取日期和哈希；正式页只能综合、改写并链接到原始网页，不能公开镜像这些文本。"
        if contains_non_official
        else "本目录是本地、非公开的证据捕获层。它先于正式知识页保存官网可见文本、URL、产品范围、抓取日期和哈希；正式页只能综合、改写并链接到官网，不能公开镜像这些文本。"
    )
    lines = [
        title,
        "",
        scope,
        "",
        f"- 构建时间：{generated_at}",
        f"- 总记录：{len(rows)}",
        "- 权利状态：全部 `unknown`",
        "- 使用边界：`local-evidence-capture; public-link-only`",
        "- 媒体：未下载图片、视频、PDF 或附件。",
        "",
        "## 覆盖统计",
        "",
        "| 站点 | 记录数 |",
        "|---|---:|",
    ]
    lines.extend(f"| {site} | {count} |" for site, count in sorted(count_by_site.items()))
    lines.extend(
        [
            "",
            "## 捕获状态",
            "",
            "| 状态 | 数量 |",
            "|---|---:|",
        ]
    )
    lines.extend(f"| {status} | {count} |" for status, count in sorted(count_by_status.items()))
    lines.extend(
        [
            "",
            "## 文件",
            "",
            "每个页面均保存为独立 Markdown 文件；`capture-manifest.jsonl` 提供稳定的机器可读索引和正文哈希。",
            "",
        ]
    )
    return "\n".join(lines)


def build(output: Path, inputs: list[tuple[str, Path]], check: bool) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for site, path in inputs:
        rows.extend(load_jsonl(path, site))
    if not rows:
        raise CaptureError("没有可构建的捕获记录")
    deduplicated: dict[str, dict[str, Any]] = {}
    for row in rows:
        url = value(row, "canonical_url", "url")
        key = url or f"{row['site']}:{row['_input']}:{row['_line']}"
        if key in deduplicated:
            raise CaptureError(f"重复规范 URL：{url}")
        deduplicated[key] = row
    rows = sorted(deduplicated.values(), key=lambda row: (value(row, "site"), value(row, "title"), value(row, "canonical_url")))

    captured_at = [value(row, "fetched_at") for row in rows if value(row, "fetched_at")]
    generated_at = max(captured_at) if captured_at else "unknown"
    manifest: list[dict[str, Any]] = []
    expected: dict[Path, str] = {}
    for ordinal, row in enumerate(rows, 1):
        filename, page, metadata = markdown_page(row, ordinal)
        relative = Path("pages") / safe_name(str(metadata["site"])) / filename
        expected[relative] = page
        manifest.append(manifest_row(relative.as_posix(), metadata))

    manifest_text = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in manifest)
    expected[Path("capture-manifest.jsonl")] = manifest_text
    expected[Path("index.md")] = render_index(manifest, generated_at)

    mismatched = [relative.as_posix() for relative, content in expected.items() if not (output / relative).is_file() or (output / relative).read_text(encoding="utf-8") != content]
    if check:
        if mismatched:
            raise CaptureError("raw 捕获目录缺失或漂移：" + ", ".join(mismatched[:20]))
    else:
        for relative, content in expected.items():
            atomic_write(output / relative, content)

    return {
        "output": str(output),
        "records": len(manifest),
        "sites": dict(sorted(Counter(item["site"] for item in manifest).items())),
        "statuses": dict(sorted(Counter(item["capture_status"] for item in manifest).items())),
        "missing_or_changed": len(mismatched),
        "written": not check,
    }


def parse_input(value_: str) -> tuple[str, Path]:
    if "=" not in value_:
        raise argparse.ArgumentTypeError("输入格式必须是 site=/path/to/captures.jsonl")
    site, path = value_.split("=", 1)
    site = site.strip()
    if not site:
        raise argparse.ArgumentTypeError("site 不能为空")
    return site, Path(path).expanduser()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=parse_input, required=True, help="site=/path/to/captures.jsonl；可重复")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="仅检查预期 raw 捕获文件是否存在且未漂移")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output, args.input, args.check), ensure_ascii=False, sort_keys=True))
    except CaptureError as exc:
        print(f"错误：{exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
