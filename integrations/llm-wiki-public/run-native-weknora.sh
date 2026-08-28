#!/usr/bin/env bash
set -euo pipefail

integration_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(git -C "$integration_dir" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: cannot locate the StarunWiki Git repository" >&2
  exit 2
}
echo "DEPRECATED: use $repo_root/deploy/weknora/run-native.sh; this wrapper will be removed in v0.3.0" >&2
exec "$repo_root/deploy/weknora/run-native.sh" "$@"
