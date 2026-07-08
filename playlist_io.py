import csv
from pathlib import Path
from models import InputTrack, MatchResult
from matcher import duplicate_key

def read_input_csv(path: str) -> list[InputTrack]:
    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = {c.lower().strip(): c for c in reader.fieldnames or []}

        title_col = columns.get("title") or columns.get("song") or columns.get("track")
        artist_col = columns.get("artist") or columns.get("band")
        album_col = columns.get("album")
        year_col = columns.get("year")

        if not title_col or not artist_col:
            raise ValueError("CSV must include Title and Artist columns.")

        for i, row in enumerate(reader, start=2):
            title = (row.get(title_col) or "").strip()
            artist = (row.get(artist_col) or "").strip()
            if not title or not artist:
                continue
            rows.append(InputTrack(
                title=title,
                artist=artist,
                album=(row.get(album_col) or "").strip() if album_col else "",
                year=(row.get(year_col) or "").strip() if year_col else "",
                row_number=i,
            ))
    return rows

def write_reports(results: list[MatchResult], report_dir: str = "reports") -> None:
    Path(report_dir).mkdir(exist_ok=True)

    with open(Path(report_dir) / "build_report.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "InputTitle","InputArtist","InputAlbum","InputYear",
            "Status","Score","Reason",
            "SpotifyTitle","SpotifyArtist","SpotifyAlbum","AlbumType","ReleaseDate",
            "DurationMs","Popularity","ISRC","DuplicateKey","URI"
        ])
        for r in results:
            t = r.spotify_track
            w.writerow([
                r.input_track.title,
                r.input_track.artist,
                r.input_track.album,
                r.input_track.year,
                r.status,
                r.score,
                r.reason,
                t.title if t else "",
                t.artist if t else "",
                t.album if t else "",
                t.album_type if t else "",
                t.release_date if t else "",
                t.duration_ms if t else "",
                t.popularity if t else "",
                t.isrc if t else "",
                duplicate_key(t) if t else "",
                t.uri if t else "",
            ])

    with open(Path(report_dir) / "misses.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Title","Artist","Reason","Score"])
        for r in results:
            if r.status in ("NO_MATCH", "REJECTED"):
                w.writerow([r.input_track.title, r.input_track.artist, r.reason, r.score])

    with open(Path(report_dir) / "added_tracks.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Title","Artist","SpotifyTitle","SpotifyArtist","Album","AlbumType","ReleaseDate","ISRC","URI"])
        for r in results:
            if r.status == "MATCH" and r.spotify_track:
                t = r.spotify_track
                w.writerow([r.input_track.title, r.input_track.artist, t.title, t.artist, t.album, t.album_type, t.release_date, t.isrc, t.uri])

    with open(Path(report_dir) / "duplicates.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Title","Artist","SpotifyTitle","SpotifyArtist","Album","DuplicateKey","URI","Reason"])
        for r in results:
            if r.status == "DUPLICATE" and r.spotify_track:
                t = r.spotify_track
                w.writerow([r.input_track.title, r.input_track.artist, t.title, t.artist, t.album, duplicate_key(t), t.uri, r.reason])
