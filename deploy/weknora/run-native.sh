#!/usr/bin/env bash
set -euo pipefail

# Start the local SQLite runtime without modifying services/WeKnora.  Docker
# applies this same version-locked patch in its derived image; native runs use
# Go's overlay feature to compile the patched files from a private runtime
# directory instead of altering the upstream working tree.

deploy_dir="$(cd "$(dirname "$0")" && pwd)"
workspace_dir="$(git -C "$deploy_dir" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: cannot locate the StarunWiki Git repository" >&2
  exit 2
}
weknora_dir="${WEKNORA_SOURCE_DIR:-$workspace_dir/services/WeKnora}"
patch_file="$deploy_dir/weknora-v0.7.2-sqlite-wiki.patch"
native_state_dir="${WEKNORA_NATIVE_STATE_DIR:?WEKNORA_NATIVE_STATE_DIR is required}"
overlay_dir="$native_state_dir/weknora-overlay-v0.7.2-sqlite-wiki"
stamp_file="$overlay_dir/patch.sha256"
overlay_file="$overlay_dir/overlay.json"

requested_auto_migrate="${STARUNWIKI_AUTO_MIGRATE:-false}"
case "$requested_auto_migrate" in
  true|TRUE) normalized_auto_migrate="true" ;;
  false|FALSE) normalized_auto_migrate="false" ;;
  *)
    echo "STARUNWIKI_AUTO_MIGRATE must be true or false" >&2
    exit 2
    ;;
esac

# M0 must never mutate the legacy SQLite schema, even when an old shell or
# dotenv file still exports AUTO_MIGRATE=true.  Native schema migration is
# available only when the caller identifies a committed full release and the
# canonical release verifier accepts it.
if [[ "$normalized_auto_migrate" == "true" ]]; then
  pack_id="${STARUNWIKI_PACK_ID:-deep-sky}"
  release_id="${PUBLIC_RELEASE_ID:?PUBLIC_RELEASE_ID is required when native schema migration is enabled}"
  verification="$("$workspace_dir/manage.sh" release verify --pack "$pack_id" --release "$release_id")"
  verified_mode="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("mode", ""))' <<<"$verification")"
  [[ "$verified_mode" == "full" ]] || {
    echo "native schema migration requires a verified full release; got: ${verified_mode:-unknown}" >&2
    exit 2
  }
fi
export AUTO_MIGRATE="$normalized_auto_migrate"

[[ -f "$patch_file" ]] || { echo "SQLite patch missing: $patch_file" >&2; exit 2; }
[[ -d "$weknora_dir" ]] || { echo "WeKnora source missing: $weknora_dir" >&2; exit 2; }

patch_sha="$(shasum -a 256 "$patch_file" | awk '{print $1}')"

if [[ -e "$overlay_dir" && ! -f "$stamp_file" ]]; then
  echo "refusing an untracked native overlay directory: $overlay_dir" >&2
  exit 2
fi

if [[ -f "$stamp_file" ]]; then
  IFS= read -r recorded_sha < "$stamp_file"
  [[ "$recorded_sha" == "$patch_sha" ]] || {
    echo "native overlay patch hash differs; create a new state directory instead of overwriting it" >&2
    exit 2
  }
else
  mkdir -p "$overlay_dir/internal/application/repository" "$overlay_dir/internal/agent/tools"
  cp "$weknora_dir/internal/application/repository/wiki_page.go" "$overlay_dir/internal/application/repository/wiki_page.go"
  cp "$weknora_dir/internal/application/repository/wiki_page_test.go" "$overlay_dir/internal/application/repository/wiki_page_test.go"
  cp "$weknora_dir/internal/agent/tools/wiki_tools.go" "$overlay_dir/internal/agent/tools/wiki_tools.go"
  cp "$weknora_dir/internal/agent/tools/wiki_tools_test.go" "$overlay_dir/internal/agent/tools/wiki_tools_test.go"
  (cd "$overlay_dir" && patch -p1 < "$patch_file")
  gofmt -w "$overlay_dir/internal/application/repository/wiki_page.go" "$overlay_dir/internal/agent/tools/wiki_tools.go"
  printf '%s\n' "$patch_sha" > "$stamp_file"
  printf '{\n  "Replace": {\n    "%s": "%s",\n    "%s": "%s"\n  }\n}\n' \
    "$weknora_dir/internal/application/repository/wiki_page.go" \
    "$overlay_dir/internal/application/repository/wiki_page.go" \
    "$weknora_dir/internal/agent/tools/wiki_tools.go" \
    "$overlay_dir/internal/agent/tools/wiki_tools.go" > "$overlay_file"
fi

[[ -f "$overlay_file" ]] || { echo "native overlay config missing: $overlay_file" >&2; exit 2; }

if [[ "${1:-}" == "--prepare" ]]; then
  printf 'native SQLite overlay prepared: %s\n' "$overlay_dir"
  exit 0
fi

cd "$weknora_dir"
exec go run -overlay="$overlay_file" -tags sqlite_fts5 ./cmd/server
