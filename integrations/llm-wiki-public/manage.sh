#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")" && pwd)"
workspace_dir="$(cd "$project_dir/../.." && pwd)"
weknora_dir="$workspace_dir/services/WeKnora"
weknora_repo_url="https://github.com/Tencent/WeKnora.git"
weknora_ref="v0.7.2"
weknora_commit="3d5d8bfcdfeeea266b292b71cea616847af28d0f"
compose=(docker compose -p llm-wiki-public --env-file "$project_dir/.env" -f "$weknora_dir/docker-compose.yml" -f "$project_dir/docker-compose.local.yml")
publisher=(uv run --project "$project_dir" python "$project_dir/publisher.py")

usage() {
  echo "usage: $0 {init|manifest|bootstrap|plan|publish|check|infra|reload-model|start|stop|status|logs|config|test}"
}

require_git() {
  command -v git >/dev/null 2>&1 || {
    echo "git is required to prepare WeKnora source" >&2
    return 2
  }
}

validate_weknora_source() {
  require_git
  [[ -f "$weknora_dir/docker-compose.yml" ]] || {
    echo "WeKnora source missing or incomplete: $weknora_dir" >&2
    echo "run $project_dir/manage.sh init to clone the pinned source" >&2
    return 2
  }
  git -C "$weknora_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
    echo "existing WeKnora path is not a Git worktree: $weknora_dir" >&2
    return 2
  }
  local actual_commit
  actual_commit="$(git -C "$weknora_dir" rev-parse HEAD)"
  [[ "$actual_commit" == "$weknora_commit" ]] || {
    echo "WeKnora commit mismatch: expected=$weknora_commit actual=$actual_commit" >&2
    echo "refusing to change an existing worktree automatically" >&2
    return 2
  }
  [[ -z "$(git -C "$weknora_dir" status --porcelain --untracked-files=normal)" ]] || {
    echo "WeKnora worktree has local changes: $weknora_dir" >&2
    echo "refusing to build from unreviewed source; preserve or move those changes first" >&2
    return 2
  }
}

ensure_weknora_source() {
  require_git
  if [[ ! -e "$weknora_dir" ]]; then
    mkdir -p "$(dirname "$weknora_dir")"
    echo "WeKnora source not found; cloning $weknora_ref into $weknora_dir"
    git clone --depth 1 --branch "$weknora_ref" --single-branch "$weknora_repo_url" "$weknora_dir"
  fi
  validate_weknora_source
  echo "WeKnora source ready: $weknora_commit"
}

case "${1:-}" in
  init)
    [[ ! -e "$project_dir/.env" || -f "$project_dir/.env" ]] || {
      echo "expected a regular file at $project_dir/.env" >&2
      exit 2
    }
    if [[ ! -f "$project_dir/.env" ]]; then
      cp "$project_dir/.env.example" "$project_dir/.env"
      chmod 600 "$project_dir/.env"
    fi
    mkdir -p "$project_dir/.secrets"
    chmod 700 "$project_dir/.secrets"
    ensure_weknora_source
    echo "initialized $project_dir/.env; replace every CHANGE_ME value before starting"
    ;;
  manifest|bootstrap|plan|publish|check)
    "${publisher[@]}" "$1" "${@:2}"
    ;;
  infra)
    validate_weknora_source
    "${compose[@]}" up -d --build docreader app
    ;;
  reload-model)
    validate_weknora_source
    "${compose[@]}" restart app
    ;;
  start)
    validate_weknora_source
    [[ -f "$project_dir/.secrets/runtime.env" ]] || { echo "run bootstrap and publish first" >&2; exit 2; }
    "${compose[@]}" up -d --build
    ;;
  stop)
    validate_weknora_source
    "${compose[@]}" down
    ;;
  status)
    validate_weknora_source
    "${compose[@]}" ps
    ;;
  logs)
    validate_weknora_source
    "${compose[@]}" logs -f --tail=200 "${2:-public-bff}" "${3:-public-web}"
    ;;
  config)
    validate_weknora_source
    "${compose[@]}" config
    ;;
  test)
    validate_weknora_source
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
