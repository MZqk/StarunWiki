from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from .errors import IntegrityError, StarunWikiError
from .pack import repository_root


LEGACY_SENTINELS = (
    ".env",
    ".secrets/bootstrap.json",
    ".secrets/runtime.env",
    "release-state.json",
    ".runtime",
)
CANONICAL_SENTINELS = (
    "state-layout.json",
    "config.env",
    "secrets/bootstrap.json",
    "secrets/runtime.env",
    "release-state.json",
    "runtime",
)
MIGRATION_SCHEMA_VERSION = "starunwiki.state-layout/v1"
MIGRATION_FILE_MAPPINGS = (
    (Path(".env"), Path("config.env"), True),
    (Path(".secrets/bootstrap.json"), Path("secrets/bootstrap.json"), True),
    (Path(".secrets/runtime.env"), Path("secrets/runtime.env"), True),
    (Path("release-state.json"), Path("release-state.json"), True),
)


@dataclass(frozen=True)
class StateRoot:
    root: Path
    kind: str
    explicit: bool = False

    @property
    def config_env(self) -> Path:
        return self.root / (".env" if self.kind == "legacy" else "config.env")

    @property
    def secret_dir(self) -> Path:
        return self.root / (".secrets" if self.kind == "legacy" else "secrets")

    @property
    def bootstrap_state(self) -> Path:
        return self.secret_dir / "bootstrap.json"

    @property
    def runtime_env(self) -> Path:
        return self.secret_dir / "runtime.env"

    @property
    def release_state(self) -> Path:
        return self.root / "release-state.json"

    @property
    def lexical_root(self) -> Path:
        """Return an absolute path without resolving away a state-root symlink."""
        return Path(os.path.abspath(os.fspath(self.root.expanduser())))

    def require_writable_canonical(self) -> None:
        if self.kind == "legacy":
            raise StarunWikiError("写入型命令拒绝修改 legacy state；请先显式执行 ./manage.sh state migrate")
        repo = repository_root().resolve()
        root = self.lexical_root
        canonical = repo / ".runtime"
        if root.is_symlink():
            raise StarunWikiError(f"可写 state root 拒绝符号链接：{root}")
        if root != canonical and (root == repo or repo in root.parents):
            raise StarunWikiError("仓库内只允许 .runtime 承载可写状态；自定义 state root 必须位于仓库外")
        resolved = root.resolve()
        if resolved != canonical and (resolved == repo or repo in resolved.parents):
            raise StarunWikiError("可写 state root 经路径解析后位于仓库内，且不是 .runtime")
        if root.exists():
            _assert_private_directory(root, label="state root")

    def relative_path(self, path: Path) -> Path:
        candidate = Path(os.path.abspath(os.fspath(path.expanduser())))
        try:
            relative = candidate.relative_to(self.lexical_root)
        except ValueError as exc:
            raise StarunWikiError(f"state 路径越界：{path}") from exc
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise StarunWikiError(f"state 路径无效：{path}")
        return relative

    def secure_directory(self, relative: Path = Path(), *, create: bool = False) -> Path:
        """Validate/create a private directory below a writable state root."""
        self.require_writable_canonical()
        root = self.lexical_root
        if not root.exists():
            if not create:
                raise StarunWikiError(f"state root 不存在：{root}")
            root.mkdir(parents=True, mode=0o700)
            os.chmod(root, 0o700)
        _assert_private_directory(root, label="state root")

        parts = relative.parts
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in parts):
            raise StarunWikiError(f"state 目录不是安全相对路径：{relative}")
        current = root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise StarunWikiError(f"state 目录拒绝符号链接：{current}")
            if not current.exists():
                if not create:
                    raise StarunWikiError(f"state 目录不存在：{current}")
                current.mkdir(mode=0o700)
                os.chmod(current, 0o700)
            _assert_private_directory(current, label="state 目录")
        if current.resolve() != root.resolve() and root.resolve() not in current.resolve().parents:
            raise StarunWikiError(f"state 目录越界：{current}")
        return current

    def prepare_sensitive_file(self, path: Path) -> Path:
        relative = self.relative_path(path)
        self.secure_directory(relative.parent, create=True)
        target = self.lexical_root / relative
        if target.exists() or target.is_symlink():
            self.validate_sensitive_file(target)
        return target

    def validate_sensitive_file(self, path: Path, *, required: bool = True) -> Path:
        """Reject symlinks and non-0600 files before reading state secrets."""
        relative = self.relative_path(path)
        root = self.lexical_root
        if root.is_symlink() or not root.is_dir():
            raise StarunWikiError(f"state root 必须是普通目录：{root}")
        if self.kind != "legacy":
            _assert_private_directory(root, label="state root")
        current = root
        for part in relative.parent.parts:
            current = current / part
            if current.is_symlink() or not current.is_dir():
                raise StarunWikiError(f"敏感 state 父目录缺失或不安全：{current}")
            _assert_private_directory(current, label="敏感 state 父目录")
        target = root / relative
        if not target.exists() and not target.is_symlink():
            if required:
                raise StarunWikiError(f"敏感 state 文件不存在：{target}")
            return target
        if target.is_symlink() or not target.is_file():
            raise StarunWikiError(f"敏感 state 必须是普通文件：{target}")
        if stat.S_IMODE(target.stat().st_mode) != 0o600:
            raise StarunWikiError(f"敏感 state 权限必须为 0600：{target}")
        if target.resolve().parent != current.resolve():
            raise StarunWikiError(f"敏感 state 路径越界：{target}")
        return target


def _assert_private_directory(path: Path, *, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise StarunWikiError(f"{label} 必须是普通目录：{path}")
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise StarunWikiError(f"{label} 权限必须为 0700：{path}")


def _has_any(root: Path, sentinels: Iterable[str]) -> bool:
    return any((root / relative).exists() or (root / relative).is_symlink() for relative in sentinels)


def state_roots(repo: Path | None = None) -> tuple[Path, Path]:
    repo = (repo or repository_root()).resolve()
    return repo / ".runtime", repo / "integrations" / "llm-wiki-public"


def resolve_state_root(
    explicit: Path | None = None,
    *,
    repo: Path | None = None,
    environ: dict[str, str] | None = None,
) -> StateRoot:
    repo = (repo or repository_root()).resolve()
    environ = os.environ if environ is None else environ
    canonical, legacy = state_roots(repo)
    selected = explicit
    if selected is None and environ.get("STARUNWIKI_STATE_ROOT", "").strip():
        selected = Path(environ["STARUNWIKI_STATE_ROOT"].strip())
    if selected is not None:
        root = Path(os.path.abspath(os.fspath(selected.expanduser())))
        resolved = root.resolve()
        kind = "canonical" if resolved == canonical.resolve() else "legacy" if resolved == legacy.resolve() else "explicit"
        return StateRoot(root, kind, True)

    canonical_present = _has_any(canonical, CANONICAL_SENTINELS)
    legacy_present = _has_any(legacy, LEGACY_SENTINELS)
    if canonical_present and legacy_present:
        attested, detail = _migration_attestation(canonical, legacy)
        if attested:
            return StateRoot(canonical, "canonical")
        suffix = f"（{detail}）" if detail else ""
        raise StarunWikiError(
            "检测到新旧 state root 同时非空，且迁移证明不完整或已漂移；"
            f"为防止逐文件混用，请显式指定 --state-root 或先执行 state doctor{suffix}"
        )
    if canonical_present:
        return StateRoot(canonical, "canonical")
    if legacy_present:
        return StateRoot(legacy, "legacy")
    return StateRoot(canonical, "canonical")


def state_report(repo: Path | None = None) -> dict[str, object]:
    repo = (repo or repository_root()).resolve()
    canonical, legacy = state_roots(repo)
    canonical_present = _has_any(canonical, CANONICAL_SENTINELS)
    legacy_present = _has_any(legacy, LEGACY_SENTINELS)
    preserved_legacy = canonical_present and legacy_present
    attested = False
    attestation_detail: str | None = None
    if preserved_legacy:
        attested, attestation_detail = _migration_attestation(canonical, legacy)
    result: dict[str, object] = {
        "canonical_present": canonical_present,
        "legacy_present": legacy_present,
        "preserved_legacy": preserved_legacy,
        "attested": attested,
        "conflict": preserved_legacy and not attested,
        "canonical_root": str(canonical),
        "legacy_root": str(legacy),
    }
    if attestation_detail:
        result["attestation_detail"] = attestation_detail
    if not result["conflict"]:
        selected = resolve_state_root(repo=repo)
        result.update({"selected_root": str(selected.root), "selected_kind": selected.kind})
    return result


def _copy_regular_relative_source(
    root: Path,
    relative: Path,
    destination: Path,
    *,
    secret: bool,
) -> str:
    """Copy and hash one source inode without following any path symlink.

    Directory descriptors pin every parent and the same source descriptor is
    used for type/permission checks, copying, and hashing.  This closes the
    lstat-then-copy race during an explicitly requested state migration.
    """
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC
    opened_directories: list[int] = []
    source_fd: int | None = None
    destination_fd: int | None = None
    try:
        current_fd = os.open(root, directory_flags)
        opened_directories.append(current_fd)
        for part in relative.parts[:-1]:
            current_fd = os.open(part, directory_flags, dir_fd=current_fd)
            opened_directories.append(current_fd)
        source_fd = os.open(relative.name, file_flags, dir_fd=current_fd)
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise StarunWikiError(f"state migrate 只接受普通文件：{root / relative}")
        if secret and stat.S_IMODE(source_stat.st_mode) & 0o077:
            raise StarunWikiError(f"敏感 state 权限过宽，必须先收紧为 0600：{root / relative}")

        destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            destination_flags |= os.O_CLOEXEC
        destination_fd = os.open(destination, destination_flags, 0o600)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise StarunWikiError(f"state migrate 写入中断：{destination}")
                view = view[written:]
        os.fsync(destination_fd)
        os.fchmod(destination_fd, 0o600 if secret else stat.S_IMODE(source_stat.st_mode))
        return digest.hexdigest()
    except OSError as exc:
        raise StarunWikiError(f"state migrate 安全复制失败：{root / relative}：{exc}") from exc
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        if source_fd is not None:
            os.close(source_fd)
        for directory_fd in reversed(opened_directories):
            os.close(directory_fd)


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise StarunWikiError(f"state migrate 拒绝符号链接：{path}")
        if path.is_file():
            files.append(path)
    return files


def _safe_marker_path(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise IntegrityError(f"state-layout.json {field} 不是安全相对路径")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise IntegrityError(f"state-layout.json {field} 不是安全相对路径")
    return Path(*relative.parts)


def _regular_relative_file(root: Path, relative: Path, *, label: str) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise IntegrityError(f"{label} state root 必须是普通目录")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise IntegrityError(f"{label} 迁移路径拒绝符号链接：{relative.as_posix()}")
    if not current.is_file():
        raise IntegrityError(f"{label} 迁移文件缺失或不是普通文件：{relative.as_posix()}")
    return current


def _migration_attestation(canonical: Path, legacy: Path) -> tuple[bool, str | None]:
    try:
        layout_path = _regular_relative_file(canonical, Path("state-layout.json"), label="canonical")
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        if not isinstance(layout, dict) or set(layout) != {
            "schema_version",
            "migrated_from",
            "legacy_preserved",
            "files",
        }:
            raise IntegrityError("state-layout.json schema 不完整或包含未知字段")
        if layout.get("schema_version") != MIGRATION_SCHEMA_VERSION:
            raise IntegrityError("state-layout.json schema_version 无效")
        if layout.get("migrated_from") != "integrations/llm-wiki-public" or layout.get("legacy_preserved") is not True:
            raise IntegrityError("state-layout.json 迁移来源或保留声明无效")
        entries = layout.get("files")
        if not isinstance(entries, list):
            raise IntegrityError("state-layout.json files 必须是列表")

        recorded: dict[str, dict[str, object]] = {}
        recorded_canonical: set[str] = set()
        static_pairs = {
            (legacy_relative.as_posix(), canonical_relative.as_posix())
            for legacy_relative, canonical_relative, _secret in MIGRATION_FILE_MAPPINGS
        }
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "legacy",
                "canonical",
                "legacy_sha256",
                "canonical_sha256",
            }:
                raise IntegrityError("state-layout.json 文件证明不完整")
            legacy_path = _safe_marker_path(entry.get("legacy"), field="legacy")
            canonical_path = _safe_marker_path(entry.get("canonical"), field="canonical")
            legacy_relative = legacy_path.as_posix()
            canonical_relative = canonical_path.as_posix()
            legacy_sha = entry.get("legacy_sha256")
            canonical_sha = entry.get("canonical_sha256")
            if not all(
                isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)
                for value in (legacy_sha, canonical_sha)
            ):
                raise IntegrityError("state-layout.json 文件哈希证明不完整")
            if legacy_sha != canonical_sha:
                raise IntegrityError(f"state-layout.json 初始两侧哈希不一致：{legacy_relative}")

            pair = (legacy_relative, canonical_relative)
            is_runtime_pair = (
                len(legacy_path.parts) > 1
                and len(canonical_path.parts) > 1
                and legacy_path.parts[0] == ".runtime"
                and canonical_path.parts[0] == "runtime"
                and legacy_path.parts[1:] == canonical_path.parts[1:]
            )
            if pair not in static_pairs and not is_runtime_pair:
                raise IntegrityError(f"state-layout.json 包含未知迁移路径：{legacy_relative}")
            if legacy_relative in recorded or canonical_relative in recorded_canonical:
                raise IntegrityError("state-layout.json 包含重复文件证明")
            recorded[legacy_relative] = entry
            recorded_canonical.add(canonical_relative)

        observed_legacy: dict[str, Path] = {}
        if legacy.is_symlink() or not legacy.is_dir():
            raise IntegrityError("legacy state root 必须是普通目录")
        for legacy_relative, _canonical_relative, _secret in MIGRATION_FILE_MAPPINGS:
            source = legacy / legacy_relative
            if source.exists() or source.is_symlink():
                observed_legacy[legacy_relative.as_posix()] = _regular_relative_file(
                    legacy, legacy_relative, label="legacy"
                )

        legacy_runtime = legacy / ".runtime"
        if legacy_runtime.exists() or legacy_runtime.is_symlink():
            if legacy_runtime.is_symlink() or not legacy_runtime.is_dir():
                raise IntegrityError("legacy runtime 必须是普通目录")
            for path in _iter_files(legacy_runtime):
                relative = Path(".runtime") / path.relative_to(legacy_runtime)
                observed_legacy[relative.as_posix()] = path

        if set(recorded) != set(observed_legacy):
            raise IntegrityError("state-layout.json 文件证明集合与 legacy 不一致")
        for legacy_relative, legacy_path in observed_legacy.items():
            if _file_sha(legacy_path) != recorded[legacy_relative]["legacy_sha256"]:
                raise IntegrityError(f"legacy 迁移文件已漂移：{legacy_relative}")

        for legacy_relative, canonical_relative, _secret in MIGRATION_FILE_MAPPINGS:
            canonical_path = canonical / canonical_relative
            canonical_exists = canonical_path.exists() or canonical_path.is_symlink()
            was_migrated = legacy_relative.as_posix() in recorded
            if was_migrated or canonical_exists:
                _regular_relative_file(canonical, canonical_relative, label="canonical")

        canonical_runtime = canonical / "runtime"
        if canonical_runtime.exists() or canonical_runtime.is_symlink():
            if canonical_runtime.is_symlink() or not canonical_runtime.is_dir():
                raise IntegrityError("canonical runtime 必须是普通目录")
    except (StarunWikiError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        return False, str(exc)
    return True, None


def migrate_legacy_state(*, repo: Path | None = None) -> dict[str, object]:
    repo = (repo or repository_root()).resolve()
    canonical, legacy = state_roots(repo)
    if not _has_any(legacy, LEGACY_SENTINELS):
        raise StarunWikiError("没有可迁移的 legacy state")
    if canonical.exists():
        raise StarunWikiError("canonical .runtime 已存在；拒绝覆盖或合并")

    temporary = Path(tempfile.mkdtemp(prefix=".runtime.migrate-", dir=repo))
    copied: list[str] = []
    file_attestations: list[dict[str, str]] = []
    try:
        for legacy_relative, canonical_relative, secret in MIGRATION_FILE_MAPPINGS:
            source = legacy / legacy_relative
            if not source.exists() and not source.is_symlink():
                continue
            _regular_relative_file(legacy, legacy_relative, label="legacy")
            destination = temporary / canonical_relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            source_sha = _copy_regular_relative_source(
                legacy,
                legacy_relative,
                destination,
                secret=secret,
            )
            destination_sha = _file_sha(destination)
            if source_sha != destination_sha:
                raise IntegrityError(f"state migrate 哈希校验失败：{canonical_relative}")
            copied.append(canonical_relative.as_posix())
            file_attestations.append(
                {
                    "legacy": legacy_relative.as_posix(),
                    "canonical": canonical_relative.as_posix(),
                    "legacy_sha256": source_sha,
                    "canonical_sha256": destination_sha,
                }
            )

        legacy_runtime = legacy / ".runtime"
        if legacy_runtime.exists() or legacy_runtime.is_symlink():
            if legacy_runtime.is_symlink() or not legacy_runtime.is_dir():
                raise StarunWikiError("legacy .runtime 必须是普通目录")
            # Inspect the complete source tree before copying.  copytree follows
            # nested symlinks by default, so checking only the destination would
            # be too late and could copy data from outside the legacy state root.
            source_files = _iter_files(legacy_runtime)
            destination = temporary / "runtime"
            # Preserve any link introduced after the source scan so the
            # destination scan rejects it instead of following it.
            shutil.copytree(legacy_runtime, destination, symlinks=True)
            destination_files = _iter_files(destination)
            source_hashes = {path.relative_to(legacy_runtime): _file_sha(path) for path in source_files}
            destination_hashes = {path.relative_to(destination): _file_sha(path) for path in destination_files}
            if source_hashes != destination_hashes:
                raise IntegrityError("legacy runtime 目录复制后哈希不一致")
            copied.extend(f"runtime/{path.as_posix()}" for path in source_hashes)
            file_attestations.extend(
                {
                    "legacy": (Path(".runtime") / path).as_posix(),
                    "canonical": (Path("runtime") / path).as_posix(),
                    "legacy_sha256": source_hashes[path],
                    "canonical_sha256": destination_hashes[path],
                }
                for path in sorted(source_hashes)
            )

        layout = {
            "schema_version": MIGRATION_SCHEMA_VERSION,
            "migrated_from": "integrations/llm-wiki-public",
            "legacy_preserved": True,
            "files": sorted(file_attestations, key=lambda item: (item["legacy"], item["canonical"])),
        }
        (temporary / "state-layout.json").write_text(
            json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.chmod(temporary, 0o700)
        os.replace(temporary, canonical)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {"source": str(legacy), "destination": str(canonical), "copied": sorted(copied), "legacy_deleted": False}
