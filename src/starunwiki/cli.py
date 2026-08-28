from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .catalog import corpus_summary, write_atomic
from .errors import ExternalServiceError, IntegrityError, StarunWikiError
from .pack import KnowledgePack, load_pack, repository_root
from .publisher import PublisherContext, bootstrap_check, bootstrap_init, check, plan, publish
from .release import (
    approve_pack,
    list_releases,
    load_json,
    resolve_release_directory,
    sha256_bytes,
    verify_release_directory,
)
from .state import StateRoot, migrate_legacy_state, resolve_state_root, state_report


WEKNORA_REPO_URL = "https://github.com/Tencent/WeKnora.git"
WEKNORA_REF = "v0.7.2"
WEKNORA_COMMIT = "3d5d8bfcdfeeea266b292b71cea616847af28d0f"
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _state_from_args(args: argparse.Namespace) -> StateRoot:
    explicit = Path(args.state_root) if getattr(args, "state_root", None) else None
    return resolve_state_root(explicit)


def _pack_from_args(args: argparse.Namespace) -> KnowledgePack:
    return load_pack(getattr(args, "pack", "deep-sky"))


def _context(args: argparse.Namespace) -> PublisherContext:
    pack = _pack_from_args(args)
    return PublisherContext(pack, resolve_release_directory(pack, args.release), _state_from_args(args))


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise StarunWikiError(f"环境文件不存在：{path}")
    values: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise StarunWikiError(f"环境文件第 {line_number} 行缺少 =：{path}")
        name, value = line.split("=", 1)
        if not ENV_NAME_RE.fullmatch(name):
            raise StarunWikiError(f"环境文件变量名非法：{name}")
        values[name] = value
    return values


def load_state_environment(state: StateRoot, *, include_runtime: bool) -> None:
    original = set(os.environ)
    config_env = state.validate_sensitive_file(state.config_env)
    for key, value in _read_env_file(config_env).items():
        if key not in original:
            os.environ[key] = value
    if include_runtime:
        runtime_env = state.validate_sensitive_file(state.runtime_env)
        for key, value in _read_env_file(runtime_env).items():
            if key not in original:
                os.environ[key] = value


def verify_compatibility_corpus(release_dir: Path, corpus_path: Path) -> dict[str, object]:
    """Read-only v0.1 `manifest --corpus` compatibility validation."""
    path = corpus_path.expanduser()
    if path.is_symlink() or not path.is_file():
        raise StarunWikiError(f"无法读取兼容语料（必须是普通文件）：{path}")
    try:
        raw = path.read_bytes()
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StarunWikiError(f"无法读取兼容语料：{path}：{exc}") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise StarunWikiError("兼容语料的每个 JSONL 记录必须是对象")
    authorization = load_json(release_dir / "authorization.json")
    corpus = authorization.get("corpus")
    expected = str(corpus.get("sha256") or "") if isinstance(corpus, dict) else ""
    actual = sha256_bytes(raw)
    if not expected or actual != expected:
        raise StarunWikiError(f"语料 SHA-256 漂移：want={expected or 'missing'} got={actual}")
    return {"provided_corpus_sha256": actual, "provided_corpus_records": len(rows), "provided_corpus_verified": True}


def validate_weknora_source(*, ensure: bool = False) -> Path:
    repo = repository_root()
    source = Path(os.environ.get("WEKNORA_SOURCE_DIR", str(repo / "services" / "WeKnora"))).expanduser().resolve()
    if not source.exists() and ensure:
        source.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["git", "clone", "--depth", "1", "--branch", WEKNORA_REF, "--single-branch", WEKNORA_REPO_URL, str(source)])
        if result.returncode:
            raise ExternalServiceError("无法克隆锁定的 WeKnora 源码")
    if not (source / "docker-compose.yml").is_file():
        raise StarunWikiError(f"WeKnora source 缺失或不完整：{source}")
    try:
        commit = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=normal"], check=True, text=True, capture_output=True).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise StarunWikiError(f"WeKnora source 不是有效 Git 工作树：{source}") from exc
    if commit != WEKNORA_COMMIT:
        raise StarunWikiError(f"WeKnora commit 不匹配：want={WEKNORA_COMMIT} got={commit}")
    if dirty:
        raise StarunWikiError(f"WeKnora worktree 有本地修改，拒绝自动覆盖：{source}")
    return source


def compose_auto_migrate(
    release_mode: str,
    config_values: dict[str, str],
    environ: dict[str, str] | None = None,
) -> str:
    if release_mode == "legacy-manifest-only":
        return "false"
    environ = os.environ if environ is None else environ
    value = (
        environ.get("STARUNWIKI_AUTO_MIGRATE")
        or config_values.get("STARUNWIKI_AUTO_MIGRATE")
        or config_values.get("AUTO_MIGRATE")
        or "false"
    ).strip().lower()
    if value not in {"true", "false"}:
        raise StarunWikiError("STARUNWIKI_AUTO_MIGRATE 必须是 true 或 false")
    return value


def _compose_command(state: StateRoot, pack: KnowledgePack, release: str, *arguments: str) -> None:
    docker = shutil.which("docker")
    if not docker:
        raise ExternalServiceError("未找到 docker；本机不能执行 Compose 验证或运行命令")
    weknora = validate_weknora_source()
    release_dir = resolve_release_directory(pack, release)
    release_contract = verify_release_directory(pack, release_dir)
    config_values = _read_env_file(state.validate_sensitive_file(state.config_env))
    if state.runtime_env.exists() or state.runtime_env.is_symlink():
        state.validate_sensitive_file(state.runtime_env)
    auto_migrate = compose_auto_migrate(release_contract["mode"], config_values)
    env = os.environ.copy()
    env.update({
        "STARUNWIKI_ROOT": str(repository_root()),
        "STARUNWIKI_CONFIG_ENV": str(state.config_env),
        "STARUNWIKI_RUNTIME_ENV": str(state.runtime_env),
        "STARUNWIKI_MANIFEST_PATH": str(release_dir / "manifest.json"),
        "STARUNWIKI_AUTO_MIGRATE": auto_migrate,
        "WEKNORA_SOURCE_DIR": str(weknora),
    })
    command = [
        # Keep upstream relative build contexts (notably docreader) anchored to
        # the locked WeKnora checkout. StarunWiki-owned paths are absolute in
        # deploy/compose.yml and therefore remain independent of the CWD.
        docker, "compose", "-p", "llm-wiki-public", "--project-directory", str(weknora),
        "--env-file", str(state.config_env), "-f", str(weknora / "docker-compose.yml"),
        "-f", str(repository_root() / "deploy" / "compose.yml"), *arguments,
    ]
    result = subprocess.run(command, env=env)
    if result.returncode:
        raise ExternalServiceError(f"docker compose 失败：exit={result.returncode}")


def command_pack(args: argparse.Namespace) -> None:
    pack = _pack_from_args(args)
    if args.pack_command == "validate":
        _, summary = corpus_summary(pack)
        print_json(summary)
    elif args.pack_command == "build":
        content, summary = corpus_summary(pack)
        output = Path(args.output) if args.output else repository_root() / ".knowledge-catalog" / "retrieval-corpus.jsonl"
        write_atomic(output, content)
        print_json({**summary, "output": str(output), "written": True})
    else:
        print_json(approve_pack(
            pack, approved_by=args.approved_by, note=args.note,
            allow_unreviewed=args.allow_unreviewed, allow_draft=args.allow_draft,
        ))


def command_release(args: argparse.Namespace) -> None:
    pack = _pack_from_args(args)
    if args.release_command == "list":
        print_json({"pack_id": pack.pack_id, "releases": list_releases(pack)})
        return
    release_dir = resolve_release_directory(pack, args.release)
    if args.release_command == "verify":
        verified = verify_release_directory(pack, release_dir)
        corpus = getattr(args, "corpus", None)
        if corpus:
            verified = {**verified, **verify_compatibility_corpus(release_dir, Path(corpus))}
        print_json(verified)
        return
    context = PublisherContext(pack, release_dir, _state_from_args(args))
    if args.release_command == "plan":
        print_json(plan(context))
    elif args.release_command == "publish":
        load_state_environment(context.state, include_runtime=False)
        print_json(publish(context))
    else:
        load_state_environment(context.state, include_runtime=True)
        print_json(check(context))


def command_bootstrap(args: argparse.Namespace) -> None:
    context = _context(args)
    load_state_environment(context.state, include_runtime=False)
    print_json(bootstrap_init(context) if args.bootstrap_command == "init" else bootstrap_check(context))


def command_runtime(args: argparse.Namespace) -> None:
    state = _state_from_args(args)
    pack = _pack_from_args(args)
    command = args.runtime_command
    if state.kind == "legacy" and command not in {"start", "status", "logs", "config"}:
        raise StarunWikiError(
            f"legacy state 仅允许 runtime start/status/logs/config；{command} 需要先显式执行 state migrate"
        )
    if command == "infra":
        _compose_command(state, pack, args.release, "up", "-d", "--build", "docreader", "app")
    elif command == "start":
        if not state.runtime_env.is_file():
            raise StarunWikiError("runtime.env 缺失；请先完成批准 release 的发布，或使用 legacy state")
        _compose_command(state, pack, args.release, "up", "-d", "--build")
    elif command == "stop":
        _compose_command(state, pack, args.release, "down")
    elif command == "status":
        _compose_command(state, pack, args.release, "ps")
    elif command == "logs":
        _compose_command(state, pack, args.release, "logs", "-f", "--tail=200", *(args.services or ["public-bff", "public-web"]))
    elif command == "config":
        _compose_command(state, pack, args.release, "config")
    else:
        _compose_command(state, pack, args.release, "restart", "app")


def command_state(args: argparse.Namespace) -> None:
    print_json(state_report() if args.state_command == "doctor" else migrate_legacy_state())


def command_init() -> None:
    state = resolve_state_root()
    if state.kind == "legacy":
        validate_weknora_source(ensure=True)
        print_json({"state_root": str(state.root), "state_kind": "legacy", "mutated": False, "message": "继续原地读取 legacy state；写入型命令需先 state migrate"})
        return
    state.require_writable_canonical()
    state.secure_directory(Path(), create=True)
    if state.config_env.exists() or state.config_env.is_symlink():
        state.validate_sensitive_file(state.config_env)
    else:
        target = state.prepare_sensitive_file(state.config_env)
        target.write_bytes((repository_root() / ".env.example").read_bytes())
        os.chmod(target, 0o600)
        state.validate_sensitive_file(target)
    state.secure_directory(state.relative_path(state.secret_dir), create=True)
    validate_weknora_source(ensure=True)
    print_json({"state_root": str(state.root), "state_kind": state.kind, "initialized": True})


def command_test() -> None:
    repo = repository_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src")
    commands = [
        ([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], repo),
        (["go", "test", "-race", "./..."], repo / "apps" / "bff"),
        (["npm", "test"], repo / "apps" / "web"),
        (["npm", "run", "build"], repo / "apps" / "web"),
    ]
    for command, cwd in commands:
        result = subprocess.run(command, cwd=cwd, env=env)
        if result.returncode:
            raise ExternalServiceError(f"测试命令失败：{' '.join(command)}")
    print_json({"python": "passed", "bff_race": "passed", "web_test": "passed", "web_build": "passed", "compose": "not-run-by-core-test"})


def add_pack_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pack", default="deep-sky")


def add_release_options(parser: argparse.ArgumentParser, *, state: bool = False) -> None:
    add_pack_option(parser)
    parser.add_argument("--release", default="current")
    if state:
        parser.add_argument("--state-root")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="starunwiki")
    parser.add_argument("--version", action="version", version=f"StarunWiki {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="v0.2 兼容：准备配置与锁定依赖，不生成凭据")
    pack_parser = commands.add_parser("pack")
    pack_commands = pack_parser.add_subparsers(dest="pack_command", required=True)
    for name in ("validate", "build"):
        child = pack_commands.add_parser(name)
        child.add_argument("pack", nargs="?", default="deep-sky")
        if name == "build": child.add_argument("--output")
    approve = pack_commands.add_parser("approve")
    approve.add_argument("pack", nargs="?", default="deep-sky")
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--note", required=True)
    approve.add_argument("--allow-unreviewed", action="store_true")
    approve.add_argument("--allow-draft", action="store_true")
    release_parser = commands.add_parser("release")
    release_commands = release_parser.add_subparsers(dest="release_command", required=True)
    child = release_commands.add_parser("list"); add_pack_option(child)
    for name in ("verify", "plan", "publish", "check"):
        child = release_commands.add_parser(name); add_release_options(child, state=name in {"plan", "publish", "check"})
        child.add_argument("--corpus", help=argparse.SUPPRESS)
    runtime_parser = commands.add_parser("runtime")
    runtime_commands = runtime_parser.add_subparsers(dest="runtime_command", required=True)
    for name in ("infra", "start", "stop", "status", "logs", "config", "reload-model"):
        child = runtime_commands.add_parser(name); add_release_options(child, state=True)
        if name == "logs": child.add_argument("services", nargs="*")
    bootstrap_parser = commands.add_parser("bootstrap")
    bootstrap_commands = bootstrap_parser.add_subparsers(dest="bootstrap_command", required=True)
    for name in ("init", "check"):
        child = bootstrap_commands.add_parser(name); add_release_options(child, state=True)
        child.add_argument("--corpus", help=argparse.SUPPRESS)
    state_parser = commands.add_parser("state")
    state_commands = state_parser.add_subparsers(dest="state_command", required=True)
    state_commands.add_parser("doctor"); state_commands.add_parser("migrate")
    commands.add_parser("test")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "pack": command_pack(args)
        elif args.command == "release": command_release(args)
        elif args.command == "runtime": command_runtime(args)
        elif args.command == "bootstrap": command_bootstrap(args)
        elif args.command == "state": command_state(args)
        elif args.command == "init": command_init()
        else: command_test()
    except IntegrityError as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 3
    except ExternalServiceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 4
    except StarunWikiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
