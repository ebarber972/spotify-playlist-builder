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

needs_container_rebuild() {
  for changed_file in $1; do
    case "$changed_file" in
      Dockerfile|docker-compose.yml|requirements.txt|api_server.py|playlist_builder.py|scripts/*)
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

start_build() {
  csv_file="$1"
  key="$(slugify "$csv_file")"

  echo "Starting build for $csv_file as playlist key: $key"
  curl -fsS \
    -X POST "$API_URL/build/$key" \
    -H "Content-Type: application/json" \
    -d "{\"dry_run\":$DRY_RUN,\"search_limit\":$SEARCH_LIMIT}"
  echo
}

start_sync() {
  csv_file="$1"
  key="$(slugify "$csv_file")"
  playlist_id="$(playlist_id_for_key "$key")"

  if [ -z "$playlist_id" ]; then
    echo "Skipping sync for $csv_file as playlist key $key: no playlist_id found in $TARGETS_FILE"
    echo "Add a line like: $key,SPOTIFY_PLAYLIST_ID"
    return 0
  fi

  echo "Starting sync for $csv_file as playlist key: $key to playlist ID: $playlist_id"
  curl -fsS \
    -X POST "$API_URL/sync/$key" \
    -H "Content-Type: application/json" \
    -d "{\"dry_run\":$DRY_RUN,\"search_limit\":$SEARCH_LIMIT,\"playlist_id\":\"$playlist_id\"}"
  echo
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

if needs_container_rebuild "$CHANGED_FILES"; then
  echo "Code/config changes detected. Rebuilding Docker container..."
  rebuild_container
else
  echo "Only playlist/content changes detected. Skipping Docker rebuild so running jobs are not interrupted."
fi

wait_for_api

if [ -z "$NEW_PLAYLISTS" ] && [ -z "$UPDATED_PLAYLISTS" ]; then
  echo "GitHub changed, but no playlist CSVs were added or modified."
  exit 0
fi

if [ -n "$NEW_PLAYLISTS" ]; then
  echo "New playlist CSVs detected:"
  echo "$NEW_PLAYLISTS"

  echo "$NEW_PLAYLISTS" | while IFS= read -r csv_file; do
    [ -n "$csv_file" ] || continue
    start_build "$csv_file"
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
