import random
import re
import time

import spotipy
from requests.exceptions import RetryError
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from config import SpotifyConfig
from models import SpotifyTrack

SCOPES = "playlist-modify-private playlist-modify-public playlist-read-private"

ARTIST_ALIASES = {
    "Mötley Crüe": ["Motley Crue"],
    "Queensrÿche": ["Queensryche"],
    "L.A. Guns": ["LA Guns", "L A Guns"],
    "W.A.S.P.": ["WASP", "W A S P"],
    "Tora Tora": ["Tora-Tora"],
    "Enuff Z'Nuff": ["Enuff Z Nuff", "Enuff Znuff"],
    "D-A-D": ["DAD", "Disneyland After Dark"],
}


def quote(value: str) -> str:
    return (value or "").replace('"', '')


class SpotifyClient:
    def __init__(self, config: SpotifyConfig):
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=config.client_id,
                client_secret=config.client_secret,
                redirect_uri=config.redirect_uri,
                scope=SCOPES,
                open_browser=False,
                cache_path=".spotify_token_cache",
            ),
            requests_timeout=30,
            retries=3,
            status_retries=3,
            backoff_factor=1,
        )
        self.user_id = self.sp.current_user()["id"]
        self._last_search_at = 0.0

    def artist_variants(self, artist: str) -> list[str]:
        variants = [artist]
        variants.extend(ARTIST_ALIASES.get(artist, []))

        plain = re.sub(r"[^A-Za-z0-9 ]+", " ", artist or "")
        plain = re.sub(r"\s+", " ", plain).strip()
        if plain and plain not in variants:
            variants.append(plain)

        return list(dict.fromkeys(v for v in variants if v))

    def build_queries(self, title: str, artist: str, album: str = "") -> list[str]:
        title_q = quote(title)
        album_q = quote(album)
        queries = []

        for artist_variant in self.artist_variants(artist):
            artist_q = quote(artist_variant)
            if album_q:
                queries.append(f'track:"{title_q}" artist:"{artist_q}" album:"{album_q}"')
                queries.append(f'"{title_q}" "{artist_q}" "{album_q}"')
            queries.append(f'track:"{title_q}" artist:"{artist_q}"')
            queries.append(f'{title_q} artist:"{artist_q}"')
            queries.append(f'"{title_q}" "{artist_q}"')
            queries.append(f'{title_q} {artist_q}')

        if album_q:
            queries.append(f'"{title_q}" "{album_q}"')

        queries.append(title_q)
        return list(dict.fromkeys(q for q in queries if q.strip()))

    def _pace_search(self) -> None:
        # Large playlist syncs can otherwise burst hundreds of searches in seconds.
        minimum_interval = 0.75
        elapsed = time.monotonic() - self._last_search_at
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)

    def _search_with_backoff(self, query: str, limit: int) -> dict:
        max_attempts = 12
        for attempt in range(1, max_attempts + 1):
            self._pace_search()
            try:
                result = self.sp.search(q=query, type="track", limit=limit)
                self._last_search_at = time.monotonic()
                return result
            except SpotifyException as exc:
                if exc.http_status != 429 or attempt == max_attempts:
                    raise
                retry_after = 0
                headers = getattr(exc, "headers", None) or {}
                try:
                    retry_after = int(headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    retry_after = 0
                delay = max(retry_after, min(120, 5 * (2 ** (attempt - 1))))
            except RetryError:
                if attempt == max_attempts:
                    raise
                delay = min(120, 5 * (2 ** (attempt - 1)))

            delay += random.uniform(0.25, 1.25)
            print(
                f"Spotify rate limit while searching; waiting {delay:.1f}s "
                f"before retry {attempt + 1}/{max_attempts}",
                flush=True,
            )
            time.sleep(delay)

        raise RuntimeError("Spotify search retry loop exited unexpectedly")

    def search_tracks(self, title: str, artist: str, album: str = "", limit: int = 20) -> list[SpotifyTrack]:
        """Search Spotify using increasingly broad query tiers."""
        queries = self.build_queries(title, artist, album)
        seen = set()
        all_tracks = []

        # Ten candidates is normally plenty for the matcher and cuts API load sharply.
        per_query_limit = max(5, min(limit, 10))
        target_pool_size = max(per_query_limit, min(limit, 20))

        for q in queries:
            results = self._search_with_backoff(q, per_query_limit)
            tracks = results.get("tracks", {}).get("items", [])
            for item in tracks:
                uri = item.get("uri")
                if uri and uri not in seen:
                    seen.add(uri)
                    all_tracks.append(self._to_track(item))

            if len(all_tracks) >= target_pool_size:
                break

        return all_tracks[:target_pool_size]

    def _to_track(self, item: dict) -> SpotifyTrack:
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        album = item.get("album", {}) or {}
        external_ids = item.get("external_ids", {}) or {}

        return SpotifyTrack(
            uri=item["uri"],
            title=item["name"],
            artist=artists,
            album=album.get("name", ""),
            album_type=album.get("album_type", ""),
            release_date=album.get("release_date", ""),
            duration_ms=item.get("duration_ms", 0),
            popularity=item.get("popularity", 0),
            explicit=item.get("explicit", False),
            isrc=external_ids.get("isrc", ""),
            track_number=item.get("track_number", 0),
            disc_number=item.get("disc_number", 0),
        )

    def create_playlist(self, name: str, description: str, public: bool = False) -> str:
        playlist = self.sp.user_playlist_create(
            user=self.user_id,
            name=name,
            public=public,
            description=description,
        )
        return playlist["id"]

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        if name:
            self.sp.playlist_change_details(playlist_id=playlist_id, name=name)

    def add_tracks(self, playlist_id: str, uris: list[str], batch_size: int = 100) -> None:
        for i in range(0, len(uris), batch_size):
            self.sp.playlist_add_items(playlist_id, uris[i:i + batch_size])

    def current_playlist_tracks(self, playlist_id: str) -> set[str]:
        seen = set()
        offset = 0
        while True:
            page = self.sp.playlist_items(
                playlist_id,
                fields="items(track(uri,external_ids,is_local,name,artists(name),album(name,album_type,release_date),duration_ms,popularity,explicit,track_number,disc_number)),next",
                limit=100,
                offset=offset,
            )
            for item in page.get("items", []):
                track = item.get("track")
                if track and track.get("uri"):
                    seen.add(track["uri"])
            if not page.get("next"):
                break
            offset += 100
        return seen
