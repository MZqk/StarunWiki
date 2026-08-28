"""Publish and verify an approved StarunWiki release against WeKnora."""

from __future__ import annotations

import getpass
import json
import os
import re
import secrets
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ExternalServiceError, IntegrityError, StarunWikiError
from .pack import KnowledgePack
from .release import (
    atomic_write,
    load_json,
    sha256_bytes,
    verify_publishable_git_snapshot,
    verify_release_directory,
)
from .state import StateRoot


class PublisherError(StarunWikiError):
    pass


class PublisherExternalError(ExternalServiceError):
    pass


@dataclass(frozen=True)
class PublisherContext:
    pack: KnowledgePack
    release_dir: Path
    state: StateRoot

    @property
    def manifest_path(self) -> Path:
        return self.release_dir / "manifest.json"

    @property
    def release_assistant_path(self) -> Path:
        return self.release_dir / "assistant.md"

    @property
    def smoke_path(self) -> Path:
        return self.release_dir / "smoke.json"

    @property
    def core_policy_path(self) -> Path:
        return self.pack.repo_root / "config" / "core-system-policy.md"


def validate_context(context: PublisherContext) -> dict[str, Any]:
    return verify_release_directory(context.pack, context.release_dir)


def manifest_counts(manifest: dict[str, Any]) -> dict[str, int]:
    counts = manifest.get("counts")
    if not isinstance(counts, dict):
        raise IntegrityError("manifest counts 缺失")
    return {key: int(counts.get(key) or 0) for key in ("pages", "draft", "stable", "unreviewed")}


def composed_prompt(context: PublisherContext) -> str:
    core = context.core_policy_path.read_text(encoding="utf-8").strip()
    assistant = context.release_assistant_path.read_text(encoding="utf-8").strip()
    return f"{core}\n\n## 当前知识包\n\n{assistant}".strip()


@dataclass
class APIClient:
    base_url: str
    bearer: str
    timeout: float = 60.0

    def request(self, method: str, path: str, payload: Any | None = None) -> Any:
        data = None
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.bearer}"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url.rstrip("/") + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise PublisherExternalError(f"{method} {path} -> HTTP {exc.code}: {raw[:1000]}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PublisherExternalError(f"{method} {path} 失败：{exc}") from exc

    def multipart_input(self, path: str, value: str) -> Any:
        boundary = f"----starunwiki{secrets.token_hex(12)}"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"input\"\r\n\r\n"
            f"{value}\r\n--{boundary}--\r\n"
        ).encode("utf-8")
        request = urllib.request.Request(
            self.base_url.rstrip("/") + path,
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.bearer}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PublisherExternalError(f"POST {path} 失败：{exc}") from exc

    def stream_sse(self, path: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        request = urllib.request.Request(
            self.base_url.rstrip("/") + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self.bearer}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        events: list[dict[str, Any]] = []
        try:
            with urllib.request.urlopen(request, timeout=130) as response:
                data_lines: list[str] = []
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not line:
                        if data_lines:
                            try:
                                value = json.loads("\n".join(data_lines))
                                if isinstance(value, dict):
                                    events.append(value)
                            except json.JSONDecodeError:
                                pass
                            data_lines.clear()
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[len("data:"):].strip())
                if data_lines:
                    value = json.loads("\n".join(data_lines))
                    if isinstance(value, dict):
                        events.append(value)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise PublisherExternalError(f"POST {path} 失败：{exc}") from exc
        return events


def env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise PublisherError(f"缺少环境变量 {name}")
    return value


def owner_token() -> str:
    return os.environ.get("WEKNORA_OWNER_TOKEN", "").strip() or getpass.getpass("WeKnora Owner JWT: ").strip()


def admin_client() -> APIClient:
    token = owner_token()
    if not token:
        raise PublisherError("Owner JWT 不能为空")
    return APIClient(os.environ.get("WEKNORA_ADMIN_BASE_URL", "http://127.0.0.1:8080"), token)


def unwrap_data(value: Any) -> Any:
    return value.get("data") if isinstance(value, dict) and "data" in value else value


def created_api_key_token(value: dict[str, Any]) -> str:
    """Return only the one-time plaintext token, never the encrypted API key field."""
    return str(value.get("token") or "")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_sensitive_text(state: StateRoot, path: Path, text: str) -> None:
    target = state.prepare_sensitive_file(path)
    atomic_write(target, text, mode=0o600)
    state.validate_sensitive_file(target)


def _write_secret_json(state: StateRoot, path: Path, value: dict[str, Any]) -> None:
    _write_sensitive_text(state, path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _publication_operation_dir(context: PublisherContext, release_id: str) -> Path:
    return context.state.root / "operations" / f"publish-{context.pack.pack_id}-{release_id}"


def _write_operation(context: PublisherContext, operation_dir: Path, value: dict[str, Any]) -> None:
    relative = context.state.relative_path(operation_dir)
    context.state.secure_directory(relative, create=True)
    _write_secret_json(context.state, operation_dir / "operation.json", value)


def _state_file_present(state: StateRoot, path: Path) -> bool:
    present = path.exists() or path.is_symlink()
    if present:
        state.validate_sensitive_file(path)
    return present


def _preflight_publish_state(context: PublisherContext, operation_dir: Path) -> None:
    """Validate every local state path before the first remote side effect."""
    state = context.state
    state.secure_directory(Path(), create=True)
    state.validate_sensitive_file(state.bootstrap_state)
    state_exists = _state_file_present(state, state.release_state)
    runtime_exists = _state_file_present(state, state.runtime_env)
    if state_exists != runtime_exists:
        raise PublisherError("当前 release-state.json 与 runtime.env 不成对；拒绝发布")
    state.secure_directory(Path("operations"), create=True)
    state.secure_directory(Path("release-history") / context.pack.pack_id, create=True)
    if operation_dir.exists() or operation_dir.is_symlink():
        raise PublisherError(
            f"该 release 已有发布操作记录；拒绝自动重试远端副作用，请先审计：{operation_dir}"
        )


def _snapshot_current_runtime(context: PublisherContext) -> dict[str, Any] | None:
    state_exists = _state_file_present(context.state, context.state.release_state)
    runtime_exists = _state_file_present(context.state, context.state.runtime_env)
    if state_exists != runtime_exists:
        raise PublisherError("当前 release-state.json 与 runtime.env 不成对；拒绝覆盖不完整运行状态")
    if not state_exists:
        return None

    current = load_release_state(context.state.release_state, context.pack.pack_id)
    release_id = str(current.get("release_id") or "")
    if not re.fullmatch(r"public-[a-f0-9]{12}", release_id):
        raise PublisherError("当前 release state 缺少合法 release_id；拒绝覆盖")
    history_dir = context.state.root / "release-history" / context.pack.pack_id / release_id
    state_sha = sha256_bytes(context.state.release_state.read_bytes())
    runtime_sha = sha256_bytes(context.state.runtime_env.read_bytes())
    metadata = {
        "schema_version": "starunwiki.release-history/v1",
        "pack_id": context.pack.pack_id,
        "release_id": release_id,
        "release_state_sha256": state_sha,
        "runtime_env_sha256": runtime_sha,
        "captured_at": _utc_now(),
    }
    if history_dir.exists() or history_dir.is_symlink():
        context.state.secure_directory(context.state.relative_path(history_dir))
        history_state = context.state.validate_sensitive_file(history_dir / "release-state.json")
        history_runtime = context.state.validate_sensitive_file(history_dir / "runtime.env")
        history_metadata = context.state.validate_sensitive_file(history_dir / "snapshot.json")
        stored_metadata = load_json(history_metadata)
        if (
            sha256_bytes(history_state.read_bytes()) != state_sha
            or sha256_bytes(history_runtime.read_bytes()) != runtime_sha
            or stored_metadata.get("pack_id") != context.pack.pack_id
            or stored_metadata.get("release_id") != release_id
            or stored_metadata.get("release_state_sha256") != state_sha
            or stored_metadata.get("runtime_env_sha256") != runtime_sha
        ):
            raise PublisherError(f"既有回滚快照与当前状态不一致，拒绝覆盖：{history_dir}")
        return {"release_id": release_id, "path": str(history_dir)}

    history_parent = history_dir.parent
    context.state.secure_directory(context.state.relative_path(history_parent), create=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{release_id}.", dir=history_parent))
    try:
        os.chmod(staging, 0o700)
        _write_sensitive_text(
            context.state,
            staging / "release-state.json",
            context.state.release_state.read_text(encoding="utf-8"),
        )
        _write_sensitive_text(
            context.state,
            staging / "runtime.env",
            context.state.runtime_env.read_text(encoding="utf-8"),
        )
        _write_secret_json(context.state, staging / "snapshot.json", metadata)
        if (
            sha256_bytes((staging / "release-state.json").read_bytes()) != state_sha
            or sha256_bytes((staging / "runtime.env").read_bytes()) != runtime_sha
        ):
            raise PublisherError("回滚快照写入后哈希校验失败")
        os.replace(staging, history_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"release_id": release_id, "path": str(history_dir)}


def _restore_runtime_snapshot(context: PublisherContext, snapshot: dict[str, Any] | None) -> None:
    if snapshot is None:
        for path in (context.state.runtime_env, context.state.release_state):
            if path.exists() or path.is_symlink():
                context.state.validate_sensitive_file(path).unlink()
        return
    release_id = str(snapshot.get("release_id") or "")
    if not re.fullmatch(r"public-[a-f0-9]{12}", release_id):
        raise PublisherError("回滚快照缺少合法 release_id")
    history_dir = context.state.root / "release-history" / context.pack.pack_id / release_id
    if str(history_dir) != str(snapshot.get("path")):
        raise PublisherError("回滚快照路径与 release_id 不一致")
    context.state.secure_directory(context.state.relative_path(history_dir))
    runtime_source = context.state.validate_sensitive_file(history_dir / "runtime.env")
    state_source = context.state.validate_sensitive_file(history_dir / "release-state.json")
    _write_sensitive_text(
        context.state,
        context.state.runtime_env,
        runtime_source.read_text(encoding="utf-8"),
    )
    _write_sensitive_text(
        context.state,
        context.state.release_state,
        state_source.read_text(encoding="utf-8"),
    )


def _matching_model(client: APIClient) -> dict[str, Any]:
    model_name = env_required("LLM_MODEL_NAME")
    base_url = env_required("LLM_BASE_URL")
    provider = os.environ.get("LLM_PROVIDER", "openai").strip() or "openai"
    models = list(unwrap_data(client.request("GET", "/api/v1/models")) or [])
    if any(item.get("type") in {"Embedding", "Rerank"} for item in models):
        raise PublisherError("专用实例中存在 Embedding/Rerank 模型，拒绝继续")
    matches = [
        item for item in models
        if item.get("id") == "builtin-llm-wiki-chat"
        and item.get("type") == "KnowledgeQA"
        and item.get("name") == model_name
    ]
    if len(matches) != 1:
        raise PublisherError("未找到唯一 builtin-llm-wiki-chat；请先检查锁定模型配置")
    parameters = matches[0].get("parameters") or {}
    if parameters.get("base_url") != base_url or parameters.get("provider") != provider:
        raise PublisherError(f"builtin 模型参数与环境不一致：{parameters}")
    return matches[0]


def bootstrap_init(context: PublisherContext) -> dict[str, Any]:
    context.state.require_writable_canonical()
    if validate_context(context)["mode"] != "full":
        raise PublisherError("bootstrap init 仅允许已批准的 full release；M0 legacy release 禁止创建或轮换凭据")
    verify_publishable_git_snapshot(context.pack, context.release_dir)
    context.state.secure_directory(Path(), create=True)
    context.state.secure_directory(context.state.relative_path(context.state.secret_dir), create=True)
    if context.state.bootstrap_state.exists() or context.state.bootstrap_state.is_symlink():
        context.state.validate_sensitive_file(context.state.bootstrap_state)
    client = admin_client()
    tenant_id = int(env_required("WEKNORA_TENANT_ID"))
    model = _matching_model(client)
    model_id = str(model.get("id") or "")
    if not model_id:
        raise PublisherError("builtin 模型响应缺少 id")
    credentials = model.get("credentials")
    api_key_metadata = credentials.get("api_key") if isinstance(credentials, dict) else None
    if not isinstance(api_key_metadata, dict):
        raise PublisherError("无法证明 builtin 模型凭据为空；bootstrap init 要求 system admin 可见的凭据元数据")
    principal = unwrap_data(client.request("GET", f"/api/v1/tenants/{tenant_id}/api-principal-config"))
    if not isinstance(principal, dict):
        raise PublisherError("无法读取 Tenant principal 配置")
    resumed = context.state.bootstrap_state.exists()
    if resumed:
        state = load_json(context.state.bootstrap_state)
        if state.get("schema_version") != "starunwiki.bootstrap-state/v3":
            raise PublisherError("bootstrap state 已存在；拒绝覆盖旧状态或隐式轮换凭据")
        if state.get("status") == "ready":
            raise PublisherError("bootstrap 已完成；init 拒绝覆盖或旋转凭据")
        if state.get("status") not in {"prepared", "credentials-set", "model-verified"}:
            raise PublisherError("bootstrap pending state 状态非法；拒绝自动继续")
        if int(state.get("tenant_id") or 0) != tenant_id or state.get("model_id") != model_id:
            raise PublisherError("bootstrap pending state 与当前 tenant/model 不一致")
        steps = state.get("steps")
        if not isinstance(steps, dict) or any(type(steps.get(name)) is not bool for name in (
            "model_credentials", "model_debug", "principal"
        )):
            raise PublisherError("bootstrap pending state 缺少完整检查点")
    else:
        if api_key_metadata.get("configured") is not False:
            raise PublisherError("builtin 模型凭据已存在；bootstrap init 拒绝覆盖或轮换")
        if principal.get("mode") not in (None, "", "tenant") or bool(principal.get("has_hmac_secret")):
            raise PublisherError("Tenant principal 已配置；bootstrap init 拒绝覆盖或轮换 HMAC")
        api_key = getpass.getpass("模型 API Key（仅本次输入）: ").strip()
        if not api_key:
            raise PublisherError("模型 API Key 不能为空")
        state = {
            "schema_version": "starunwiki.bootstrap-state/v3",
            "status": "prepared",
            "tenant_id": tenant_id,
            "model_id": model_id,
            "external_hmac_secret": secrets.token_urlsafe(48),
            "steps": {"model_credentials": False, "model_debug": False, "principal": False},
            "created_at": _utc_now(),
        }
        _write_secret_json(context.state, context.state.bootstrap_state, state)

    steps = state["steps"]
    if not steps["model_credentials"]:
        if api_key_metadata.get("configured") is not False:
            raise PublisherError(
                "模型凭据远端已配置但本地检查点未确认；为避免覆盖未知凭据，拒绝自动重试"
            )
        if resumed:
            api_key = getpass.getpass("模型 API Key（继续同一次 bootstrap）: ").strip()
            if not api_key:
                raise PublisherError("模型 API Key 不能为空")
        client.request("PUT", f"/api/v1/models/{urllib.parse.quote(model_id)}/credentials", {"api_key": api_key})
        steps["model_credentials"] = True
        state["status"] = "credentials-set"
        _write_secret_json(context.state, context.state.bootstrap_state, state)
    elif api_key_metadata.get("configured") is not True:
        raise PublisherError("本地记录模型凭据已写入，但远端未配置；拒绝继续")

    if not steps["model_debug"]:
        debug = unwrap_data(client.multipart_input(f"/api/v1/models/{urllib.parse.quote(model_id)}/debug", "只回复 OK"))
        if not isinstance(debug, dict) or not debug.get("ok"):
            raise PublisherError(f"模型真实调用失败：{debug}")
        steps["model_debug"] = True
        state["status"] = "model-verified"
        _write_secret_json(context.state, context.state.bootstrap_state, state)

    if not steps["principal"]:
        if principal.get("mode") not in (None, "", "tenant") or bool(principal.get("has_hmac_secret")):
            raise PublisherError(
                "Tenant principal 远端已配置但本地检查点未确认；HMAC secret 已保留，拒绝自动覆盖"
            )
        client.request(
            "PUT",
            f"/api/v1/tenants/{tenant_id}/api-principal-config",
            {"mode": "signed_token", "hmac_secret": state["external_hmac_secret"]},
        )
        steps["principal"] = True
    elif principal.get("mode") != "signed_token" or principal.get("has_hmac_secret") is not True:
        raise PublisherError("本地记录 principal 已创建，但远端不是 signed_token HMAC 模式")

    state["status"] = "ready"
    state["completed_at"] = _utc_now()
    _write_secret_json(context.state, context.state.bootstrap_state, state)
    return {
        "tenant_id": tenant_id,
        "model_id": model_id,
        "created": True,
        "model_credentials_created": True,
        "credentials_rotated": False,
        "resumed": resumed,
    }


def bootstrap_check(context: PublisherContext, *, client: APIClient | None = None) -> dict[str, Any]:
    validate_context(context)
    state = load_json(context.state.validate_sensitive_file(context.state.bootstrap_state))
    if state.get("schema_version") == "starunwiki.bootstrap-state/v3" and (
        state.get("status") != "ready" or state.get("steps") != {
            "model_credentials": True,
            "model_debug": True,
            "principal": True,
        }
    ):
        raise PublisherError("bootstrap 操作尚未完整完成；拒绝将 pending state 当作可用凭据")
    tenant_id = int(state.get("tenant_id") or 0)
    model_id = str(state.get("model_id") or "")
    hmac_secret = str(state.get("external_hmac_secret") or "")
    if tenant_id <= 0 or not model_id or len(hmac_secret) < 32:
        raise PublisherError("bootstrap state 不完整")
    client = client or admin_client()
    model = _matching_model(client)
    if str(model.get("id") or "") != model_id:
        raise PublisherError("bootstrap model_id 与当前锁定模型不一致")
    principal = unwrap_data(client.request("GET", f"/api/v1/tenants/{tenant_id}/api-principal-config"))
    if (
        not isinstance(principal, dict)
        or principal.get("mode") != "signed_token"
        or principal.get("has_hmac_secret") is not True
    ):
        raise PublisherError("Tenant principal 不是已配置 HMAC 的 signed_token 模式")
    return {"tenant_id": tenant_id, "model_id": model_id, "checked": True, "mutated": False}


def page_payload(page: dict[str, Any], release_id: str, pack_id: str = "deep-sky") -> dict[str, Any]:
    return {
        "slug": page["slug"],
        "title": page["title"],
        "summary": page["summary"],
        "content": page["content"],
        "page_type": "concept",
        "status": "published",
        "aliases": [],
        "source_refs": [],
        "chunk_refs": [],
        "page_metadata": {
            "schema_version": "starunwiki.public-page/v2",
            "pack_id": pack_id,
            "source_entry": page["entry_name"],
            "source_status": page["source_status"],
            "source_access": page["source_access"],
            "source_review_state": page.get("source_review_state"),
            "source_verified": bool(page.get("source_verified")),
            "release_id": release_id,
            "payload_sha256": page["payload_sha256"],
            "stale_after": page.get("stale_after"),
            "tags": page.get("tags") or [],
        },
    }


def _render_runtime_env(values: dict[str, str]) -> str:
    lines: list[str] = []
    for key, value in values.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or any(char in value for char in "\r\n\x00"):
            raise PublisherError(f"运行状态包含非法环境变量值：{key}")
        lines.append(f"{key}={value}\n")
    return "".join(lines)


def publish(context: PublisherContext) -> dict[str, Any]:
    context.state.require_writable_canonical()
    verified = validate_context(context)
    if verified["mode"] != "full":
        raise PublisherError("legacy-manifest-only release 禁止 fresh publish")
    verify_publishable_git_snapshot(context.pack, context.release_dir)
    release_id = str(verified["release_id"])
    operation_dir = _publication_operation_dir(context, release_id)
    _preflight_publish_state(context, operation_dir)
    if context.state.release_state.exists() or context.state.release_state.is_symlink():
        existing = load_release_state(context.state.release_state, context.pack.pack_id)
        if existing.get("release_id") == release_id:
            raise PublisherError("该 release 已有本地发布状态；拒绝重复创建 KB/Agent/key")
    manifest = load_json(context.manifest_path)
    bootstrap_state = load_json(context.state.validate_sensitive_file(context.state.bootstrap_state))
    client = admin_client()
    bootstrap_check(context, client=client)
    model_id = str(bootstrap_state["model_id"])
    tenant_id = int(bootstrap_state["tenant_id"])
    counts = manifest_counts(manifest)
    previous = _snapshot_current_runtime(context)
    operation = {
        "schema_version": "starunwiki.publish-operation/v1",
        "pack_id": context.pack.pack_id,
        "release_id": release_id,
        "status": "started",
        "step": "create-knowledge-base",
        "started_at": _utc_now(),
        "previous_release": previous,
        "kb_id": None,
        "pages_created": 0,
        "last_page_slug": None,
        "agent_id": None,
        "api_key_received": False,
        "runtime_switched": False,
    }
    _write_operation(context, operation_dir, operation)

    try:
        kb = unwrap_data(client.request("POST", "/api/v1/knowledge-bases", {
            "name": f"starunwiki-{context.pack.pack_id}-{release_id}",
            "description": f"固定{counts['pages']}页匿名公开纯Wiki派生库",
            "type": "document",
            "embedding_model_id": "",
            "rerank_model_id": "",
            "summary_model_id": model_id,
            "indexing_strategy": {"vector_enabled": False, "keyword_enabled": False, "wiki_enabled": True, "graph_enabled": False},
            "wiki_config": {},
        }))
        kb_id = str(kb.get("id") or "")
        if not kb_id:
            raise PublisherError("知识库创建响应缺少 id")
        operation.update({"step": "create-pages", "kb_id": kb_id})
        _write_operation(context, operation_dir, operation)

        for index, page in enumerate(manifest["pages"], 1):
            client.request(
                "POST",
                f"/api/v1/knowledgebase/{urllib.parse.quote(kb_id)}/wiki/pages",
                page_payload(page, release_id, context.pack.pack_id),
            )
            operation.update({"pages_created": index, "last_page_slug": page["slug"]})
            _write_operation(context, operation_dir, operation)
            if index % 20 == 0 or index == len(manifest["pages"]):
                print(f"已写入 Wiki 页面 {index}/{len(manifest['pages'])}")

        operation["step"] = "create-agent"
        _write_operation(context, operation_dir, operation)
        agent = unwrap_data(client.request("POST", "/api/v1/agents", {
            "name": f"StarunWiki {context.pack.pack_id} {release_id}",
            "description": "匿名公开纯 Wiki 问答，只读",
            "avatar": "telescope",
            "config": {
                "agent_mode": "smart-reasoning", "agent_type": "wiki-qa", "system_prompt": composed_prompt(context),
                "model_id": model_id, "rerank_model_id": "", "temperature": 0.2, "max_iterations": 10,
                "thinking": False, "citation_enabled": True, "allowed_tools": ["wiki_search", "wiki_read_page"],
                "kb_selection_mode": "selected", "knowledge_bases": [kb_id], "retrieve_kb_only_when_mentioned": False,
                "retain_retrieval_history": False, "mcp_selection_mode": "none", "skills_selection_mode": "none",
                "web_search_enabled": False, "image_upload_enabled": False, "audio_upload_enabled": False,
                "multi_turn_enabled": True, "history_turns": 5,
            },
        }))
        agent_id = str(agent.get("id") or "")
        if not agent_id:
            raise PublisherError("Agent 创建响应缺少 id")
        operation.update({"step": "create-api-key", "agent_id": agent_id})
        _write_operation(context, operation_dir, operation)

        key_result = unwrap_data(client.request("POST", f"/api/v1/tenants/{tenant_id}/api-keys", {
            "name": f"starunwiki-chat-{release_id}", "full_access": False, "knowledge_base_ids": [kb_id],
            "capabilities": ["chat"], "expires_at_unix": int(time.time()) + 90 * 24 * 3600,
        }))
        chat_key = created_api_key_token(key_result)
        if not chat_key:
            raise PublisherError("chat-only API Key 创建响应没有返回明文 token")
        _write_secret_json(
            context.state,
            operation_dir / "one-time-key.json",
            {"token": chat_key, "received_at": _utc_now()},
        )

        runtime = {
            "WEKNORA_TENANT_ID": str(tenant_id), "WEKNORA_CHAT_API_KEY": chat_key,
            "WEKNORA_EXTERNAL_HMAC_SECRET": str(bootstrap_state["external_hmac_secret"]), "WEKNORA_AGENT_ID": agent_id,
            "WEKNORA_KB_ID": kb_id, "WEKNORA_MODEL_ID": model_id, "PUBLIC_RELEASE_ID": release_id,
            "STARUNWIKI_PACK_ID": context.pack.pack_id,
        }
        runtime_content = _render_runtime_env(runtime)
        release_state = {
            "schema_version": "starunwiki.release-state/v2", "pack_id": context.pack.pack_id,
            "release_id": release_id, "release_mode": "full", "kb_id": kb_id, "agent_id": agent_id,
            "model_id": model_id, "page_count": counts["pages"],
            "manifest_sha256": sha256_bytes(context.manifest_path.read_bytes()),
            "core_policy_sha256": sha256_bytes(context.core_policy_path.read_bytes()),
        }
        pending_runtime = operation_dir / "pending-runtime.env"
        pending_state = operation_dir / "pending-release-state.json"
        _write_sensitive_text(context.state, pending_runtime, runtime_content)
        _write_secret_json(context.state, pending_state, release_state)
        operation.update({"step": "switch-runtime", "api_key_received": True, "status": "remote-complete"})
        _write_operation(context, operation_dir, operation)

        try:
            _write_sensitive_text(
                context.state,
                context.state.runtime_env,
                pending_runtime.read_text(encoding="utf-8"),
            )
            _write_sensitive_text(
                context.state,
                context.state.release_state,
                pending_state.read_text(encoding="utf-8"),
            )
            operation.update({
                "status": "complete",
                "step": "complete",
                "runtime_switched": True,
                "completed_at": _utc_now(),
            })
            _write_operation(context, operation_dir, operation)
        except Exception:
            operation.update({"status": "rollback-required", "step": "restore-previous-runtime"})
            try:
                _write_operation(context, operation_dir, operation)
            except Exception:
                pass
            try:
                _restore_runtime_snapshot(context, previous)
                operation["rollback_restored"] = True
                operation["status"] = "failed-restored"
            except Exception:
                operation["rollback_restored"] = False
                operation["status"] = "rollback-failed"
            try:
                _write_operation(context, operation_dir, operation)
            except Exception:
                pass
            raise

        for ephemeral in (operation_dir / "one-time-key.json", pending_runtime, pending_state):
            try:
                if ephemeral.exists() or ephemeral.is_symlink():
                    context.state.validate_sensitive_file(ephemeral).unlink()
            except OSError:
                pass
        return {"release_id": release_id, "pages": counts["pages"], "kb_id": kb_id, "agent_id": agent_id}
    except Exception as exc:
        if operation.get("status") != "complete":
            operation.setdefault("failed_step", operation.get("step"))
            if operation.get("status") not in {"failed-restored", "rollback-failed"}:
                operation["status"] = "failed"
            operation["error_type"] = type(exc).__name__
            operation["failed_at"] = _utc_now()
            try:
                _write_operation(context, operation_dir, operation)
            except Exception:
                pass
        raise


def load_release_state(path: Path, pack_id: str) -> dict[str, Any]:
    state = load_json(path)
    if state.get("schema_version") == 1:
        state = {**state, "schema_version": "starunwiki.release-state/v1-normalized", "pack_id": pack_id, "release_mode": "legacy-manifest-only"}
    if state.get("pack_id") != pack_id:
        raise IntegrityError("release state pack_id 不一致")
    return state


def list_wiki_pages(client: APIClient, kb_id: str) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    page_number = 1
    while True:
        query = urllib.parse.urlencode({"page": page_number, "page_size": 50, "sort_by": "slug", "sort_order": "asc"})
        response = client.request("GET", f"/api/v1/knowledgebase/{urllib.parse.quote(kb_id)}/wiki/pages?{query}")
        batch = list(response.get("pages") or [])
        pages.extend(batch)
        if not batch or len(pages) >= int(response.get("total") or len(pages)):
            return pages
        page_number += 1


def check(context: PublisherContext) -> dict[str, Any]:
    verified = validate_context(context)
    manifest = load_json(context.manifest_path)
    state = load_release_state(
        context.state.validate_sensitive_file(context.state.release_state),
        context.pack.pack_id,
    )
    if state.get("release_id") != manifest.get("release_id"):
        raise IntegrityError("release state 与 manifest release_id 不一致")
    if state.get("schema_version") == "starunwiki.release-state/v2":
        current_core_digest = sha256_bytes(context.core_policy_path.read_bytes())
        if state.get("core_policy_sha256") != current_core_digest:
            raise IntegrityError("核心平台策略与部署状态 digest 不一致；拒绝把策略漂移视为已发布")
    client = admin_client()
    kb_id = str(state["kb_id"])
    agent_id = str(state["agent_id"])
    models = list(unwrap_data(client.request("GET", "/api/v1/models")) or [])
    if any(item.get("type") in {"Embedding", "Rerank"} for item in models):
        raise PublisherError("合同失败：存在 Embedding/Rerank 模型")
    kb = unwrap_data(client.request("GET", f"/api/v1/knowledge-bases/{urllib.parse.quote(kb_id)}"))
    strategy = kb.get("indexing_strategy") or {}
    expected_strategy = {"vector_enabled": False, "keyword_enabled": False, "wiki_enabled": True, "graph_enabled": False}
    if any(bool(strategy.get(key)) != value for key, value in expected_strategy.items()) or kb.get("embedding_model_id") or kb.get("rerank_model_id"):
        raise PublisherError(f"合同失败：KB 不是 Wiki-only：{strategy}")
    remote_pages = list_wiki_pages(client, kb_id)
    expected = {page["slug"]: page for page in manifest["pages"]}
    actual = {str(page.get("slug") or ""): page for page in remote_pages}
    if set(actual) != set(expected):
        raise PublisherError(f"合同失败：远端 slug 集不一致 missing={set(expected)-set(actual)} extra={set(actual)-set(expected)}")
    for slug, page in actual.items():
        metadata = page.get("page_metadata") or {}
        if metadata.get("payload_sha256") != expected[slug]["payload_sha256"]:
            raise PublisherError(f"合同失败：页面 hash 不一致：{slug}")
        if page.get("status") != "published" or page.get("source_refs") or page.get("chunk_refs"):
            raise PublisherError(f"合同失败：页面状态/来源引用异常：{slug}")
    agent = unwrap_data(client.request("GET", f"/api/v1/agents/{urllib.parse.quote(agent_id)}"))
    config = agent.get("config") or {}
    if config.get("allowed_tools") != ["wiki_search", "wiki_read_page"]:
        raise PublisherError(f"合同失败：Agent tools={config.get('allowed_tools')}")
    if config.get("knowledge_bases") != [kb_id] or config.get("model_id") != state.get("model_id"):
        raise PublisherError("合同失败：Agent 的 KB/model 绑定不一致")
    if state.get("schema_version") == "starunwiki.release-state/v2" and config.get("system_prompt") != composed_prompt(context):
        raise PublisherError("合同失败：Agent 核心策略或知识包身份与本地部署状态不一致")
    smoke = load_json(context.smoke_path)
    query = str(smoke["questions"][0]["query"])
    session_id = ""
    try:
        session = unwrap_data(client.request("POST", "/api/v1/sessions", {"title": "starunwiki-contract-smoke"}))
        session_id = str(session.get("id") or "")
        if not session_id:
            raise PublisherError("真实 Agent 测试无法创建 session")
        events = client.stream_sse(f"/api/v1/agent-chat/{urllib.parse.quote(session_id)}", {
            "query": query, "agent_enabled": True, "agent_id": agent_id, "knowledge_base_ids": [kb_id],
            "disable_title": True, "web_search_enabled": False, "mcp_service_ids": [], "skill_names": [],
            "images": [], "attachment_ids": [], "attachment_uploads": [], "channel": "publisher-contract-check",
        })
        tool_names = {
            str((event.get("data") or {}).get("tool_name") or "") for event in events
            if event.get("response_type") == "tool_call" and isinstance(event.get("data"), dict)
        }
        answer = "".join(str(event.get("content") or "") for event in events if event.get("response_type") == "answer")
        completed = any(event.get("response_type") == "complete" for event in events)
        if tool_names != {"wiki_search", "wiki_read_page"} or not completed or not answer.strip():
            raise PublisherError(f"真实 Agent 工具合同失败：tools={sorted(tool_names)} completed={completed}")
        citations = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", answer)
        if not citations or any(slug not in expected for slug in citations):
            raise PublisherError(f"真实 Agent 引用不可解析：{citations}")
    finally:
        if session_id:
            client.request("DELETE", f"/api/v1/sessions/{urllib.parse.quote(session_id)}")
    return {**verified, "remote_pages": len(remote_pages), "tools": ["wiki_search", "wiki_read_page"]}


def plan(context: PublisherContext) -> dict[str, Any]:
    verified = validate_context(context)
    counts = verified["counts"]
    publishable = verified["mode"] == "full"
    return {
        "pack_id": context.pack.pack_id,
        "release_id": verified["release_id"],
        "release_mode": verified["mode"],
        "publishable": publishable,
        "create_wiki_kb": publishable,
        "create_pages": counts["pages"] if publishable else 0,
        "draft_pages": counts["draft"],
        "unreviewed_pages": counts["unreviewed"],
        "create_agent": publishable,
        "create_chat_only_key": publishable,
        "embedding_or_rerank": False,
        "reason": None if publishable else "legacy-manifest-only release cannot be freshly published",
    }
