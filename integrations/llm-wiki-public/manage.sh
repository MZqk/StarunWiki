#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")" && pwd)"
workspace_dir="$(cd "$project_dir/../.." && pwd)"
weknora_dir="$workspace_dir/services/WeKnora"
compose=(docker compose -p llm-wiki-public --env-file "$project_dir/.env" -f "$weknora_dir/docker-compose.yml" -f "$project_dir/docker-compose.local.yml")
publisher=(uv run --project "$project_dir" python "$project_dir/publisher.py")

usage() {
  echo "usage: $0 {init|manifest|bootstrap|plan|publish|check|infra|reload-model|start|stop|status|logs|config|test}"
}

case "${1:-}" in
  init)
    if [[ ! -f "$project_dir/.env" ]]; then
      cp "$project_dir/.env.example" "$project_dir/.env"
      chmod 600 "$project_dir/.env"
    fi
    mkdir -p "$project_dir/.secrets"
    chmod 700 "$project_dir/.secrets"
    echo "initialized $project_dir/.env; replace every CHANGE_ME value before starting"
    ;;
  manifest|bootstrap|plan|publish|check)
    "${publisher[@]}" "$1" "${@:2}"
    ;;
  infra)
    "${compose[@]}" up -d --build docreader app
    ;;
  reload-model)
    "${compose[@]}" restart app
    ;;
  start)
    [[ -f "$project_dir/.secrets/runtime.env" ]] || { echo "run bootstrap and publish first" >&2; exit 2; }
    "${compose[@]}" up -d --build
    ;;
  stop)
    "${compose[@]}" down
    ;;
  status)
    "${compose[@]}" ps
    ;;
  logs)
    "${compose[@]}" logs -f --tail=200 "${2:-public-bff}" "${3:-public-web}"
    ;;
  config)
    "${compose[@]}" config
    ;;
  test)
    (cd "$project_dir" && uv run python -m unittest discover -s tests -v)
    (cd "$weknora_dir" && git apply --check "$project_dir/weknora-build/weknora-v0.7.2-sqlite-wiki.patch")
    (cd "$project_dir/bff" && go test -race ./...)
    (cd "$project_dir/web" && npm test && npm run build)
    ;;
  *)
    usage
    exit 2
    ;;
esac
