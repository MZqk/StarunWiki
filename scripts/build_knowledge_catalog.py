#!/usr/bin/env python3
"""v0.2 compatibility wrapper for the deep-sky catalog builder."""

from __future__ import annotations

import sys
import subprocess
from pathlib import Path

script_dir = Path(__file__).resolve().parent
result = subprocess.run(
    ["git", "-C", str(script_dir), "rev-parse", "--show-toplevel"],
    text=True,
    capture_output=True,
)
if result.returncode or not result.stdout.strip():
    print("ERROR: cannot locate the StarunWiki Git repository", file=sys.stderr)
    raise SystemExit(2)
REPO = Path(result.stdout.strip()).resolve()
sys.path.insert(0, str(REPO / "src"))
from starunwiki.catalog import main  # noqa: E402
from starunwiki.errors import StarunWikiError  # noqa: E402

if __name__ == "__main__":
    print("DEPRECATED: use ./manage.sh pack build deep-sky; this wrapper will be removed in v0.3.0", file=sys.stderr)
    try:
        raise SystemExit(main(["--pack", "deep-sky", *sys.argv[1:]]))
    except StarunWikiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
