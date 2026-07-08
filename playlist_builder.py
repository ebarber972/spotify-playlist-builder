import argparse
from rich.console import Console
from rich.table import Table

from config import get_config
from spotify_client import SpotifyClient
from matcher import pick_best_match, duplicate_key
from playlist_io import read_input_csv, write_reports
from reorder import space_artists

console = Console()

def parse_bool(value: str) -> bool:
    return str(value).lower() in ("1", "true", "yes", "y")

def build(args):
    tracks = read_input_csv(args.csv)
    if args.artist_gap and args.artist_gap > 0:
        tracks = space_artists(tracks, artist_gap=args.artist_gap)

    if args.limit:
        tracks = tracks[:args.limit]

    console.print(f"[bold]Loaded {len(tracks)} tracks from CSV[/bold]")

    config = get_config()
    client = SpotifyClient(config)

    results = []
    uris = []
    seen_uris = set()
    seen_recordings = set()

    if args.playlist_id:
        existing_uris = client.current_playlist_tracks(args.playlist_id)
        seen_uris.update(existing_uris)
        console.print(f"[bold]Loaded {len(existing_uris)} existing playlist URIs[/bold]")

    for idx, wanted in enumerate(tracks, start=1):
        console.print(f"[cyan]{idx}/{len(tracks)}[/cyan] Searching: {wanted.title} - {wanted.artist}")
        candidates = client.search_tracks(wanted.title, wanted.artist, limit=args.search_limit)
        match = pick_best_match(
            wanted,
            candidates,
            allow_live=args.allow_live,
            allow_remasters=not args.no_remasters,
        )

        if match.status == "MATCH" and match.spotify_track:
            uri = match.spotify_track.uri
            rec_key = duplicate_key(match.spotify_track)

            if uri in seen_uris:
                match.status = "DUPLICATE"
                match.reason = "duplicate URI already selected or already in playlist"
            elif rec_key in seen_recordings:
                match.status = "DUPLICATE"
                match.reason = f"duplicate recording already selected: {rec_key}"
            else:
                seen_uris.add(uri)
                seen_recordings.add(rec_key)
                uris.append(uri)

        results.append(match)

    write_reports(results, args.report_dir)

    matched = sum(1 for r in results if r.status == "MATCH")
    missed = sum(1 for r in results if r.status == "NO_MATCH")
    duplicates = sum(1 for r in results if r.status == "DUPLICATE")
    rejected = sum(1 for r in results if r.status == "REJECTED")

    table = Table(title="Build Summary")
    table.add_column("Result")
    table.add_column("Count", justify="right")
    table.add_row("Matched", str(matched))
    table.add_row("Missed", str(missed))
    table.add_row("Duplicates", str(duplicates))
    table.add_row("Rejected", str(rejected))
    table.add_row("URIs ready to add", str(len(uris)))
    console.print(table)

    if args.dry_run:
        console.print("[yellow]Dry run only. No playlist created or changed.[/yellow]")
        console.print(f"Reports written to: {args.report_dir}")
        return

    if args.playlist_id:
        playlist_id = args.playlist_id
        console.print(f"[green]Adding to existing playlist:[/green] {playlist_id}")
    else:
        public = parse_bool(args.public) if args.public is not None else config.default_public
        playlist_id = client.create_playlist(
            name=args.name,
            description=args.description,
            public=public,
        )
        console.print(f"[green]Created playlist:[/green] {playlist_id}")

    client.add_tracks(playlist_id, uris)

    console.print(f"[green]Added {len(uris)} tracks.[/green]")
    console.print(f"[green]Playlist ID:[/green] {playlist_id}")
    console.print(f"Reports written to: {args.report_dir}")

def main():
    parser = argparse.ArgumentParser(description="Build exact Spotify playlists from CSV files.")
    sub = parser.add_subparsers(dest="command", required=True)

    build_parser = sub.add_parser("build", help="Build a Spotify playlist from a CSV.")
    build_parser.add_argument("csv", help="Input CSV path with Title and Artist columns.")
    build_parser.add_argument("--name", required=True, help="Spotify playlist name.")
    build_parser.add_argument("--description", default="Built with Spotify Playlist Builder v2.")
    build_parser.add_argument("--playlist-id", default="", help="Existing Spotify playlist ID to add tracks to instead of creating a new playlist.")
    build_parser.add_argument("--public", default=None, help="true or false. Defaults to SPOTIFY_PUBLIC.")
    build_parser.add_argument("--dry-run", action="store_true", help="Search and report only. Do not create or modify playlist.")
    build_parser.add_argument("--allow-live", action="store_true", help="Allow live versions.")
    build_parser.add_argument("--no-remasters", action="store_true", help="Penalize remastered versions more heavily.")
    build_parser.add_argument("--artist-gap", type=int, default=10, help="Try to keep same artist this many songs apart.")
    build_parser.add_argument("--limit", type=int, default=0, help="Only process first N tracks.")
    build_parser.add_argument("--search-limit", type=int, default=20, help="Spotify candidates per track.")
    build_parser.add_argument("--report-dir", default="reports")

    args = parser.parse_args()

    if args.command == "build":
        build(args)

if __name__ == "__main__":
    main()
