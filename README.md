# Spotify Playlist Builder v2

A local Spotify playlist builder that imports a CSV, searches Spotify, picks the best matching track, avoids junk versions, creates a playlist, adds tracks in order, and writes detailed reports.

## What's New in v2

- Album type awareness: album, single, compilation.
- ISRC capture for duplicate/version detection.
- Duration-based duplicate fingerprinting.
- Better original-album preference.
- Compilation/greatest-hits penalties.
- Live/karaoke/tribute rejection or heavy penalties.
- Better reporting with album type, ISRC, duration, popularity, and scoring reason.
- Existing playlist duplicate detection if you pass `--playlist-id`.

## CSV Format

```csv
Title,Artist
Round and Round,Ratt
Tooth and Nail,Dokken
Looks That Kill,Mötley Crüe
```

Optional columns:

```csv
Title,Artist,Album,Year
```

## Setup

```powershell
cd spotify-playlist-builder
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and add your Spotify developer app credentials.

Spotify redirect URI:

```text
http://127.0.0.1:8888/callback
```

## Dry Run

```powershell
python playlist_builder.py build playlists\hair_metal_starter.csv --name "Spotify API Test Playlist" --dry-run
```

## Create Playlist

```powershell
python playlist_builder.py build playlists\hair_metal_starter.csv --name "Spotify API Test Playlist"
```

## Add to Existing Playlist

```powershell
python playlist_builder.py build playlists\hair_metal_starter.csv --name "Ignored When Using Playlist ID" --playlist-id YOUR_PLAYLIST_ID
```

## Helpful Options

```powershell
--artist-gap 10
--search-limit 20
--allow-live
--no-remasters
--limit 50
--dry-run
```

## Reports

```text
reports/build_report.csv
reports/misses.csv
reports/added_tracks.csv
reports/duplicates.csv
```
