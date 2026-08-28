#!/usr/bin/env bash
set -euo pipefail

integration_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(git -C "$integration_dir" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: cannot locate the StarunWiki Git repository" >&2
  exit 2
}
echo "DEPRECATED: integrations/llm-wiki-public/manage.sh will be removed in v0.3.0; use $repo_root/manage.sh" >&2

forward_v1_publisher() {
  set +e
  "$repo_root/manage.sh" "$@"
  local status=$?
  set -e
  # The v0.1 publisher exposed one application-error status (2). Preserve it
  # while the v0.2 core keeps its more specific integrity/external statuses.
  case "$status" in
    3|4) return 2 ;;
    *) return "$status" ;;
  esac
}

command_name="${1:-}"
if [[ $# -gt 0 ]]; then shift; fi
case "$command_name" in
  init) exec "$repo_root/manage.sh" init "$@" ;;
  manifest) forward_v1_publisher release verify --pack deep-sky --release current "$@" ;;
  bootstrap) forward_v1_publisher bootstrap check --pack deep-sky --release current "$@" ;;
  plan) forward_v1_publisher release plan --pack deep-sky --release current "$@" ;;
  publish) forward_v1_publisher release publish --pack deep-sky --release current "$@" ;;
  check) forward_v1_publisher release check --pack deep-sky --release current "$@" ;;
  infra|reload-model|start|stop|status|logs|config)
    exec "$repo_root/manage.sh" runtime "$command_name" --pack deep-sky --release current "$@"
    ;;
  test) exec "$repo_root/manage.sh" test "$@" ;;
  "") exec "$repo_root/manage.sh" --help ;;
  *) exec "$repo_root/manage.sh" "$command_name" "$@" ;;
esac
