#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"
if command -v uv >/dev/null 2>&1; then
  exec uv run --project "$repo_root" python -m starunwiki.cli "$@"
fi
exec python3 -m starunwiki.cli "$@"
