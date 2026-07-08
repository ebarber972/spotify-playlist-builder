import re
import spotipy
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
                open_browser=True,
                cache_path=".spotify_token_cache",
            )
        )
        self.user_id = self.sp.current_user()["id"]

    def artist_variants(self, artist: str) -> list[str]:
        variants = [artist]
        variants.extend(ARTIST_ALIASES.get(artist, []))

        # Punctuation-light fallback for metal bands that Spotify sometimes indexes differently.
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

    def search_tracks(self, title: str, artist: str, album: str = "", limit: int = 20) -> list[SpotifyTrack]:
        """
        Search Spotify using multiple increasingly broad query tiers.
        Returns a merged candidate pool, not just the first successful search page.
        """
        queries = self.build_queries(title, artist, album)
        seen = set()
        all_tracks = []
        per_query_limit = max(10, min(limit, 50))
        target_pool_size = max(limit, 50)

        for q in queries:
            results = self.sp.search(q=q, type="track", limit=per_query_limit)
            tracks = results.get("tracks", {}).get("items", [])
            for item in tracks:
                uri = item.get("uri")
                if uri and uri not in seen:
                    seen.add(uri)
                    all_tracks.append(self._to_track(item))

            # Keep searching past the first page when the pool is small. This matters for deep cuts.
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
