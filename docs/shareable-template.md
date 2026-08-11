# Shareable Template Packaging Guide

This project can be shared as a clean self-hosted Spotify playlist builder, but the shared copy must not include personal playlist CSVs, Spotify playlist IDs, token caches, reports, or private environment files.

Use `scripts/export-shareable-template.sh` to create a clean package under `dist/spotify-playlist-builder-template/`.

## What the package includes

- The playlist builder application code.
- Docker Compose support.
- `.env.example` for each user's own Spotify Developer app credentials.
- A sample playlist CSV in `playlists/example_open_road.csv`.
- A blank `config/playlist_targets.csv` with the required header.
- Documentation explaining setup, CSV format, build, sync, and safety behavior.

## What the package excludes

- `.git/` history.
- `.env` and any real Spotify credentials.
- `.spotify_token_cache`.
- `reports/`, `cache/`, virtual environments, and Python caches.
- Eric's personal playlist CSVs.
- Eric's `config/playlist_targets.csv` playlist IDs.

## Create the clean package

From the repository root:

```bash
sh scripts/export-shareable-template.sh
```

The script creates:

```text
dist/spotify-playlist-builder-template/
```

You can zip that folder or push it to a separate public/template repository.

## Recommended sharing flow

1. Run the export script.
2. Review `dist/spotify-playlist-builder-template/` before sharing.
3. Zip that folder or publish it as a separate clean GitHub template repository.
4. Tell the recipient to create their own Spotify Developer app and fill in `.env` from `.env.example`.
5. Have them add CSVs under `playlists/` using exactly:

   ```csv
   Title,Artist,Album,Year
   ```

6. They run Docker Compose and build or sync their own playlists.

## Recipient setup summary

```bash
cp .env.example .env
# edit .env with their Spotify credentials
docker compose up -d --build
curl http://127.0.0.1:5150/health
```

Then build the sample playlist:

```bash
curl -X POST http://127.0.0.1:5150/build/example-open-road \
  -H "Content-Type: application/json" \
  -d '{"dry_run":false,"search_limit":50}'
```

After Spotify creates a playlist, save its ID in `config/playlist_targets.csv` if they want future syncs to update that same playlist instead of creating a new one.

## Safety notes

- One installation is intended for one Spotify account.
- Sync is additive: it can add missing tracks and rename a playlist, but it does not remove tracks or reorder Spotify playlists.
- The API has no authentication yet. Keep port `5150` on a trusted network unless authentication and restricted CORS are added.
- Do not share `.env`, `.spotify_token_cache`, logs with secrets, screenshots containing secrets, or real playlist IDs unless intended.
