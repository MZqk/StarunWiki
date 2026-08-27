#!/usr/bin/env python3
"""Check external formal-page sources without modifying Wiki files."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "StarunWikiSourceAudit/1.0 (+local evidence review)"
FOOTNOTE_RE = re.compile(r"\[\^([^\]]+)\]")
FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)


def source_urls(only_cited: bool = False) -> list[str]:
    urls: set[str] = set()
    for directory in ROOT.glob("[0-9][0-9]-*"):
        for path in directory.glob("*.md"):
            if path.name == "index.md":
                continue
            text = path.read_text(encoding="utf-8")
            end = text.find("\n---\n", 4)
            metadata = yaml.safe_load(text[4:end]) or {}
            cited_ids = set(FOOTNOTE_RE.findall(FENCED_CODE_RE.sub("", text[end + 5 :])))
            for source in metadata.get("sources") or []:
                resource = str(source.get("resource") or "")
                if resource.startswith(("http://", "https://")) and (
                    not only_cited or str(source.get("id") or "") in cited_ids
                ):
                    urls.add(resource)
    return sorted(urls)


def check(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*;q=0.5", "Range": "bytes=0-4095"},
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            response.read(4096)
            return {
                "url": url,
                "reachable": 200 <= response.status < 400,
                "status": response.status,
                "final_url": response.geturl(),
                "content_type": response.headers.get("Content-Type"),
            }
    except urllib.error.HTTPError as exc:
        return {"url": url, "reachable": False, "status": exc.code, "error": str(exc)}
    except Exception as exc:  # network/TLS failures must remain explicit audit evidence
        return {"url": url, "reachable": False, "status": None, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--only-cited", action="store_true", help="只检查正文实际引用的外部来源")
    args = parser.parse_args()
    urls = source_urls(only_cited=args.only_cited)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda url: check(url, args.timeout), urls))
    payload = {
        "summary": {
            "url_count": len(results),
            "reachable": sum(item["reachable"] for item in results),
            "failed": sum(not item["reachable"] for item in results),
        },
        "results": results,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    for item in results:
        if not item["reachable"]:
            print(json.dumps(item, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
