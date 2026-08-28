#!/usr/bin/env python3
"""Fail-closed audit for formal pages that may support authoritative answers."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
FORMAL_DIR_RE = re.compile(r"^[0-9]{2}-")
FOOTNOTE_RE = re.compile(r"\[\^([^\]]+)\]")
FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
HIGH_IMPACT_TYPES = {
    "Buying Guide",
    "Equipment Guide",
    "Equipment Reference",
    "Capture SOP",
    "Playbook",
    "Software Guide",
    "Comparison",
    "Target Reference",
    "Planning Reference",
    "FAQ",
    "Troubleshooting Guide",
}
SOURCE_LEVELS = {"primary", "secondary", "experience", "internal-ledger"}
RIGHTS = {"permitted", "restricted", "unknown"}
PLACEHOLDER_FOOTNOTES = {"source-id", "sources-id"}


def parse_page(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unclosed frontmatter")
    metadata = yaml.safe_load(text[4:end]) or {}
    if not isinstance(metadata, dict):
        raise ValueError("frontmatter is not an object")
    return metadata, text[end + 5 :]


def formal_pages() -> list[Path]:
    pages: list[Path] = []
    for directory in sorted(path for path in ROOT.iterdir() if path.is_dir() and FORMAL_DIR_RE.match(path.name)):
        pages.extend(path for path in sorted(directory.glob("*.md")) if path.name != "index.md")
    return pages


def audit_page(path: Path, today: dt.date) -> dict[str, Any]:
    relative = path.relative_to(ROOT).as_posix()
    blockers: list[str] = []
    warnings: list[str] = []
    try:
        metadata, body = parse_page(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return {"path": relative, "authority_ready": False, "blockers": [str(exc)], "warnings": []}

    applies_to = metadata.get("applies_to") or {}
    for field in ("系统", "条件", "不适用"):
        values = applies_to.get(field) if isinstance(applies_to, dict) else None
        if not isinstance(values, list) or not values:
            blockers.append(f"missing applies_to.{field}")

    stale_after = str(metadata.get("stale_after") or "")
    try:
        if dt.date.fromisoformat(stale_after) < today:
            blockers.append(f"stale_after expired: {stale_after}")
    except ValueError:
        blockers.append("invalid stale_after")

    sources = metadata.get("sources") or []
    source_ids = {str(source.get("id") or "") for source in sources if isinstance(source, dict)}
    referenced_ids = set(FOOTNOTE_RE.findall(FENCED_CODE_RE.sub("", body))) - PLACEHOLDER_FOOTNOTES
    if "## 权威问答口径\n" not in body:
        blockers.append("missing authority answer contract")
    unresolved = sorted(referenced_ids - source_ids)
    if unresolved:
        blockers.append("unresolved body citations: " + ", ".join(unresolved))
    if metadata.get("type") in HIGH_IMPACT_TYPES and not referenced_ids:
        blockers.append("high-impact page has no claim-level citations")

    cited_primary_count = 0
    for index, source in enumerate(sources, 1):
        if not isinstance(source, dict):
            blockers.append(f"source {index} is not an object")
            continue
        source_id = str(source.get("id") or f"#{index}")
        level = str(source.get("evidence_level") or "")
        if level not in SOURCE_LEVELS:
            blockers.append(f"source {source_id} missing evidence_level")
        if level == "primary" and source_id in referenced_ids:
            cited_primary_count += 1
        rights = str(source.get("rights") or "")
        if rights not in RIGHTS:
            blockers.append(f"source {source_id} missing rights")
        if not source.get("accessed_at"):
            if source_id in referenced_ids:
                blockers.append(f"cited source {source_id} missing accessed_at")
            else:
                warnings.append(f"uncited source {source_id} missing accessed_at")
        if not source.get("usage"):
            blockers.append(f"source {source_id} missing usage")
    if metadata.get("type") in HIGH_IMPACT_TYPES and cited_primary_count == 0:
        blockers.append("high-impact page has no cited primary source")

    verified = metadata.get("verified")
    if not isinstance(verified, dict):
        blockers.append("missing human verified record")
    else:
        verifier = str(verified.get("by") or "")
        if not verifier.startswith("human:"):
            blockers.append("verified.by is not a human actor")
        if not verified.get("at"):
            blockers.append("verified.at missing")
        scope = str(verified.get("scope") or "").strip()
        if len(scope) < 12:
            blockers.append("verified.scope is missing or not auditable")

    if metadata.get("review", {}).get("state") == "needs-human-review":
        warnings.append("review.state remains needs-human-review")

    return {
        "path": relative,
        "type": metadata.get("type"),
        "authority_ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", default=dt.date.today().isoformat(), help="audit date in YYYY-MM-DD")
    parser.add_argument("--only-blocked", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument(
        "--machine-gate",
        action="store_true",
        help="仅以机器可修复阻断决定退出码；仍在报告中保留真人签署缺口",
    )
    args = parser.parse_args()
    today = dt.date.fromisoformat(args.today)
    pages = [audit_page(path, today) for path in formal_pages()]
    visible = [page for page in pages if not page["authority_ready"]] if args.only_blocked else pages
    summary = {
        "as_of": today.isoformat(),
        "page_count": len(pages),
        "authority_ready": sum(page["authority_ready"] for page in pages),
        "blocked": sum(not page["authority_ready"] for page in pages),
        "blocker_count": sum(len(page["blockers"]) for page in pages),
        "machine_ready_for_human_review": sum(
            set(page["blockers"]) == {"missing human verified record"} for page in pages
        ),
        "machine_blocked": sum(
            any(blocker != "missing human verified record" for blocker in page["blockers"])
            for page in pages
        ),
        "human_verification_pending": sum(
            "missing human verified record" in page["blockers"] for page in pages
        ),
    }
    blocker_kinds: collections.Counter[str] = collections.Counter()
    for page in pages:
        for blocker in page["blockers"]:
            if blocker.startswith("source ") and " missing " in blocker:
                blocker_kinds["source_missing_" + blocker.rsplit(" missing ", 1)[1]] += 1
            elif blocker.startswith("high-impact page has no claim-level citations"):
                blocker_kinds["high_impact_without_claim_citations"] += 1
            elif blocker.startswith("high-impact page has no cited primary source"):
                blocker_kinds["high_impact_without_cited_primary_source"] += 1
            elif blocker.startswith("cited source ") and blocker.endswith("missing accessed_at"):
                blocker_kinds["cited_source_missing_accessed_at"] += 1
            elif blocker == "missing human verified record":
                blocker_kinds["missing_human_verified"] += 1
            else:
                blocker_kinds["other"] += 1
    summary["blocker_kinds"] = dict(sorted(blocker_kinds.items()))
    if args.summary_only:
        visible = []
    print(json.dumps({"summary": summary, "pages": visible}, ensure_ascii=False, indent=2))
    if args.machine_gate:
        return 0 if summary["machine_blocked"] == 0 else 2
    return 0 if summary["blocked"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
