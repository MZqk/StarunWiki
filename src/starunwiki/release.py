from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .catalog import build_records, render
from .errors import IntegrityError, StarunWikiError
from .pack import KnowledgePack


LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+?\.md)(?:#[^)]*)?\)")
RELEASE_ID_RE = re.compile(r"^public-[a-f0-9]{12}$")
LOCAL_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:/.])/(?:Users|home|root|Volumes|tmp|opt|mnt|srv|etc|private|var|usr/local)/"
    r"|(?<![A-Za-z0-9])[A-Za-z]:[\\/]"
)
GIT_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"无法读取 JSON：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"JSON 顶层必须是对象：{path}")
    return value


def atomic_write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _authorized(row: dict[str, Any], pack: KnowledgePack) -> bool:
    return (
        row.get("access") == pack.publication["required_access"]
        and row.get("status") in set(pack.publication["allowed_status"])
        and not bool(row.get("content_withheld", False))
        and isinstance(row.get("text"), str)
    )


def slug_for(entry_name: str, explicit_slug: str = "") -> str:
    if explicit_slug:
        value = explicit_slug.strip().strip("/")
        if not value.startswith("concept/") or ".." in value.split("/"):
            raise IntegrityError(f"非法显式 slug：{explicit_slug}")
        return value
    value = entry_name.strip().replace("\\", "/")
    if not value.endswith(".md"):
        raise IntegrityError(f"非法 entry_name：{entry_name}")
    value = posixpath.normpath(value[:-len(".md")]).strip("/")
    if value.startswith("..") or not value:
        raise IntegrityError(f"非法 entry_name：{entry_name}")
    return f"concept/{value}"


def resolve_link(source_entry: str, raw_target: str) -> str:
    target = urllib.parse.unquote(raw_target).split("?", 1)[0]
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_entry), target))


def rewrite_links(text: str, source_entry: str, slug_map: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        label, raw_target = match.group(1), match.group(2)
        slug = slug_map.get(resolve_link(source_entry, raw_target))
        return f"[[{slug}|{label}]]" if slug else label

    return LINK_RE.sub(replace, text)


def build_pages(rows: list[dict[str, Any]], pack: KnowledgePack) -> list[dict[str, Any]]:
    selected = sorted((row for row in rows if _authorized(row, pack)), key=lambda item: str(item.get("entry_name") or ""))
    entries = [str(row.get("entry_name") or "") for row in selected]
    if len(entries) != len(set(entries)):
        raise IntegrityError("候选语料存在重复 entry_name")
    slug_map = {str(row["entry_name"]): slug_for(str(row["entry_name"]), str(row.get("slug") or "")) for row in selected}
    if len(slug_map.values()) != len(set(slug_map.values())):
        raise IntegrityError("候选语料存在重复 slug")
    pages: list[dict[str, Any]] = []
    for row in selected:
        entry = str(row["entry_name"])
        page = {
            "entry_name": entry,
            "slug": slug_map[entry],
            "title": str(row.get("display_name") or entry),
            "summary": str(row.get("description") or ""),
            "content": rewrite_links(str(row["text"]), entry, slug_map).strip() + "\n",
            "page_type": "concept",
            "status": "published",
            "source_status": str(row.get("status") or ""),
            "source_access": str(row.get("access") or ""),
            "source_review_state": str(row.get("review_state") or ""),
            "source_verified": bool(row.get("verified")),
            "source_verified_by": row.get("verified_by"),
            "source_verified_at": row.get("verified_at"),
            "source_verified_scope": row.get("verified_scope"),
            "tags": [str(item) for item in row.get("tags") or []],
            "stale_after": row.get("stale_after"),
        }
        page["payload_sha256"] = sha256_text(canonical_json(page))
        pages.append(page)
    return pages


def _profile_parts(pack: KnowledgePack) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    assistant = normalize_text(pack.resolve(pack.assistant_path, "profile.assistant", must_exist=True).read_text(encoding="utf-8"))
    ui = pack.public_profile()
    smoke = pack.smoke_profile()
    policy = {key: value for key, value in pack.publication.items() if key != "active_release"}
    return assistant, ui, smoke, policy


def build_full_release(pack: KnowledgePack) -> dict[str, str]:
    records = build_records(pack)
    corpus = render(records)
    corpus_sha = sha256_text(corpus)
    assistant, ui, smoke, policy = _profile_parts(pack)
    descriptor = {
        "corpus_sha256": corpus_sha,
        "assistant_sha256": sha256_text(assistant),
        "ui_sha256": sha256_text(canonical_json(ui)),
        "smoke_sha256": sha256_text(canonical_json(smoke)),
        "publication_policy_sha256": sha256_text(canonical_json(policy)),
    }
    bundle_sha = sha256_text(canonical_json(descriptor))
    release_id = f"public-{bundle_sha[:12]}"
    pages = build_pages(records, pack)
    counts = {
        "pages": len(pages),
        "draft": sum(page["source_status"] == "draft" for page in pages),
        "stable": sum(page["source_status"] == "stable" for page in pages),
        "unreviewed": sum(page["source_review_state"] == "needs-human-review" for page in pages),
    }
    public_profile = {key: ui[key] for key in ("brand", "title", "description", "suggestions")}
    manifest = {
        "schema_version": "starunwiki.public-manifest/v2",
        "pack_id": pack.pack_id,
        "release_id": release_id,
        "release_mode": "full",
        "bundle_sha256": bundle_sha,
        "locale": pack.locale,
        "corpus": {
            "logical_uri": f"pack://{pack.pack_id}/releases/{release_id}/corpus.jsonl",
            "sha256": corpus_sha,
            "available": True,
        },
        "counts": counts,
        "public_profile": public_profile,
        "pages": pages,
    }
    profile = {
        "schema_version": "starunwiki.release-profile/v1",
        "pack_id": pack.pack_id,
        "release_id": release_id,
        "locale": pack.locale,
        "digests": descriptor,
        "public_profile": public_profile,
        "smoke": smoke,
        "publication_policy": policy,
    }
    return {
        "release_id": release_id,
        "bundle_sha256": bundle_sha,
        "corpus.jsonl": corpus,
        "manifest.json": pretty_json(manifest),
        "profile.json": pretty_json(profile),
        "assistant.md": assistant,
        "smoke.json": pretty_json(smoke),
    }


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True)
    if result.returncode:
        raise StarunWikiError(result.stderr.strip() or f"git {' '.join(args)} 失败")
    return result.stdout.strip()


def _content_tree_sha(pack: KnowledgePack) -> str:
    repo = pack.repo_root
    relative_pack = pack.root.relative_to(repo).as_posix()
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", relative_pack],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    digest = hashlib.sha256()
    for raw_relative in sorted(item for item in result.stdout.split(b"\x00") if item):
        relative = raw_relative.decode("utf-8")
        path = repo / relative
        if "releases" in path.relative_to(pack.root).parts:
            continue
        logical = path.relative_to(pack.root).as_posix()
        digest.update(logical.encode("utf-8") + b"\x00")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _render_sums(directory: Path) -> str:
    lines = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.name != "SHA256SUMS":
            lines.append(f"{sha256_bytes(path.read_bytes())}  {path.name}")
    return "\n".join(lines) + "\n"


def approve_pack(
    pack: KnowledgePack,
    *,
    approved_by: str,
    note: str,
    allow_unreviewed: bool = False,
    allow_draft: bool = False,
) -> dict[str, Any]:
    if not approved_by.startswith(("operator:", "human:")) or not approved_by.split(":", 1)[1].strip():
        raise StarunWikiError("--approved-by 必须是 operator:<id> 或 human:<id>")
    if not note.strip():
        raise StarunWikiError("--note 不能为空")
    repo = pack.repo_root
    if _git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise StarunWikiError("pack approve 要求整个 Git 工作区已提交且干净")
    relative_pack = pack.root.relative_to(repo).as_posix()
    _git(repo, "ls-files", "--error-unmatch", f"{relative_pack}/pack.yaml")

    bundle = build_full_release(pack)
    manifest = json.loads(bundle["manifest.json"])
    counts = manifest["counts"]
    if counts["unreviewed"] and not allow_unreviewed:
        raise StarunWikiError("候选包含未审核页面；必须显式传入 --allow-unreviewed")
    if counts["draft"] and not allow_draft:
        raise StarunWikiError("候选包含草稿页面；必须显式传入 --allow-draft")
    release_id = bundle["release_id"]
    destination = pack.releases_root / release_id
    if destination.exists():
        raise IntegrityError(f"release 已存在，拒绝覆盖：{release_id}")

    staging = Path(tempfile.mkdtemp(prefix=f".{release_id}.", dir=pack.releases_root))
    try:
        for filename in ("corpus.jsonl", "manifest.json", "profile.json", "assistant.md", "smoke.json"):
            (staging / filename).write_text(bundle[filename], encoding="utf-8", newline="\n")
        manifest_sha = sha256_bytes((staging / "manifest.json").read_bytes())
        profile_sha = sha256_bytes((staging / "profile.json").read_bytes())
        authorization = {
            "schema_version": "starunwiki.authorization/v2",
            "pack_id": pack.pack_id,
            "release_id": release_id,
            "mode": "full",
            "approved_by": approved_by,
            "approved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "approval_note": note.strip(),
            "source": {
                "git_commit": _git(repo, "rev-parse", "HEAD"),
                "pack_path": relative_pack,
                "content_tree_sha256": _content_tree_sha(pack),
            },
            "corpus": {"path": "corpus.jsonl", "sha256": manifest["corpus"]["sha256"], "available": True, "rebuildable": True},
            "manifest": {"path": "manifest.json", "sha256": manifest_sha},
            "profile": {"path": "profile.json", "sha256": profile_sha},
            "bundle_sha256": bundle["bundle_sha256"],
            "counts": counts,
            "exceptions": {"allow_draft": allow_draft, "allow_unreviewed": allow_unreviewed},
            "future_changes_automatically_authorized": False,
        }
        (staging / "authorization.json").write_text(pretty_json(authorization), encoding="utf-8", newline="\n")
        (staging / "SHA256SUMS").write_text(_render_sums(staging), encoding="utf-8", newline="\n")
        verify_release_directory(pack, staging, expected_release_id=release_id)
        os.replace(staging, destination)
        active = {
            "schema_version": "starunwiki.active-release/v1",
            "pack_id": pack.pack_id,
            "release_id": release_id,
        }
        atomic_write(pack.active_pointer_path, pretty_json(active))
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"pack_id": pack.pack_id, "release_id": release_id, "counts": counts, "bundle_sha256": bundle["bundle_sha256"]}


def active_release_id(pack: KnowledgePack) -> str:
    value = load_json(pack.active_pointer_path)
    if value.get("schema_version") != "starunwiki.active-release/v1" or value.get("pack_id") != pack.pack_id:
        raise IntegrityError("active release 指针无效")
    release_id = str(value.get("release_id") or "")
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise IntegrityError(f"active release ID 非法：{release_id}")
    return release_id


def resolve_release_directory(pack: KnowledgePack, release: str = "current") -> Path:
    release_id = active_release_id(pack) if release == "current" else release
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise StarunWikiError(f"非法 release ID：{release_id}")
    path = pack.releases_root / release_id
    if not path.is_dir():
        raise IntegrityError(f"release 不存在：{release_id}")
    return path


def verify_publishable_git_snapshot(pack: KnowledgePack, directory: Path) -> dict[str, Any]:
    """Verify that a full release is the clean, committed active Git snapshot."""
    directory = directory.resolve()
    expected_directory = (pack.releases_root / active_release_id(pack)).resolve()
    if directory != expected_directory:
        raise IntegrityError("publish 仅允许当前 active release")

    verified = verify_release_directory(pack, directory, expected_release_id=directory.name)
    if verified["mode"] != "full":
        raise IntegrityError("publish 仅允许 full release")

    repo = pack.repo_root
    if _git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise StarunWikiError("publish 要求整个 Git 工作区已提交且干净")

    tracked_paths = [pack.active_pointer_path, *(path for path in directory.rglob("*") if path.is_file())]
    for path in tracked_paths:
        try:
            relative = path.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError as exc:
            raise IntegrityError(f"release 文件逃逸 Git 仓库：{path}") from exc
        try:
            _git(repo, "ls-files", "--error-unmatch", "--", relative)
        except StarunWikiError as exc:
            raise IntegrityError(f"publish 要求 release 文件已跟踪并提交：{relative}") from exc

    authorization = load_json(directory / "authorization.json")
    source = authorization["source"]
    source_commit = source["git_commit"]
    try:
        _git(repo, "cat-file", "-e", f"{source_commit}^{{commit}}")
        _git(repo, "merge-base", "--is-ancestor", source_commit, "HEAD")
    except StarunWikiError as exc:
        raise IntegrityError("authorization source.git_commit 不存在或不是 HEAD 祖先") from exc
    if source["content_tree_sha256"] != _content_tree_sha(pack):
        raise IntegrityError("authorization source.content_tree_sha256 与当前 pack 不一致")
    return verified


def list_releases(pack: KnowledgePack) -> list[dict[str, Any]]:
    active = active_release_id(pack)
    result = []
    for path in sorted(pack.releases_root.glob("public-*")):
        if not path.is_dir() or not RELEASE_ID_RE.fullmatch(path.name):
            continue
        authorization = load_json(path / "authorization.json")
        result.append({"release_id": path.name, "mode": authorization.get("mode"), "active": path.name == active})
    return result


def _verify_page_hashes(pages: list[Any]) -> None:
    seen: set[str] = set()
    for index, value in enumerate(pages):
        if not isinstance(value, dict):
            raise IntegrityError(f"manifest pages[{index}] 必须是对象")
        page = dict(value)
        recorded = str(page.pop("payload_sha256", ""))
        slug = str(page.get("slug") or "")
        if not recorded or sha256_text(canonical_json(page)) != recorded:
            raise IntegrityError(f"页面 payload SHA 不一致：{slug or index}")
        if not slug or slug in seen:
            raise IntegrityError(f"页面 slug 缺失或重复：{slug}")
        seen.add(slug)


def _verify_sums(directory: Path) -> None:
    sums = directory / "SHA256SUMS"
    if not sums.is_file():
        raise IntegrityError("release 缺少 SHA256SUMS")
    expected: dict[str, str] = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9._-]+)", line)
        if not match or match.group(2) == "SHA256SUMS":
            raise IntegrityError(f"SHA256SUMS 行非法：{line}")
        expected[match.group(2)] = match.group(1)
    actual = {path.name: sha256_bytes(path.read_bytes()) for path in directory.iterdir() if path.is_file() and path.name != "SHA256SUMS"}
    if expected != actual:
        raise IntegrityError("release 文件集合或 SHA256SUMS 不一致")


def _assert_no_absolute_path_values(value: Any, location: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _assert_no_absolute_path_values(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_absolute_path_values(child, f"{location}[{index}]")
    elif isinstance(value, str):
        candidate = value.strip()
        if "\n" not in candidate and (
            PurePosixPath(candidate).is_absolute()
            or PureWindowsPath(candidate).is_absolute()
            or candidate.lower().startswith("file://")
        ):
            raise IntegrityError(f"release 禁止绝对路径值：{location}")


def verify_release_directory(
    pack: KnowledgePack,
    directory: Path,
    *,
    expected_release_id: str | None = None,
) -> dict[str, Any]:
    _verify_sums(directory)
    for release_file in directory.iterdir():
        if release_file.is_file() and LOCAL_ABSOLUTE_PATH_RE.search(
            release_file.read_text(encoding="utf-8", errors="ignore")
        ):
            raise IntegrityError(f"release 禁止本机绝对用户路径：{release_file.name}")
    manifest = load_json(directory / "manifest.json")
    authorization = load_json(directory / "authorization.json")
    profile = load_json(directory / "profile.json")
    smoke = load_json(directory / "smoke.json")
    for filename, value in (
        ("manifest.json", manifest),
        ("authorization.json", authorization),
        ("profile.json", profile),
        ("smoke.json", smoke),
    ):
        _assert_no_absolute_path_values(value, filename)
    release_id = expected_release_id or directory.name
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise IntegrityError(f"非法 release ID：{release_id}")
    if manifest.get("schema_version") != "starunwiki.public-manifest/v2":
        raise IntegrityError("manifest schema_version 必须是 starunwiki.public-manifest/v2")
    if authorization.get("schema_version") != "starunwiki.authorization/v2":
        raise IntegrityError("authorization schema_version 必须是 starunwiki.authorization/v2")
    if profile.get("schema_version") != "starunwiki.release-profile/v1":
        raise IntegrityError("profile schema_version 必须是 starunwiki.release-profile/v1")
    if smoke.get("schema_version") != "starunwiki.smoke-profile/v1":
        raise IntegrityError("smoke schema_version 必须是 starunwiki.smoke-profile/v1")
    if manifest.get("pack_id") != pack.pack_id or authorization.get("pack_id") != pack.pack_id:
        raise IntegrityError("release pack_id 不一致")
    if manifest.get("release_id") != release_id or authorization.get("release_id") != release_id:
        raise IntegrityError("release_id 与目录名不一致")
    if profile.get("pack_id") != pack.pack_id or profile.get("release_id") != release_id:
        raise IntegrityError("release profile identity 不一致")
    if "corpus_path" in manifest:
        raise IntegrityError("manifest 禁止绝对本机路径")
    pages = manifest.get("pages")
    counts = manifest.get("counts")
    if not isinstance(pages, list) or not isinstance(counts, dict) or counts.get("pages") != len(pages):
        raise IntegrityError("manifest 页面计数无效")
    _verify_page_hashes(pages)
    actual_counts = {
        "pages": len(pages),
        "draft": sum(page.get("source_status") == "draft" for page in pages),
        "stable": sum(page.get("source_status") == "stable" for page in pages),
        "unreviewed": sum(page.get("source_review_state") == "needs-human-review" for page in pages),
    }
    if counts != actual_counts or authorization.get("counts") != counts:
        raise IntegrityError("manifest/authorization 审核计数不一致")
    mode = str(authorization.get("mode") or "")
    if manifest.get("release_mode") != mode:
        raise IntegrityError("manifest 与 authorization release mode 不一致")
    expected_files = {
        "manifest.json",
        "authorization.json",
        "profile.json",
        "assistant.md",
        "smoke.json",
        "SHA256SUMS",
    }
    if mode == "full":
        expected_files.add("corpus.jsonl")
    release_entries = list(directory.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in release_entries) or {
        path.name for path in release_entries
    } != expected_files:
        raise IntegrityError("release 文件集合必须与 release mode 严格一致")
    if authorization.get("future_changes_automatically_authorized") is not False:
        raise IntegrityError("authorization 必须明确拒绝自动授权未来变化")
    digests = profile.get("digests")
    if not isinstance(digests, dict) or sha256_text(canonical_json(digests)) != manifest.get("bundle_sha256"):
        raise IntegrityError("release bundle digest 不一致")
    if authorization.get("bundle_sha256") != manifest.get("bundle_sha256"):
        raise IntegrityError("authorization bundle digest 不一致")
    if profile.get("public_profile") != manifest.get("public_profile"):
        raise IntegrityError("release profile 与 manifest 公开 UI 不一致")
    assistant = normalize_text((directory / "assistant.md").read_text(encoding="utf-8"))
    if digests.get("assistant_sha256") != sha256_text(assistant):
        raise IntegrityError("release assistant digest 不一致")
    ui_for_digest = {"schema_version": "starunwiki.ui-profile/v1", **manifest.get("public_profile", {})}
    if digests.get("ui_sha256") != sha256_text(canonical_json(ui_for_digest)):
        raise IntegrityError("release UI profile digest 不一致")
    if digests.get("smoke_sha256") != sha256_text(canonical_json(smoke)):
        raise IntegrityError("release smoke digest 不一致")
    if profile.get("smoke") != smoke:
        raise IntegrityError("release profile 与 smoke.json 不一致")
    corpus = manifest.get("corpus")
    auth_corpus = authorization.get("corpus")
    if not isinstance(corpus, dict) or not isinstance(auth_corpus, dict):
        raise IntegrityError("release corpus 合同缺失")
    manifest_contract = authorization.get("manifest")
    profile_contract = authorization.get("profile")
    if not isinstance(manifest_contract, dict) or manifest_contract.get("path") != "manifest.json":
        raise IntegrityError("authorization manifest path 无效")
    if not isinstance(profile_contract, dict) or profile_contract.get("path") != "profile.json":
        raise IntegrityError("authorization profile path 无效")
    if mode == "full":
        if release_id != f"public-{str(manifest.get('bundle_sha256') or '')[:12]}":
            raise IntegrityError("full release ID 必须由 bundle digest 派生")
        corpus_path = directory / "corpus.jsonl"
        if (
            not corpus_path.is_file()
            or not corpus.get("available")
            or not auth_corpus.get("rebuildable")
            or auth_corpus.get("path") != "corpus.jsonl"
        ):
            raise IntegrityError("full release 必须包含可重建 corpus")
        digest = sha256_bytes(corpus_path.read_bytes())
        if digest != corpus.get("sha256") or digest != auth_corpus.get("sha256"):
            raise IntegrityError("full release corpus SHA 不一致")
        if digests.get("corpus_sha256") != digest:
            raise IntegrityError("full release bundle 未绑定实际 corpus SHA")
        if corpus.get("logical_uri") != f"pack://{pack.pack_id}/releases/{release_id}/corpus.jsonl":
            raise IntegrityError("full release corpus logical_uri 无效")
        publication_policy = profile.get("publication_policy")
        if not isinstance(publication_policy, dict) or digests.get("publication_policy_sha256") != sha256_text(
            canonical_json(publication_policy)
        ):
            raise IntegrityError("full release bundle 未绑定 publication policy")
        approved_by = str(authorization.get("approved_by") or "")
        if not approved_by.startswith(("operator:", "human:")) or not str(authorization.get("approval_note") or "").strip():
            raise IntegrityError("full release 缺少明确批准人或批准说明")
        approved_at = authorization.get("approved_at")
        try:
            approved_time = datetime.fromisoformat(str(approved_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise IntegrityError("full release approved_at 必须是带时区的 ISO 8601 时间") from exc
        if approved_time.tzinfo is None:
            raise IntegrityError("full release approved_at 必须是带时区的 ISO 8601 时间")

        source = authorization.get("source")
        expected_pack_path = pack.root.relative_to(pack.repo_root).as_posix()
        if not isinstance(source, dict):
            raise IntegrityError("full release 缺少 source 合同")
        if not GIT_COMMIT_RE.fullmatch(str(source.get("git_commit") or "")):
            raise IntegrityError("full release source.git_commit 必须是 40 位小写十六进制")
        if source.get("pack_path") != expected_pack_path:
            raise IntegrityError("full release source.pack_path 必须是当前 pack 的仓库相对路径")
        if not SHA256_RE.fullmatch(str(source.get("content_tree_sha256") or "")):
            raise IntegrityError("full release source.content_tree_sha256 无效")

        exceptions = authorization.get("exceptions")
        if not isinstance(exceptions, dict) or type(exceptions.get("allow_draft")) is not bool or type(
            exceptions.get("allow_unreviewed")
        ) is not bool:
            raise IntegrityError("full release exceptions 必须明确记录 allow_draft/allow_unreviewed")
        if counts["draft"] and not exceptions["allow_draft"]:
            raise IntegrityError("full release 包含草稿但未记录 allow_draft 例外")
        if counts["unreviewed"] and not exceptions["allow_unreviewed"]:
            raise IntegrityError("full release 包含未审核页面但未记录 allow_unreviewed 例外")
    elif mode == "legacy-manifest-only":
        if (
            (directory / "corpus.jsonl").exists()
            or corpus.get("available")
            or auth_corpus.get("available")
            or auth_corpus.get("rebuildable")
            or auth_corpus.get("path") is not None
        ):
            raise IntegrityError("legacy manifest-only release 不得包含或声明可用 corpus")
        if corpus.get("logical_uri") is not None or manifest.get("corpus_verified") is not False:
            raise IntegrityError("legacy release 必须明确 corpus_verified=false")
        if digests.get("corpus_sha256") != corpus.get("sha256"):
            raise IntegrityError("legacy release bundle 未绑定历史 corpus SHA")
        if digests.get("original_manifest_sha256") != manifest.get("source_manifest_sha256"):
            raise IntegrityError("legacy release bundle 未绑定原 manifest SHA")
    else:
        raise IntegrityError(f"未知 release mode：{mode}")
    manifest_digest = sha256_bytes((directory / "manifest.json").read_bytes())
    if manifest_contract.get("sha256") != manifest_digest:
        raise IntegrityError("authorization manifest SHA 不一致")
    if profile_contract.get("sha256") != sha256_bytes((directory / "profile.json").read_bytes()):
        raise IntegrityError("authorization profile SHA 不一致")
    return {"pack_id": pack.pack_id, "release_id": release_id, "mode": mode, "counts": counts, "corpus_verified": mode == "full"}


def write_legacy_release(
    pack: KnowledgePack,
    *,
    source_manifest: Path,
    source_authorization: dict[str, Any],
    legacy_assistant: str,
) -> dict[str, Any]:
    raw_manifest = source_manifest.read_bytes()
    old = json.loads(raw_manifest)
    release_id = str(old.get("release_id") or "")
    if release_id != "public-de219d707e39" or source_authorization.get("corpus_sha256") != old.get("corpus_sha256"):
        raise IntegrityError("legacy manifest/authorization 不是锁定的 de219 release")
    pages = old.get("pages")
    if not isinstance(pages, list):
        raise IntegrityError("legacy manifest pages 缺失")
    _verify_page_hashes(pages)
    ui = pack.public_profile()
    smoke = pack.smoke_profile()
    assistant = normalize_text(legacy_assistant)
    original_manifest_sha = sha256_bytes(raw_manifest)
    legacy_descriptor = {
        "corpus_sha256": old["corpus_sha256"],
        "original_manifest_sha256": original_manifest_sha,
        "assistant_sha256": sha256_text(assistant),
        "ui_sha256": sha256_text(canonical_json(ui)),
        "smoke_sha256": sha256_text(canonical_json(smoke)),
    }
    bundle_sha = sha256_text(canonical_json(legacy_descriptor))
    counts = {
        "pages": int(old["page_count"]),
        "draft": int(old["draft_count"]),
        "stable": int(old["stable_count"]),
        "unreviewed": int(old["unreviewed_count"]),
    }
    public_profile = {key: ui[key] for key in ("brand", "title", "description", "suggestions")}
    manifest = {
        "schema_version": "starunwiki.public-manifest/v2",
        "pack_id": pack.pack_id,
        "release_id": release_id,
        "release_mode": "legacy-manifest-only",
        "bundle_sha256": bundle_sha,
        "locale": pack.locale,
        "corpus": {"logical_uri": None, "sha256": old["corpus_sha256"], "available": False},
        "corpus_verified": False,
        "counts": counts,
        "public_profile": public_profile,
        "source_manifest_sha256": original_manifest_sha,
        "pages": pages,
    }
    profile = {
        "schema_version": "starunwiki.release-profile/v1",
        "pack_id": pack.pack_id,
        "release_id": release_id,
        "origin": "legacy-migration",
        "digests": legacy_descriptor,
        "public_profile": public_profile,
        "smoke": smoke,
    }
    destination = pack.releases_root / release_id
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "manifest.json").write_text(pretty_json(manifest), encoding="utf-8", newline="\n")
    (destination / "profile.json").write_text(pretty_json(profile), encoding="utf-8", newline="\n")
    (destination / "assistant.md").write_text(assistant, encoding="utf-8", newline="\n")
    (destination / "smoke.json").write_text(pretty_json(smoke), encoding="utf-8", newline="\n")
    manifest_sha = sha256_bytes((destination / "manifest.json").read_bytes())
    profile_sha = sha256_bytes((destination / "profile.json").read_bytes())
    authorization = {
        "schema_version": "starunwiki.authorization/v2",
        "pack_id": pack.pack_id,
        "release_id": release_id,
        "mode": "legacy-manifest-only",
        "approved_by": None,
        "approved_at": None,
        "authorization_origin": "migrated-fixed-corpus-contract",
        "original_authorization": source_authorization,
        "corpus": {"path": None, "sha256": old["corpus_sha256"], "available": False, "rebuildable": False},
        "manifest": {"path": "manifest.json", "sha256": manifest_sha, "original_sha256": original_manifest_sha},
        "profile": {"path": "profile.json", "sha256": profile_sha},
        "bundle_sha256": bundle_sha,
        "counts": counts,
        "limitations": ["cannot_rebuild_corpus", "cannot_create_new_publication", "cannot_claim_full_reproducibility"],
        "future_changes_automatically_authorized": False,
    }
    (destination / "authorization.json").write_text(pretty_json(authorization), encoding="utf-8", newline="\n")
    (destination / "SHA256SUMS").write_text(_render_sums(destination), encoding="utf-8", newline="\n")
    return verify_release_directory(pack, destination)
