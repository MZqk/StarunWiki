#!/usr/bin/env python3
"""Add explicit source provenance fields while preserving existing YAML layout."""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
PRIMARY_DOMAINS = {
    "adgsoftware.com",
    "ascom-standards.org",
    "docs.indilib.org",
    "docs.sharpcap.co.uk",
    "fits.gsfc.nasa.gov",
    "github.com",
    "h5.seestar.com",
    "help.dwarflab.com",
    "i.zwoastro.com",
    "ioptron.com",
    "lightpollutionmap.info",
    "nighttime-imaging.eu",
    "openphdguiding.org",
    "pegasusastro.com",
    "pixinsight.com",
    "siril.org",
    "siril.readthedocs.io",
    "skywatcher.com",
    "skysafariastronomy.com",
    "stellarium.org",
    "stellarmate.com",
    "starun.cloud",
    "www.antliafilter.com",
    "www.ascom-standards.org",
    "www.celestron.com",
    "www.cpsc.gov",
    "www.hnsky.org",
    "www.qhyccd.com",
    "www.rc-astro.com",
    "www.sharpcap.co.uk",
    "www.skywatcher.com",
    "www.weather.gov",
    "www.zwoastro.com",
}
EXPERIENCE_DOMAINS = {
    "bbs.zwoastro.com",
    "www.astrobin.com",
    "www.bilibili.com",
    "www.cloudynights.com",
    "www.toutiao.com",
    "zhuanlan.zhihu.com",
}


def normalized_domain(resource: str) -> str:
    domain = urllib.parse.urlparse(resource).netloc.lower().split(":", 1)[0]
    return domain.removeprefix("us.")


def evidence_level(resource: str) -> str:
    if resource.startswith("/"):
        return "internal-ledger"
    domain = normalized_domain(resource)
    if domain in PRIMARY_DOMAINS:
        return "primary"
    if domain in EXPERIENCE_DOMAINS or "forum" in resource.lower() or "/bbs/" in resource.lower():
        return "experience"
    return "secondary"


def usage(resource: str) -> str:
    if resource.startswith("/raw/"):
        return "metadata-only"
    if resource.startswith("/"):
        return "internal-reference"
    return "link-only"


def load_reachable(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(item["url"]) for item in payload.get("results") or [] if item.get("reachable")}


def update_page(path: Path, reachable: set[str], accessed_at: str) -> bool:
    text = path.read_text(encoding="utf-8")
    repaired = re.sub(r"([^\n])    evidence_level:", r"\1\n    evidence_level:", text)
    if repaired != text:
        text = repaired
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"unclosed frontmatter: {path.relative_to(ROOT)}")
    lines = text[:end].splitlines(keepends=True)
    source_start = next((index for index, line in enumerate(lines) if line == "sources:\n"), None)
    if source_start is None:
        raise ValueError(f"sources missing: {path.relative_to(ROOT)}")
    boundaries = [index for index in range(source_start + 1, len(lines)) if lines[index].startswith("  - id:")]
    boundaries.append(len(lines))
    changed = repaired != path.read_text(encoding="utf-8")
    for block_index in range(len(boundaries) - 2, -1, -1):
        start = boundaries[block_index]
        stop = boundaries[block_index + 1]
        block_text = "sources:\n" + "".join(lines[start:stop])
        source = (yaml.safe_load(block_text) or {}).get("sources", [None])[0]
        if not isinstance(source, dict):
            raise ValueError(f"invalid source block: {path.relative_to(ROOT)}:{start + 1}")
        resource = str(source.get("resource") or "")
        additions: list[str] = []
        if not source.get("evidence_level"):
            additions.append(f"    evidence_level: {evidence_level(resource)}\n")
        if not source.get("rights"):
            additions.append("    rights: unknown\n")
        if not source.get("usage"):
            additions.append(f"    usage: {usage(resource)}\n")
        if not source.get("accessed_at"):
            if resource.startswith("/"):
                local = ROOT / resource.lstrip("/")
                if local.is_file():
                    local.read_bytes()
                    additions.append(f'    accessed_at: "{accessed_at}"\n')
            elif resource in reachable:
                additions.append(f'    accessed_at: "{accessed_at}"\n')
        if additions:
            lines[stop:stop] = additions
            changed = True
    if changed:
        path.write_text("".join(lines) + text[end:], encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--link-status", type=Path, required=True)
    parser.add_argument("--accessed-at", required=True)
    args = parser.parse_args()
    reachable = load_reachable(args.link_status)
    changed = 0
    for directory in sorted(ROOT.glob("[0-9][0-9]-*")):
        for path in sorted(directory.glob("*.md")):
            if path.name != "index.md" and update_page(path, reachable, args.accessed_at):
                changed += 1
    print(f"updated {changed} pages; reachable external sources: {len(reachable)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
