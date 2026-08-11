#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/dist/spotify-playlist-builder-template}"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

copy_path() {
  src="$1"
  dest="$OUT_DIR/$src"
  if [ -d "$ROOT_DIR/$src" ]; then
    mkdir -p "$dest"
    (cd "$ROOT_DIR/$src" && tar \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      --exclude='.pytest_cache' \
      -cf - .) | (cd "$dest" && tar -xf -)
  elif [ -f "$ROOT_DIR/$src" ]; then
    mkdir -p "$(dirname "$dest")"
    cp "$ROOT_DIR/$src" "$dest"
  fi
}

# Core portable application files. Do not copy host-specific deployment wrappers.
for path in \
  api_server.py \
  config.py \
  docker-compose.yml \
  Dockerfile \
  matcher.py \
  playlist_builder.py \
  playlist_io.py \
  reorder.py \
  requirements.txt \
  spotify_client.py \
  .env.example \
  .gitignore
 do
  copy_path "$path"
done

mkdir -p "$OUT_DIR/docs" "$OUT_DIR/playlists" "$OUT_DIR/config"
copy_path "docs/shareable-template.md"

# Replace personal playlist data with safe samples.
cat > "$OUT_DIR/playlists/example_open_road.csv" <<'CSV'
Title,Artist,Album,Year
Take On Me,a-ha,Hunting High and Low,1985
Girls Just Want to Have Fun,Cyndi Lauper,She's So Unusual,1983
Walking on Sunshine,Katrina and the Waves,Katrina and the Waves,1985
Don't You (Forget About Me),Simple Minds,The Breakfast Club,1985
Everybody Wants to Rule the World,Tears for Fears,Songs from the Big Chair,1985
CSV

cat > "$OUT_DIR/config/playlist_targets.csv" <<'CSV'
playlist_key,playlist_id,playlist_name
example-open-road,,Example Open Road Playlist
CSV

cat > "$OUT_DIR/README.md" <<'MD'
# Spotify Playlist Builder Template

This is a clean, platform-neutral Spotify Playlist Builder package. It does not assume Portainer, Proxmox, Synology, a NAS, n8n, or any specific hosting stack.

It only assumes the recipient has Docker Compose available on one machine, such as:

- Windows or macOS with Docker Desktop
- Linux with Docker Engine and the Docker Compose plugin
- any server/VM/container host that can run Docker Compose

The package does not include the original owner's Spotify credentials, token cache, personal playlists, reports, or Spotify playlist IDs.

## 1. Create a Spotify Developer app

Create an app in the Spotify Developer Dashboard and add this redirect URI:

```text
http://127.0.0.1:8888/callback
```

## 2. Configure credentials

Copy the example file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
copy .env.example .env
```

Edit `.env` and add your own Spotify Developer app credentials:

```dotenv
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
SPOTIFY_PUBLIC=false
```

Do not share or commit `.env`.

## 3. Start the app

```bash
docker compose up -d --build
```

Check it:

```bash
curl http://127.0.0.1:5150/health
```

Open API docs:

```text
http://127.0.0.1:5150/docs
```

## 4. Build the sample playlist

```bash
curl -X POST http://127.0.0.1:5150/build/example-open-road \
  -H "Content-Type: application/json" \
  -d '{"dry_run":false,"search_limit":50}'
```

The first Spotify action will require the user to authorize their own Spotify account.

## 5. Add your own playlist

Create a CSV under `playlists/` with exactly this header:

```csv
Title,Artist,Album,Year
```

Example:

```csv
Title,Artist,Album,Year
Take On Me,a-ha,Hunting High and Low,1985
Girls Just Want to Have Fun,Cyndi Lauper,She's So Unusual,1983
```

Save it as:

```text
playlists/my_party_mix.csv
```

That becomes playlist key:

```text
my-party-mix
```

Build it:

```bash
curl -X POST http://127.0.0.1:5150/build/my-party-mix \
  -H "Content-Type: application/json" \
  -d '{"dry_run":false,"search_limit":50}'
```

## 6. Update the same Spotify playlist later

After Spotify creates a playlist, copy its playlist ID into `config/playlist_targets.csv`:

```csv
playlist_key,playlist_id,playlist_name
my-party-mix,SPOTIFY_PLAYLIST_ID,My Party Mix
```

Then sync missing tracks into the same playlist:

```bash
curl -X POST http://127.0.0.1:5150/sync/my-party-mix \
  -H "Content-Type: application/json" \
  -d '{"dry_run":false,"search_limit":50}'
```

## Safety notes

- One installation is intended for one Spotify account.
- Sync is additive: it can add missing tracks and rename a playlist, but it does not remove tracks or reorder Spotify playlists.
- The API has no authentication yet. Keep port `5150` on a trusted network unless authentication and restricted CORS are added.
- Do not share `.env`, `.spotify_token_cache`, logs with secrets, screenshots containing secrets, or real playlist IDs unless intended.
MD

cat > "$OUT_DIR/QUICKSTART.md" <<'MD'
# Quickstart

This template is platform-neutral. It does not require Portainer, Proxmox, Synology, a NAS, or n8n.

## Requirements

- Docker Compose
- A Spotify account
- A Spotify Developer app with redirect URI: `http://127.0.0.1:8888/callback`

## Start

```bash
cp .env.example .env
# edit .env with your own Spotify credentials
docker compose up -d --build
curl http://127.0.0.1:5150/health
```

On Windows PowerShell, use:

```powershell
copy .env.example .env
```

## Build sample playlist

```bash
curl -X POST http://127.0.0.1:5150/build/example-open-road \
  -H "Content-Type: application/json" \
  -d '{"dry_run":false,"search_limit":50}'
```

## CSV format

Every playlist CSV must use exactly:

```csv
Title,Artist,Album,Year
```

Put CSV files under `playlists/`.
MD

cat > "$OUT_DIR/.template-export-notes.txt" <<'TXT'
Generated by scripts/export-shareable-template.sh.
Review this folder before sharing.
This clean export intentionally excludes Portainer, Proxmox, Synology/NAS, n8n, and other host-specific assumptions.
Do not copy real .env files, token caches, private reports, personal playlists, or personal playlist IDs into this package.
TXT

printf 'Created clean platform-neutral template package: %s\n' "$OUT_DIR"
printf 'Review it, then zip or publish that folder as a separate template repo.\n'
