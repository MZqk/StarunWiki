from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .errors import StarunWikiError


PACK_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def repository_root() -> Path:
    explicit = os.environ.get("STARUNWIKI_ROOT", "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if (root / "pyproject.toml").is_file() and (root / "knowledge-packs").is_dir():
            return root
        raise StarunWikiError(f"STARUNWIKI_ROOT 不是有效仓库根：{root}")
    starts = [Path(__file__).resolve().parent, Path.cwd()]
    seen: set[Path] = set()
    for start in starts:
        resolved = start.resolve()
        for candidate in (resolved, *resolved.parents):
            if candidate in seen:
                continue
            seen.add(candidate)
            if (candidate / "pyproject.toml").is_file() and (candidate / "knowledge-packs").is_dir():
                return candidate
    raise StarunWikiError("无法从已安装模块位置或当前目录定位 StarunWiki 仓库根")


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise StarunWikiError(f"无法读取 YAML：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise StarunWikiError(f"YAML 顶层必须是对象：{path}")
    return value


def _relative_parts(value: str, field: str, *, allow_glob: bool = False) -> tuple[str, ...]:
    if not value or "\\" in value:
        raise StarunWikiError(f"{field} 必须是非空 POSIX 相对路径")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise StarunWikiError(f"{field} 不得是绝对路径或包含 ..：{value}")
    if not allow_glob and any(any(char in part for char in "*?[") for part in pure.parts):
        raise StarunWikiError(f"{field} 不得包含 glob：{value}")
    return pure.parts


@dataclass(frozen=True)
class Category:
    path: str
    slug: str


@dataclass(frozen=True)
class KnowledgePack:
    repo_root: Path
    root: Path
    pack_id: str
    display_name: str
    locale: str
    index_path: str
    categories: tuple[Category, ...]
    excluded: tuple[str, ...]
    assistant_path: str
    ui_path: str
    smoke_path: str
    publication: dict[str, Any]

    @property
    def descriptor_path(self) -> Path:
        return self.root / "pack.yaml"

    @property
    def releases_root(self) -> Path:
        return self.root / "releases"

    @property
    def active_pointer_path(self) -> Path:
        value = str(self.publication.get("active_release") or "")
        return self.resolve(value, "publication.active_release")

    def resolve(self, value: str, field: str, *, must_exist: bool = False) -> Path:
        parts = _relative_parts(value, field)
        candidate = self.root.joinpath(*parts)
        resolved_root = self.root.resolve()
        resolved = candidate.resolve(strict=False)
        if resolved != resolved_root and resolved_root not in resolved.parents:
            raise StarunWikiError(f"{field} 逃逸知识包目录：{value}")
        if must_exist and not candidate.exists():
            raise StarunWikiError(f"{field} 指向的文件不存在：{value}")
        return candidate

    def category_slugs(self) -> dict[str, str]:
        return {category.path: category.slug for category in self.categories}

    def public_profile(self) -> dict[str, Any]:
        value = _load_yaml(self.resolve(self.ui_path, "profile.ui", must_exist=True))
        if value.get("schema_version") != "starunwiki.ui-profile/v1":
            raise StarunWikiError("profile.ui schema_version 必须是 starunwiki.ui-profile/v1")
        for field in ("brand", "title", "description"):
            if not isinstance(value.get(field), str) or not value[field].strip():
                raise StarunWikiError(f"profile.ui.{field} 必须是非空字符串")
        suggestions = value.get("suggestions")
        if not isinstance(suggestions, list):
            raise StarunWikiError("profile.ui.suggestions 必须是数组")
        for index, suggestion in enumerate(suggestions):
            if not isinstance(suggestion, dict) or any(
                not isinstance(suggestion.get(field), str) or not suggestion[field].strip()
                for field in ("category", "title", "question")
            ):
                raise StarunWikiError(f"profile.ui.suggestions[{index}] 字段不完整")
        return value

    def smoke_profile(self) -> dict[str, Any]:
        value = _load_yaml(self.resolve(self.smoke_path, "profile.smoke", must_exist=True))
        if value.get("schema_version") != "starunwiki.smoke-profile/v1":
            raise StarunWikiError("profile.smoke schema_version 必须是 starunwiki.smoke-profile/v1")
        questions = value.get("questions")
        if not isinstance(questions, list) or not questions:
            raise StarunWikiError("profile.smoke.questions 必须是非空数组")
        for index, question in enumerate(questions):
            if not isinstance(question, dict) or not str(question.get("query") or "").strip():
                raise StarunWikiError(f"profile.smoke.questions[{index}] 缺少 query")
            tools = question.get("required_tools")
            if tools != ["wiki_search", "wiki_read_page"]:
                raise StarunWikiError("smoke required_tools 必须严格为 wiki_search、wiki_read_page")
        return value

    def validate(self) -> dict[str, Any]:
        if not self.resolve(self.index_path, "content.index", must_exist=True).is_file():
            raise StarunWikiError("content.index 必须指向文件")
        seen_paths: set[str] = set()
        seen_slugs: set[str] = set()
        for category in self.categories:
            if category.path in seen_paths or category.slug in seen_slugs:
                raise StarunWikiError("content.categories 的 path/slug 必须唯一")
            seen_paths.add(category.path)
            seen_slugs.add(category.slug)
            directory = self.resolve(category.path, f"content.categories.{category.path}", must_exist=True)
            if not directory.is_dir() or not (directory / "index.md").is_file():
                raise StarunWikiError(f"正式分类缺少目录或 index.md：{category.path}")
        for pattern in self.excluded:
            _relative_parts(pattern, "content.excluded", allow_glob=True)
        self.resolve(self.assistant_path, "profile.assistant", must_exist=True)
        self.public_profile()
        self.smoke_profile()
        allowed = self.publication.get("allowed_status")
        if allowed != ["stable", "draft"]:
            raise StarunWikiError("publication.allowed_status 必须严格为 stable、draft")
        if self.publication.get("required_access") != "public_candidate":
            raise StarunWikiError("publication.required_access 必须是 public_candidate")
        active = self.active_pointer_path
        if not active.is_file():
            raise StarunWikiError("publication.active_release 指针不存在")
        return {
            "pack_id": self.pack_id,
            "display_name": self.display_name,
            "locale": self.locale,
            "categories": len(self.categories),
            "active_pointer": active.relative_to(self.root).as_posix(),
        }


def load_pack(pack_id: str = "deep-sky", *, repo: Path | None = None, pack_path: Path | None = None) -> KnowledgePack:
    repo = (repo or repository_root()).resolve()
    if pack_path is None:
        if not PACK_ID_RE.fullmatch(pack_id):
            raise StarunWikiError(f"非法 pack ID：{pack_id}")
        root = repo / "knowledge-packs" / pack_id
    else:
        root = pack_path.resolve()
        packs_root = (repo / "knowledge-packs").resolve()
        if root != packs_root and packs_root not in root.parents:
            raise StarunWikiError("pack_path 必须位于 knowledge-packs 目录内")
    descriptor = root / "pack.yaml"
    value = _load_yaml(descriptor)
    if value.get("schema_version") != "starunwiki.pack/v1":
        raise StarunWikiError("pack.yaml schema_version 必须是 starunwiki.pack/v1")
    actual_id = str(value.get("id") or "")
    if not PACK_ID_RE.fullmatch(actual_id) or actual_id != pack_id:
        raise StarunWikiError(f"pack ID 不匹配：requested={pack_id} actual={actual_id}")
    content = value.get("content")
    profile = value.get("profile")
    publication = value.get("publication")
    if not isinstance(content, dict) or not isinstance(profile, dict) or not isinstance(publication, dict):
        raise StarunWikiError("pack.yaml 缺少 content/profile/publication 对象")
    raw_categories = content.get("categories")
    if not isinstance(raw_categories, list) or not raw_categories:
        raise StarunWikiError("content.categories 必须是非空数组")
    categories: list[Category] = []
    for index, item in enumerate(raw_categories):
        if not isinstance(item, dict):
            raise StarunWikiError(f"content.categories[{index}] 必须是对象")
        path = str(item.get("path") or "")
        slug = str(item.get("slug") or "")
        _relative_parts(path, f"content.categories[{index}].path")
        if not PACK_ID_RE.fullmatch(slug):
            raise StarunWikiError(f"content.categories[{index}].slug 非法：{slug}")
        categories.append(Category(path, slug))
    excluded = content.get("excluded")
    if not isinstance(excluded, list) or not all(isinstance(item, str) for item in excluded):
        raise StarunWikiError("content.excluded 必须是字符串数组")
    pack = KnowledgePack(
        repo_root=repo,
        root=root,
        pack_id=actual_id,
        display_name=str(value.get("display_name") or actual_id),
        locale=str(value.get("locale") or ""),
        index_path=str(content.get("index") or ""),
        categories=tuple(categories),
        excluded=tuple(excluded),
        assistant_path=str(profile.get("assistant") or ""),
        ui_path=str(profile.get("ui") or ""),
        smoke_path=str(profile.get("smoke") or ""),
        publication=dict(publication),
    )
    pack.validate()
    return pack
