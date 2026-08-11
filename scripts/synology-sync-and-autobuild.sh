#!/bin/sh
set -eu

APP_DIR="${APP_DIR:-/volume1/docker/spotify-playlist-builder}"
BRANCH="${BRANCH:-main}"
API_URL="${API_URL:-http://127.0.0.1:5150}"
DRY_RUN="${DRY_RUN:-false}"
SEARCH_LIMIT="${SEARCH_LIMIT:-50}"
TARGETS_FILE="${TARGETS_FILE:-config/playlist_targets.csv}"
SUDO="${SUDO-sudo -n}"
JOB_POLL_SECONDS="${JOB_POLL_SECONDS:-5}"
JOB_POLL_LIMIT="${JOB_POLL_LIMIT:-2880}"

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

humanize_key() {
  printf '%s\n' "$1" | awk -F- '{
    for (i = 1; i <= NF; i++) {
      word = $i
      if (word ~ /^[0-9]+s$/) {
        out = out (out ? " " : "") word
      } else if (word == "rnb") {
        out = out (out ? " " : "") "R&B"
      } else if (word == "roq" || word == "kroq") {
        out = out (out ? " " : "") toupper(word)
      } else {
        out = out (out ? " " : "") toupper(substr(word, 1, 1)) substr(word, 2)
      }
    }
    print out
  }'
}

playlist_id_for_key() {
  key="$1"
  [ -f "$TARGETS_FILE" ] || return 0

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
  [ -f "$TARGETS_FILE" ] || return 0

  awk -F, -v key="$key" '
    NR == 1 { next }
    /^[[:space:]]*#/ { next }
    $1 == key {
      name = $3
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      print name
      exit
    }
  ' "$TARGETS_FILE"
}

default_playlist_name_for_key() {
  printf '%s | The Sony Walkman Session\n' "$(humanize_key "$1")"
}

target_csv_for_key() {
  key="$1"
  configured_csv=""
  if [ -f "$TARGETS_FILE" ]; then
    configured_csv="$(awk -F, -v key="$key" '
      NR == 1 { next }
      /^[[:space:]]*#/ { next }
      $1 == key {
        value = $4
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        print value
        exit
      }
    ' "$TARGETS_FILE")"
  fi
  if [ -n "$configured_csv" ]; then
    [ -f "$configured_csv" ] && printf '%s\n' "$configured_csv"
    return
  fi
  candidate="playlists/$(printf '%s' "$key" | tr '-' '_').csv"
  [ -f "$candidate" ] && printf '%s\n' "$candidate"
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
  [ -n "$playlist_name" ] || playlist_name="$(default_playlist_name_for_key "$key")"

  payload="{\"dry_run\":$DRY_RUN,\"search_limit\":$SEARCH_LIMIT"
  [ -z "$playlist_id" ] || payload="$payload,\"playlist_id\":\"$(json_escape "$playlist_id")\""
  [ -z "$playlist_name" ] || payload="$payload,\"name\":\"$(json_escape "$playlist_name")\""
  payload="$payload}"
  printf '%s' "$payload"
}

extract_json_string() {
  field="$1"
  sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p"
}

wait_for_job() {
  job_id="$1"
  count=0

  while [ "$count" -lt "$JOB_POLL_LIMIT" ]; do
    job_json="$(curl -fsS "$API_URL/jobs/$job_id?include_output=true")"
    status="$(printf '%s' "$job_json" | extract_json_string status)"

    case "$status" in
      completed)
        printf '%s' "$job_json"
        return 0
        ;;
      failed|killed)
        echo "Playlist job $job_id ended with status: $status" >&2
        printf '%s\n' "$job_json" >&2
        return 1
        ;;
    esac

    count=$((count + 1))
    sleep "$JOB_POLL_SECONDS"
  done

  echo "Timed out waiting for playlist job $job_id" >&2
  return 1
}

save_playlist_target() {
  key="$1"
  playlist_id="$2"
  playlist_name="$3"

  existing_id="$(playlist_id_for_key "$key")"
  if [ -n "$existing_id" ]; then
    echo "Playlist target already exists for $key: $existing_id"
    return 0
  fi

  mkdir -p "$(dirname "$TARGETS_FILE")"
  if [ ! -f "$TARGETS_FILE" ]; then
    printf 'playlist_key,playlist_id,playlist_name,csv_file\n' > "$TARGETS_FILE"
  fi

  printf '%s,%s,%s\n' "$key" "$playlist_id" "$playlist_name" >> "$TARGETS_FILE"
  echo "Saved Spotify playlist target: $key -> $playlist_id"

  git_run config user.name "Spotify Playlist Builder"
  git_run config user.email "spotify-playlist-builder@local"
  git_run add "$TARGETS_FILE"

  if git_run diff --cached --quiet; then
    echo "No target-map changes to commit."
    return 0
  fi

  git_run commit -m "Save Spotify playlist ID for $playlist_name"
  git_run push origin "$BRANCH"
  echo "Committed and pushed the new Spotify playlist ID to GitHub."
}

start_build() {
  csv_file="$1"
  key="$(slugify "$csv_file")"
  playlist_name="$(playlist_name_for_key "$key")"
  [ -n "$playlist_name" ] || playlist_name="$(default_playlist_name_for_key "$key")"

  echo "Starting build for $csv_file as playlist key: $key with name: $playlist_name"
  response="$(curl -fsS \
    -X POST "$API_URL/build/$key" \
    -H "Content-Type: application/json" \
    -d "$(build_payload "$key")")"
  echo "$response"

  job_id="$(printf '%s' "$response" | extract_json_string job_id)"
  if [ -z "$job_id" ]; then
    echo "Build request did not return a job_id; cannot auto-detect playlist ID." >&2
    return 1
  fi

  job_json="$(wait_for_job "$job_id")"
  playlist_id="$(printf '%s' "$job_json" | grep -oE '[A-Za-z0-9]{22}' | tail -n 1 || true)"

  if [ -z "$playlist_id" ]; then
    echo "Build completed but no Spotify playlist ID was found in the job output." >&2
    printf '%s\n' "$job_json" >&2
    return 1
  fi

  echo "Detected new Spotify playlist ID: $playlist_id"
  save_playlist_target "$key" "$playlist_id" "$playlist_name"
}

start_sync() {
  csv_file="$1"
  key="$(slugify "$csv_file")"
  playlist_id="$(playlist_id_for_key "$key")"
  playlist_name="$(playlist_name_for_key "$key")"

  if [ -z "$playlist_id" ]; then
    echo "Skipping sync for $csv_file as playlist key $key: no playlist_id found in $TARGETS_FILE"
    return 0
  fi

  echo "Starting sync for $csv_file as playlist key: $key to playlist ID: $playlist_id"
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

sync_configured_targets() {
  [ -f "$TARGETS_FILE" ] || return 0

  awk -F, '
    NR == 1 { next }
    /^[[:space:]]*#/ { next }
    {
      key = $1; id = $2
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", id)
      if (key != "" && id != "") print key
    }
  ' "$TARGETS_FILE" | while IFS= read -r key; do
    csv_file="$(target_csv_for_key "$key")"
    [ -z "$csv_file" ] || start_sync "$csv_file"
  done
}

sync_changed_targets_only() {
  changed_keys="$(
    git_run diff "$BEFORE" "$AFTER" -- "$TARGETS_FILE" \
      | grep -E '^\+[^+]' \
      | sed -E 's/^\+//' \
      | awk -F, '{
          key = $1
          gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
          if (key != "" && key !~ /^#/ && key != "playlist_key") print key
        }'
  )"

  if [ -z "$changed_keys" ]; then
    sync_configured_targets
    return 0
  fi

  printf '%s\n' "$changed_keys" | while IFS= read -r key; do
    csv_file="$(target_csv_for_key "$key")"
    [ -z "$csv_file" ] || start_sync "$csv_file"
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

CHANGED_FILES="$(git_run diff --name-only "$BEFORE" "$AFTER")"
NEW_PLAYLISTS="$(git_run diff --name-status "$BEFORE" "$AFTER" -- playlists | awk '$1 == "A" && $2 ~ /^playlists\/.*\.csv$/ {print $2}')"
UPDATED_PLAYLISTS="$(git_run diff --name-status "$BEFORE" "$AFTER" -- playlists | awk '$1 == "M" && $2 ~ /^playlists\/.*\.csv$/ {print $2}')"
TARGETS_CHANGED="$(git_run diff --name-only "$BEFORE" "$AFTER" -- "$TARGETS_FILE" | grep -F "$TARGETS_FILE" || true)"
SCRIPT_CHANGED="$(printf '%s\n' "$CHANGED_FILES" | grep -E '^scripts/synology-sync-and-autobuild\.sh$' || true)"

if needs_container_rebuild "$CHANGED_FILES"; then
  echo "Code/config changes detected. Rebuilding Docker container..."
  rebuild_container
else
  echo "Only playlist/content changes detected. Skipping Docker rebuild."
fi

wait_for_api

if [ -z "$NEW_PLAYLISTS" ] && [ -z "$UPDATED_PLAYLISTS" ]; then
  echo "No playlist CSVs were added or modified. Nothing will be synced."
  exit 0
fi

if [ -n "$NEW_PLAYLISTS" ]; then
  echo "New playlist CSVs detected:"
  echo "$NEW_PLAYLISTS"
  echo "$NEW_PLAYLISTS" | while IFS= read -r csv_file; do
    [ -z "$csv_file" ] || start_new_playlist "$csv_file"
  done
fi

if [ -n "$UPDATED_PLAYLISTS" ]; then
  echo "Updated playlist CSVs detected:"
  echo "$UPDATED_PLAYLISTS"
  echo "$UPDATED_PLAYLISTS" | while IFS= read -r csv_file; do
    # If a prior first-build event was missed, an updated orphaned CSV should
    # be built now instead of being skipped for not having a playlist ID.
    [ -z "$csv_file" ] || start_new_playlist "$csv_file"
  done
fi

echo "Synology sync and autobuild complete."
