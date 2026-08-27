#!/usr/bin/env python3
"""Build and publish the fixed public LLM Wiki snapshot to a Wiki-only WeKnora KB."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import posixpath
import re
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parents[1]
AUTHORIZATION_FILE = ROOT / "authorization" / "public-authorization.json"
MANIFEST_FILE = ROOT / "authorization" / "public-manifest.json"
PROMPT_FILE = ROOT / "agent-system-prompt.md"
SECRET_DIR = ROOT / ".secrets"
BOOTSTRAP_STATE = SECRET_DIR / "bootstrap.json"
RUNTIME_ENV = SECRET_DIR / "runtime.env"
RELEASE_STATE = ROOT / "release-state.json"
DEFAULT_CORPUS = WORKSPACE_ROOT / ".knowledge-catalog" / "retrieval-corpus.jsonl"
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+?\.md)(?:#[^)]*)?\)")


class PublisherError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublisherError(f"无法读取 JSON：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise PublisherError(f"JSON 顶层必须是对象：{path}")
    return value


def load_corpus(path: Path) -> tuple[list[dict[str, Any]], str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PublisherError(f"无法读取语料：{path}：{exc}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PublisherError(f"语料第 {line_number} 行不是合法 JSON：{exc}") from exc
        if not isinstance(row, dict):
            raise PublisherError(f"语料第 {line_number} 行不是对象")
        rows.append(row)
    return rows, sha256_bytes(raw)


def authorized(row: dict[str, Any]) -> bool:
    return (
        row.get("access") == "public_candidate"
        and row.get("status") in {"stable", "draft"}
        and not bool(row.get("content_withheld", False))
        and isinstance(row.get("text"), str)
    )


def slug_for(entry_name: str, explicit_slug: str = "") -> str:
    if explicit_slug:
        value = explicit_slug.strip().strip("/")
        if not value.startswith("concept/") or ".." in value.split("/"):
            raise PublisherError(f"非法显式 slug：{explicit_slug}")
        return value
    value = entry_name.strip().replace("\\", "/")
    if not value.startswith("wiki/") or not value.endswith(".md"):
        raise PublisherError(f"非法 entry_name：{entry_name}")
    value = posixpath.normpath(value[len("wiki/") : -len(".md")]).strip("/")
    if value.startswith("..") or not value:
        raise PublisherError(f"非法 entry_name：{entry_name}")
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


def build_manifest(corpus_path: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    authorization = load_json(AUTHORIZATION_FILE)
    rows, corpus_sha = load_corpus(corpus_path)
    expected_sha = str(authorization.get("corpus_sha256") or "")
    if corpus_sha != expected_sha:
        raise PublisherError(f"语料 SHA-256 漂移：want={expected_sha} got={corpus_sha}")

    selected = sorted((row for row in rows if authorized(row)), key=lambda item: str(item.get("entry_name") or ""))
    expected_count = int(authorization.get("expected_page_count") or 0)
    if len(selected) != expected_count:
        raise PublisherError(f"公开页数量漂移：want={expected_count} got={len(selected)}")

    entries = [str(row.get("entry_name") or "") for row in selected]
    if len(entries) != len(set(entries)):
        raise PublisherError("授权集合存在重复 entry_name")
    slug_map = {str(row["entry_name"]): slug_for(str(row["entry_name"]), str(row.get("slug") or "")) for row in selected}
    if len(slug_map.values()) != len(set(slug_map.values())):
        raise PublisherError("授权集合存在重复 slug")

    release_id = f"public-{corpus_sha[:12]}"
    pages: list[dict[str, Any]] = []
    for row in selected:
        entry = str(row["entry_name"])
        content = rewrite_links(str(row["text"]), entry, slug_map).strip() + "\n"
        page = {
            "entry_name": entry,
            "slug": slug_map[entry],
            "title": str(row.get("display_name") or entry),
            "summary": str(row.get("description") or ""),
            "content": content,
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

    return {
        "schema_version": 1,
        "release_id": release_id,
        "scope": "anonymous-public",
        "corpus_path": str(corpus_path),
        "corpus_sha256": corpus_sha,
        "page_count": len(pages),
        "draft_count": sum(page["source_status"] == "draft" for page in pages),
        "stable_count": sum(page["source_status"] == "stable" for page in pages),
        "unreviewed_count": sum(page["source_review_state"] == "needs-human-review" for page in pages),
        "pages": pages,
    }


def validate_manifest(manifest: dict[str, Any], corpus_path: Path = DEFAULT_CORPUS) -> None:
    rebuilt = build_manifest(corpus_path)
    if canonical_json(rebuilt) != canonical_json(manifest):
        raise PublisherError("public-manifest.json 与固定语料快照不一致；请重新生成并审查")
    authorization = load_json(AUTHORIZATION_FILE)
    expected_count = int(authorization.get("expected_page_count") or 0)
    expected_unreviewed = int(authorization.get("expected_unreviewed_count") or 0)
    if manifest.get("page_count") != expected_count or manifest.get("unreviewed_count") != expected_unreviewed:
        raise PublisherError(
            f"manifest 计数不满足授权合同：pages={manifest.get('page_count')}/{expected_count} "
            f"unreviewed={manifest.get('unreviewed_count')}/{expected_unreviewed}"
        )


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
            raise PublisherError(f"{method} {path} -> HTTP {exc.code}: {raw[:1000]}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PublisherError(f"{method} {path} 失败：{exc}") from exc

    def multipart_input(self, path: str, value: str) -> Any:
        boundary = f"----llmwiki{secrets.token_hex(12)}"
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
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise PublisherError(f"POST {path} -> HTTP {exc.code}: {raw[:1000]}") from exc

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
                        data_lines.append(line[len("data:") :].strip())
                if data_lines:
                    value = json.loads("\n".join(data_lines))
                    if isinstance(value, dict):
                        events.append(value)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise PublisherError(f"POST {path} -> HTTP {exc.code}: {raw[:1000]}") from exc
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
    """Return the one-time plaintext token from an API-key creation response."""
    # The ORM save hook may leave `api_key` holding the encrypted-at-rest
    # representation, while `token` is the explicit one-time credential.
    return str(value.get("token") or "")


def bootstrap() -> None:
    manifest = load_json(MANIFEST_FILE)
    validate_manifest(manifest)
    client = admin_client()
    tenant_id = int(env_required("WEKNORA_TENANT_ID"))
    model_name = env_required("LLM_MODEL_NAME")
    base_url = env_required("LLM_BASE_URL")
    provider = os.environ.get("LLM_PROVIDER", "openai").strip() or "openai"

    models = list(unwrap_data(client.request("GET", "/api/v1/models")) or [])
    if any(item.get("type") in {"Embedding", "Rerank"} for item in models):
        raise PublisherError("专用实例中存在 Embedding/Rerank 模型，拒绝继续")
    matches = [
        item
        for item in models
        if item.get("id") == "builtin-llm-wiki-chat"
        and item.get("type") == "KnowledgeQA"
        and item.get("name") == model_name
    ]
    if len(matches) != 1:
        raise PublisherError("未找到唯一 builtin-llm-wiki-chat；首次创建 Tenant 后请先执行 ./manage.sh reload-model")
    model = matches[0]
    model_id = str(model.get("id") or "")
    if not model_id:
        raise PublisherError("builtin 模型响应缺少 id")
    parameters = model.get("parameters") or {}
    if parameters.get("base_url") != base_url or parameters.get("provider") != provider:
        raise PublisherError(f"builtin 模型参数与环境不一致：{parameters}")

    api_key = getpass.getpass("模型 API Key（仅本次输入）: ").strip()
    if not api_key:
        raise PublisherError("模型 API Key 不能为空")
    client.request("PUT", f"/api/v1/models/{urllib.parse.quote(model_id)}/credentials", {"api_key": api_key})
    debug = unwrap_data(client.multipart_input(f"/api/v1/models/{urllib.parse.quote(model_id)}/debug", "只回复 OK"))
    if not isinstance(debug, dict) or not debug.get("ok"):
        raise PublisherError(f"模型真实调用失败：{debug}")

    hmac_secret = secrets.token_urlsafe(48)
    client.request(
        "PUT",
        f"/api/v1/tenants/{tenant_id}/api-principal-config",
        {"mode": "signed_token", "hmac_secret": hmac_secret},
    )
    atomic_write(
        BOOTSTRAP_STATE,
        json.dumps(
            {"schema_version": 1, "tenant_id": tenant_id, "model_id": model_id, "external_hmac_secret": hmac_secret},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    print(f"bootstrap 完成：model_id={model_id}")


def page_payload(page: dict[str, Any], release_id: str) -> dict[str, Any]:
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
            "schema_version": "llm-wiki-public-page-v1",
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


def publish() -> None:
    manifest = load_json(MANIFEST_FILE)
    validate_manifest(manifest)
    bootstrap_state = load_json(BOOTSTRAP_STATE)
    client = admin_client()
    model_id = str(bootstrap_state["model_id"])
    tenant_id = int(bootstrap_state["tenant_id"])
    release_id = str(manifest["release_id"])

    kb = unwrap_data(
        client.request(
            "POST",
            "/api/v1/knowledge-bases",
            {
                "name": f"llm-wiki-{release_id}",
                "description": f"固定{manifest['page_count']}页匿名公开纯Wiki派生库",
                "type": "document",
                "embedding_model_id": "",
                "rerank_model_id": "",
                "summary_model_id": model_id,
                "indexing_strategy": {
                    "vector_enabled": False,
                    "keyword_enabled": False,
                    "wiki_enabled": True,
                    "graph_enabled": False,
                },
                "wiki_config": {},
            },
        )
    )
    kb_id = str(kb.get("id") or "")
    if not kb_id:
        raise PublisherError("知识库创建响应缺少 id")

    for index, page in enumerate(manifest["pages"], 1):
        client.request(
            "POST",
            f"/api/v1/knowledgebase/{urllib.parse.quote(kb_id)}/wiki/pages",
            page_payload(page, release_id),
        )
        if index % 20 == 0 or index == len(manifest["pages"]):
            print(f"已写入 Wiki 页面 {index}/{len(manifest['pages'])}")

    agent = unwrap_data(
        client.request(
            "POST",
            "/api/v1/agents",
            {
                "name": f"LLM Wiki Public {release_id}",
                "description": "匿名公开纯 Wiki 问答，只读",
                "avatar": "telescope",
                "config": {
                    "agent_mode": "smart-reasoning",
                    "agent_type": "wiki-qa",
                    "system_prompt": PROMPT_FILE.read_text(encoding="utf-8").strip(),
                    "model_id": model_id,
                    "rerank_model_id": "",
                    "temperature": 0.2,
                    "max_iterations": 10,
                    "thinking": False,
                    "citation_enabled": True,
                    "allowed_tools": ["wiki_search", "wiki_read_page"],
                    "kb_selection_mode": "selected",
                    "knowledge_bases": [kb_id],
                    "retrieve_kb_only_when_mentioned": False,
                    "retain_retrieval_history": False,
                    "mcp_selection_mode": "none",
                    "skills_selection_mode": "none",
                    "web_search_enabled": False,
                    "image_upload_enabled": False,
                    "audio_upload_enabled": False,
                    "multi_turn_enabled": True,
                    "history_turns": 5,
                },
            },
        )
    )
    agent_id = str(agent.get("id") or "")
    if not agent_id:
        raise PublisherError("Agent 创建响应缺少 id")

    key_result = unwrap_data(
        client.request(
            "POST",
            f"/api/v1/tenants/{tenant_id}/api-keys",
            {
                "name": f"llm-wiki-public-chat-{release_id}",
                "full_access": False,
                "knowledge_base_ids": [kb_id],
                "capabilities": ["chat"],
                "expires_at_unix": int(time.time()) + 90 * 24 * 3600,
            },
        )
    )
    chat_key = created_api_key_token(key_result)
    if not chat_key:
        raise PublisherError("chat-only API Key 创建响应没有返回明文 key")

    runtime = {
        "WEKNORA_TENANT_ID": str(tenant_id),
        "WEKNORA_CHAT_API_KEY": chat_key,
        "WEKNORA_EXTERNAL_HMAC_SECRET": str(bootstrap_state["external_hmac_secret"]),
        "WEKNORA_AGENT_ID": agent_id,
        "WEKNORA_KB_ID": kb_id,
        "WEKNORA_MODEL_ID": model_id,
        "PUBLIC_RELEASE_ID": release_id,
    }
    atomic_write(RUNTIME_ENV, "".join(f"{key}={value}\n" for key, value in runtime.items()))
    atomic_write(
        RELEASE_STATE,
        json.dumps(
            {
                "schema_version": 1,
                "release_id": release_id,
                "kb_id": kb_id,
                "agent_id": agent_id,
                "model_id": model_id,
                "page_count": manifest["page_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    print(f"publish 完成：release={release_id} pages={manifest['page_count']}")


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


def check() -> None:
    manifest = load_json(MANIFEST_FILE)
    validate_manifest(manifest)
    state = load_json(RELEASE_STATE)
    client = admin_client()
    kb_id = str(state["kb_id"])
    agent_id = str(state["agent_id"])

    models = list(unwrap_data(client.request("GET", "/api/v1/models")) or [])
    if any(item.get("type") in {"Embedding", "Rerank"} for item in models):
        raise PublisherError("合同失败：存在 Embedding/Rerank 模型")
    kb = unwrap_data(client.request("GET", f"/api/v1/knowledge-bases/{urllib.parse.quote(kb_id)}"))
    strategy = kb.get("indexing_strategy") or {}
    expected_strategy = {
        "vector_enabled": False,
        "keyword_enabled": False,
        "wiki_enabled": True,
        "graph_enabled": False,
    }
    if any(bool(strategy.get(key)) != value for key, value in expected_strategy.items()):
        raise PublisherError(f"合同失败：KB indexing_strategy={strategy}")
    if kb.get("embedding_model_id") or kb.get("rerank_model_id"):
        raise PublisherError("合同失败：KB 绑定了 embedding/rerank model")

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

    session_id = ""
    try:
        session = unwrap_data(client.request("POST", "/api/v1/sessions", {"title": "public-wiki-contract-smoke"}))
        session_id = str(session.get("id") or "")
        if not session_id:
            raise PublisherError("真实 Agent 测试无法创建 session")
        events = client.stream_sse(
            f"/api/v1/agent-chat/{urllib.parse.quote(session_id)}",
            {
                "query": "请先用 wiki_search 搜索城市阳台深空拍摄，再用 wiki_read_page 读取最相关页面，最后用一句话回答并引用该页。",
                "agent_enabled": True,
                "agent_id": agent_id,
                "knowledge_base_ids": [kb_id],
                "disable_title": True,
                "web_search_enabled": False,
                "mcp_service_ids": [],
                "skill_names": [],
                "images": [],
                "attachment_ids": [],
                "attachment_uploads": [],
                "channel": "publisher-contract-check",
            },
        )
        tool_names = {
            str((event.get("data") or {}).get("tool_name") or "")
            for event in events
            if event.get("response_type") == "tool_call" and isinstance(event.get("data"), dict)
        }
        answer = "".join(str(event.get("content") or "") for event in events if event.get("response_type") == "answer")
        completed = any(event.get("response_type") == "complete" for event in events)
        if tool_names != {"wiki_search", "wiki_read_page"} or not completed or not answer.strip():
            raise PublisherError(
                f"真实 Agent 工具合同失败：tools={sorted(tool_names)} completed={completed} answer={bool(answer.strip())}"
            )
        citations = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", answer)
        if not citations or any(slug not in expected for slug in citations):
            raise PublisherError(f"真实 Agent 引用不可解析：{citations}")
    finally:
        if session_id:
            client.request("DELETE", f"/api/v1/sessions/{urllib.parse.quote(session_id)}")
    print(f"check 通过：release={manifest['release_id']} pages={len(remote_pages)} tools=wiki_search,wiki_read_page")


def plan() -> None:
    manifest = load_json(MANIFEST_FILE)
    validate_manifest(manifest)
    print(
        json.dumps(
            {
                "release_id": manifest["release_id"],
                "create_wiki_kb": True,
                "create_pages": manifest["page_count"],
                "draft_pages": manifest["draft_count"],
                "unreviewed_pages": manifest["unreviewed_count"],
                "create_agent": True,
                "create_chat_only_key": True,
                "embedding_or_rerank": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["manifest", "bootstrap", "plan", "publish", "check"])
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    args = parser.parse_args(argv)
    try:
        if args.command == "manifest":
            manifest = build_manifest(args.corpus)
            atomic_write(MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", mode=0o644)
            print(f"manifest 已生成：{MANIFEST_FILE}（{manifest['page_count']} 页）")
        elif args.command == "bootstrap":
            bootstrap()
        elif args.command == "plan":
            plan()
        elif args.command == "publish":
            publish()
        else:
            check()
    except PublisherError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
