#!/bin/sh
set -eu

APP_DIR="${APP_DIR:-/volume1/docker/spotify-playlist-builder}"
BRANCH="${BRANCH:-main}"
API_URL="${API_URL:-http://127.0.0.1:5150}"
DRY_RUN="${DRY_RUN:-false}"
SEARCH_LIMIT="${SEARCH_LIMIT:-50}"
TARGETS_FILE="${TARGETS_FILE:-config/playlist_targets.csv}"
SUDO="${SUDO-sudo -n}"

cd "$APP_DIR" || exit 1

git_run() {
  $SUDO docker run --rm \
    -v "$APP_DIR:/repo" \
    -w /repo \
    alpine/git "$@"
}

slugify() {
  basename "$1" .csv | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//'
}

trim() {
  sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//'
}

playlist_id_for_key() {
  key="$1"

  if [ ! -f "$TARGETS_FILE" ]; then
    return 0
  fi

  awk -F, -v key="$key" '
    NR == 1 { next }
    /^[[:space:]]*#/ { next }
    $1 == key {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2)
      print $2
      exit
    }
  ' "$TARGETS_FILE"
}

playlist_name_for_key() {
  key="$1"

  if [ ! -f "$TARGETS_FILE" ]; then
    return 0
  fi

  awk -F, -v key="$key" '
    NR == 1 { next }
    /^[[:space:]]*#/ { next }
    $1 == key {
      name = $3
      for (i = 4; i <= NF; i++) {
        name = name "," $i
      }
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      print name
      exit
    }
  ' "$TARGETS_FILE"
}

target_csv_for_key() {
  key="$1"
  candidate="playlists/$(printf '%s' "$key" | tr '-' '_').csv"

  if [ -f "$candidate" ]; then
    printf '%s\n' "$candidate"
  fi
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

wait_for_api() {
  count=0
  until curl -fsS "$API_URL/health" >/dev/null 2>&1; do
    count=$((count + 1))
    if [ "$count" -gt 30 ]; then
      echo "API did not become healthy at $API_URL/health"
      return 1
    fi
    sleep 2
  done
}

# CHANGED: scripts/* removed. This host-side script and anything else under
# scripts/ doesn't run inside the container, so editing it shouldn't force a
# rebuild. Only files that are actually baked into the image belong here.
needs_container_rebuild() {
  for changed_file in $1; do
    case "$changed_file" in
      Dockerfile|docker-compose.yml|requirements.txt|api_server.py|playlist_builder.py|spotify_client.py)
        return 0
        ;;
    esac
  done

  return 1
}

rebuild_container() {
  if $SUDO docker compose version >/dev/null 2>&1; then
    $SUDO docker compose up -d --build
  elif command -v docker-compose >/dev/null 2>&1; then
    $SUDO docker-compose up -d --build
  else
    echo "Docker Compose not found. Restarting container only..."
    $SUDO docker restart spotify-playlist-builder
  fi
}

build_payload() {
  key="$1"
  playlist_id="${2:-}"
  playlist_name="$(playlist_name_for_key "$key")"

  payload="{\"dry_run\":$DRY_RUN,\"search_limit\":$SEARCH_LIMIT"

  if [ -n "$playlist_id" ]; then
    payload="$payload,\"playlist_id\":\"$(json_escape "$playlist_id")\""
  fi

  if [ -n "$playlist_name" ]; then
    payload="$payload,\"name\":\"$(json_escape "$playlist_name")\""
  fi

  payload="$payload}"
  printf '%s' "$payload"
}

start_build() {
  csv_file="$1"
  key="$(slugify "$csv_file")"
  playlist_name="$(playlist_name_for_key "$key")"

  if [ -n "$playlist_name" ]; then
    echo "Starting build for $csv_file as playlist key: $key with name: $playlist_name"
  else
    echo "Starting build for $csv_file as playlist key: $key"
  fi

  curl -fsS \
    -X POST "$API_URL/build/$key" \
    -H "Content-Type: application/json" \
    -d "$(build_payload "$key")"
  echo
}

start_sync() {
  csv_file="$1"
  key="$(slugify "$csv_file")"
  playlist_id="$(playlist_id_for_key "$key")"
  playlist_name="$(playlist_name_for_key "$key")"

  if [ -z "$playlist_id" ]; then
    echo "Skipping sync for $csv_file as playlist key $key: no playlist_id found in $TARGETS_FILE"
    echo "Add a line like: $key,SPOTIFY_PLAYLIST_ID,Playlist Display Name"
    return 0
  fi

  if [ -n "$playlist_name" ]; then
    echo "Starting sync for $csv_file as playlist key: $key to playlist ID: $playlist_id with name: $playlist_name"
  else
    echo "Starting sync for $csv_file as playlist key: $key to playlist ID: $playlist_id"
  fi

  curl -fsS \
    -X POST "$API_URL/sync/$key" \
    -H "Content-Type: application/json" \
    -d "$(build_payload "$key" "$playlist_id")"
  echo
}

start_new_playlist() {
  csv_file="$1"
  key="$(slugify "$csv_file")"
  playlist_id="$(playlist_id_for_key "$key")"

  if [ -n "$playlist_id" ]; then
    echo "New playlist CSV $csv_file already has target $playlist_id. Syncing instead of creating a duplicate playlist."
    start_sync "$csv_file"
  else
    start_build "$csv_file"
  fi
}

# CHANGED: full resync of every configured target. Still used, but now only
# as a deliberate fallback when the sync script itself changes (we want to
# re-verify everything in that case). No longer used for ordinary
# playlist_targets.csv edits.
sync_configured_targets() {
  if [ ! -f "$TARGETS_FILE" ]; then
    return 0
  fi

  echo "Sync script changed. Re-syncing ALL configured targets to be safe."

  awk -F, '
    NR == 1 { next }
    /^[[:space:]]*#/ { next }
    {
      key = $1
      id = $2
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", id)
      if (key != "" && id != "") {
        print key
      }
    }
  ' "$TARGETS_FILE" | while IFS= read -r key; do
    [ -n "$key" ] || continue
    csv_file="$(target_csv_for_key "$key")"
    if [ -n "$csv_file" ]; then
      start_sync "$csv_file"
    else
      echo "Skipping target $key: expected CSV file not found."
    fi
  done
}

# NEW: only sync the specific keys whose row was added or changed in
# playlist_targets.csv, instead of every configured playlist. Looks at the
# '+' lines of the diff (added rows, and the "new" side of any modified row)
# and pulls out just the key column.
sync_changed_targets_only() {
  echo "Playlist target config changed. Syncing only the affected targets."

  changed_keys="$(
    git_run diff "$BEFORE" "$AFTER" -- "$TARGETS_FILE" \
      | grep -E '^\+[^+]' \
      | sed -E 's/^\+//' \
      | awk -F, '{
          key = $1
          gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
          if (key != "" && key !~ /^#/) print key
        }'
  )"

  if [ -z "$changed_keys" ]; then
    echo "Could not determine which target rows changed. Falling back to full resync."
    sync_configured_targets
    return 0
  fi

  printf '%s\n' "$changed_keys" | while IFS= read -r key; do
    [ -n "$key" ] || continue
    csv_file="$(target_csv_for_key "$key")"
    if [ -n "$csv_file" ]; then
      start_sync "$csv_file"
    else
      echo "Skipping target $key: expected CSV file not found."
    fi
  done
}

echo "Checking GitHub for Spotify playlist builder updates..."

BEFORE="$(git_run rev-parse HEAD)"

git_run fetch origin "$BRANCH"
git_run reset --hard "origin/$BRANCH"

AFTER="$(git_run rev-parse HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
  echo "No GitHub changes found."
  exit 0
fi

echo "Updated from $BEFORE to $AFTER"

CHANGED_FILES="$(git_run diff --name-only "$BEFORE" "$AFTER")"
NEW_PLAYLISTS="$(git_run diff --name-status "$BEFORE" "$AFTER" -- playlists | awk '$1 == "A" && $2 ~ /^playlists\/.*\.csv$/ {print $2}')"
UPDATED_PLAYLISTS="$(git_run diff --name-status "$BEFORE" "$AFTER" -- playlists | awk '$1 == "M" && $2 ~ /^playlists\/.*\.csv$/ {print $2}')"
TARGETS_CHANGED="$(git_run diff --name-only "$BEFORE" "$AFTER" -- "$TARGETS_FILE" | grep -F "$TARGETS_FILE" || true)"
TARGET_SYNC_CONTROL_CHANGED="$(printf '%s\n' "$CHANGED_FILES" | grep -E '^scripts/synology-sync-and-autobuild\.sh$' || true)"

if needs_container_rebuild "$CHANGED_FILES"; then
  echo "Code/config changes detected. Rebuilding Docker container..."
  rebuild_container
else
  echo "Only playlist/content changes detected. Skipping Docker rebuild so running jobs are not interrupted."
fi

wait_for_api

if [ -z "$NEW_PLAYLISTS" ] && [ -z "$UPDATED_PLAYLISTS" ]; then
  if [ -n "$TARGET_SYNC_CONTROL_CHANGED" ]; then
    sync_configured_targets
    echo "Synology sync and autobuild complete."
    exit 0
  fi

  if [ -n "$TARGETS_CHANGED" ]; then
    sync_changed_targets_only
    echo "Synology sync and autobuild complete."
    exit 0
  fi

  echo "GitHub changed, but no playlist CSVs were added or modified."
  exit 0
fi

if [ -n "$NEW_PLAYLISTS" ]; then
  echo "New playlist CSVs detected:"
  echo "$NEW_PLAYLISTS"

  echo "$NEW_PLAYLISTS" | while IFS= read -r csv_file; do
    [ -n "$csv_file" ] || continue
    start_new_playlist "$csv_file"
  done
fi

if [ -n "$UPDATED_PLAYLISTS" ]; then
  echo "Updated playlist CSVs detected:"
  echo "$UPDATED_PLAYLISTS"

  echo "$UPDATED_PLAYLISTS" | while IFS= read -r csv_file; do
    [ -n "$csv_file" ] || continue
    start_sync "$csv_file"
  done
fi

echo "Synology sync and autobuild complete."
