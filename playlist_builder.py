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


def resolve_tracks(args, client: SpotifyClient):
    tracks = read_input_csv(args.csv)
    if getattr(args, "artist_gap", 0) and args.artist_gap > 0:
        tracks = space_artists(tracks, artist_gap=args.artist_gap)

    if getattr(args, "limit", 0):
        tracks = tracks[:args.limit]

    console.print(f"[bold]Loaded {len(tracks)} tracks from CSV[/bold]")

    results = []
    uris = []
    seen_uris = set()
    seen_recordings = set()

    existing_uris = set()
    if getattr(args, "playlist_id", ""):
        existing_uris = client.current_playlist_tracks(args.playlist_id)
        seen_uris.update(existing_uris)
        console.print(f"[bold]Loaded {len(existing_uris)} existing playlist URIs[/bold]")

    for idx, wanted in enumerate(tracks, start=1):
        console.print(f"[cyan]{idx}/{len(tracks)}[/cyan] Searching: {wanted.title} - {wanted.artist}")
        candidates = client.search_tracks(
            wanted.title,
            wanted.artist,
            album=wanted.album,
            limit=args.search_limit,
        )
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

    return results, uris


def print_summary(results, uris, title="Build Summary"):
    matched = sum(1 for r in results if r.status == "MATCH")
    missed = sum(1 for r in results if r.status == "NO_MATCH")
    duplicates = sum(1 for r in results if r.status == "DUPLICATE")
    rejected = sum(1 for r in results if r.status == "REJECTED")

    table = Table(title=title)
    table.add_column("Result")
    table.add_column("Count", justify="right")
    table.add_row("Matched", str(matched))
    table.add_row("Missed", str(missed))
    table.add_row("Duplicates", str(duplicates))
    table.add_row("Rejected", str(rejected))
    table.add_row("URIs ready to add", str(len(uris)))
    console.print(table)


def build(args):
    config = get_config()
    client = SpotifyClient(config)

    results, uris = resolve_tracks(args, client)
    write_reports(results, args.report_dir)
    print_summary(results, uris)

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


def sync(args):
    """
    Safe v1.1 sync: resolves the CSV against Spotify, compares to an existing playlist,
    and adds only missing tracks. It never removes or reorders tracks yet.
    """
    if not args.playlist_id:
        raise ValueError("sync requires --playlist-id")

    config = get_config()
    client = SpotifyClient(config)

    results, uris = resolve_tracks(args, client)
    write_reports(results, args.report_dir)
    print_summary(results, uris, title="Sync Summary")

    if args.dry_run:
        console.print("[yellow]Dry run only. No playlist changed.[/yellow]")
        console.print(f"Reports written to: {args.report_dir}")
        return

    client.add_tracks(args.playlist_id, uris)
    console.print(f"[green]Added {len(uris)} missing tracks to existing playlist.[/green]")
    console.print(f"[green]Playlist ID:[/green] {args.playlist_id}")
    console.print(f"Reports written to: {args.report_dir}")


def add_common_args(parser):
    parser.add_argument("csv", help="Input CSV path with Title and Artist columns.")
    parser.add_argument("--playlist-id", default="", help="Existing Spotify playlist ID.")
    parser.add_argument("--dry-run", action="store_true", help="Search and report only. Do not create or modify playlist.")
    parser.add_argument("--allow-live", action="store_true", help="Allow live versions.")
    parser.add_argument("--no-remasters", action="store_true", help="Penalize remastered versions more heavily.")
    parser.add_argument("--artist-gap", type=int, default=10, help="Try to keep same artist this many songs apart.")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N tracks.")
    parser.add_argument("--search-limit", type=int, default=50, help="Spotify candidates per track.")
    parser.add_argument("--report-dir", default="reports")


def main():
    parser = argparse.ArgumentParser(description="Build exact Spotify playlists from CSV files.")
    sub = parser.add_subparsers(dest="command", required=True)

    build_parser = sub.add_parser("build", help="Build a Spotify playlist from a CSV.")
    add_common_args(build_parser)
    build_parser.add_argument("--name", required=True, help="Spotify playlist name.")
    build_parser.add_argument("--description", default="Built with Spotify Playlist Builder v1.1.")
    build_parser.add_argument("--public", default=None, help="true or false. Defaults to SPOTIFY_PUBLIC.")

    sync_parser = sub.add_parser("sync", help="Add missing CSV tracks to an existing Spotify playlist.")
    add_common_args(sync_parser)

    args = parser.parse_args()

    if args.command == "build":
        build(args)
    elif args.command == "sync":
        sync(args)


if __name__ == "__main__":
    main()
