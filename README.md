# Spotify Playlist Builder

A self-hosted playlist-building system that turns curated CSV files into Spotify playlists, matches the best available recordings, avoids duplicates and undesirable versions, and safely updates existing playlists.

The project can be used from the command line or through its FastAPI service. The API automatically discovers playlist CSVs, maps them to existing Spotify playlist IDs, tracks job progress, and serializes all Spotify work through a single global queue to reduce rate-limit risk.

> **Current scope:** This repository is designed for one Spotify account per installation. It is under active development and is not an official Spotify product.

## Core Features

- Build a new Spotify playlist from a CSV.
- Sync missing tracks into an existing playlist without deleting or reordering existing tracks.
- Automatically reuse saved playlist IDs so an existing playlist is not accidentally recreated.
- Smart track matching using title, artist, album, release information, popularity, and recording metadata.
- Reject or heavily penalize live, karaoke, tribute, compilation, and other unwanted versions.
- Detect duplicate Spotify URIs and duplicate recordings.
- Generate detailed reports for matches, misses, duplicates, and additions.
- Discover playlist CSVs automatically from `playlists/`.
- Manage known Spotify playlist IDs and display names through `config/playlist_targets.csv`.
- Expose build, sync, status, progress, logs, and cancellation through FastAPI.
- Allow browser-based dashboards and other integrations through CORS.
- Protect Spotify from concurrent bursts with a global FIFO queue:
  - one Spotify job runs at a time;
  - additional jobs wait in `queued` state;
  - duplicate running or queued jobs are rejected;
  - a 10-second cooldown is applied between jobs.
- Pace Spotify searches and honor Spotify search rate-limit retry delays.
- Support Docker Compose and Synology Git-sync/autobuild workflows.

## CSV Format

Every playlist CSV must use exactly these columns:

```csv
Title,Artist,Album,Year
Round and Round,Ratt,Out of the Cellar,1984
Tooth and Nail,Dokken,Tooth and Nail,1984
Looks That Kill,Mötley Crüe,Shout at the Devil,1983
```

Store playlist files under `playlists/` using snake_case names, for example:

```text
playlists/open_road_anthems.csv
```

The API converts the filename to a playlist key:

```text
open-road-anthems
```

## Spotify Developer Setup

1. Create an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Add the redirect URI used in `.env`. The default is:

   ```text
   http://127.0.0.1:8888/callback
   ```

3. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

4. Add your own Spotify credentials to `.env`:

   ```dotenv
   SPOTIFY_CLIENT_ID=your_spotify_client_id
   SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
   SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
   SPOTIFY_PUBLIC=false
   ```

The redirect URI in Spotify must exactly match the value in `.env`. Keep the client secret private and never commit `.env`.

## Docker Quick Start

```bash
git clone https://github.com/ebarber972/spotify-playlist-builder.git
cd spotify-playlist-builder
cp .env.example .env
# Edit .env with your Spotify credentials
docker compose up -d --build
```

The API listens on port `5150`:

```bash
curl http://127.0.0.1:5150/health
```

Expected response:

```json
{"status":"ok","jobs":0}
```

Interactive FastAPI documentation is available at:

```text
http://127.0.0.1:5150/docs
```

## Native Python Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

## Command-Line Usage

### Dry run

Search, match, and write reports without changing Spotify:

```bash
python playlist_builder.py build playlists/open_road_anthems.csv \
  --name "Open Road Anthems" \
  --dry-run
```

### Build a new playlist

```bash
python playlist_builder.py build playlists/open_road_anthems.csv \
  --name "Open Road Anthems"
```

### Add missing tracks to an existing playlist

```bash
python playlist_builder.py sync playlists/open_road_anthems.csv \
  --playlist-id YOUR_PLAYLIST_ID \
  --name "Open Road Anthems"
```

Useful options include:

```text
--artist-gap 10
--search-limit 50
--allow-live
--no-remasters
--limit 50
--dry-run
--report-dir reports
```

## Playlist Target Map

Known Spotify playlists are stored in:

```text
config/playlist_targets.csv
```

Format:

```csv
playlist_key,playlist_id,playlist_name
open-road-anthems,SPOTIFY_PLAYLIST_ID,Open Road Anthems
```

When a saved ID exists, both API build and sync requests automatically use it unless the caller explicitly supplies a different ID. This prevents the Build endpoint from creating a duplicate playlist for an existing target.

## API Endpoints

### Discovery and status

```text
GET /health
GET /playlists
GET /jobs
GET /jobs/{job_id}
```

### Start work

```text
POST /build/{playlist_key}
POST /sync/{playlist_key}
```

Example:

```bash
curl -X POST http://127.0.0.1:5150/sync/open-road-anthems \
  -H "Content-Type: application/json" \
  -d '{"dry_run":false,"search_limit":50}'
```

A submitted job normally returns `queued`. The single queue worker starts jobs in order and runs only one Spotify-hitting subprocess at a time.

### Cancel a job

```text
POST /jobs/{job_id}/kill
```

Queued jobs can be cancelled before they start. Running jobs are terminated.

## Synology Automation

The repository includes:

```text
scripts/synology-sync-and-autobuild.sh
```

The script:

- fetches and hard-resets the local checkout to `main`;
- rebuilds the Docker container only when application code or container configuration changes;
- skips rebuilds for playlist-only changes so active jobs are not interrupted;
- builds newly added playlist CSVs;
- syncs modified playlist CSVs;
- syncs only affected targets when `config/playlist_targets.csv` changes;
- avoids creating a duplicate when a new CSV already has a saved playlist ID.

Example installation-specific invocation:

```bash
sudo /usr/local/sbin/spotify-playlist-git-sync
```

Host-side wrappers such as `plsync` are installation-specific and are not currently maintained in this repository.

## Reports

Reports are written under `reports/` by default:

```text
reports/build_report.csv
reports/misses.csv
reports/added_tracks.csv
reports/duplicates.csv
```

The reports include matching status, selected Spotify recording details, album type, ISRC, duration, popularity, and match reasoning.

## Current Safety Behavior

- All API-submitted Spotify jobs are serialized globally.
- The queue is in memory and resets when the API container restarts.
- Job history is also in memory and resets on restart.
- Search calls are paced and retried with backoff when Spotify returns HTTP 429.
- Sync is additive only: it adds missing tracks and can rename the playlist, but it does not remove or reorder tracks.
- The API currently has no authentication and CORS allows browser clients. Keep port `5150` on a trusted network unless authentication, HTTPS, and restricted origins are added.
- Direct command-line executions bypass the API queue. Avoid running multiple CLI builds or syncs simultaneously.

## Security

- Never commit `.env`, Spotify tokens, client secrets, or token-cache files.
- `.env` and `.spotify_token_cache` are excluded by `.gitignore`.
- Use `.env.example` only as a template.
- Rotate a Spotify client secret immediately if it has ever appeared in Git history, logs, screenshots, or chat messages.

## Project Status

The current installation is functional for local playlist creation and additive synchronization. Planned hardening includes:

- a process-level lock for direct CLI execution;
- shared retry handling for all Spotify operations, not only searches;
- persistent queue and job history;
- authentication and restricted CORS for deployments outside a trusted LAN;
- a future packaged edition that lets each user connect their own Spotify account.
