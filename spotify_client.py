import spotipy
from spotipy.oauth2 import SpotifyOAuth
from config import SpotifyConfig
from models import SpotifyTrack

SCOPES = "playlist-modify-private playlist-modify-public playlist-read-private"

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

    def search_tracks(self, title: str, artist: str, limit: int = 20) -> list[SpotifyTrack]:
        queries = [
            f'track:"{title}" artist:"{artist}"',
            f'{title} artist:"{artist}"',
            f'"{title}" "{artist}"',
            f"{title} {artist}",
        ]

        seen = set()
        all_tracks = []

        for q in queries:
            results = self.sp.search(q=q, type="track", limit=limit)
            tracks = results.get("tracks", {}).get("items", [])
            for item in tracks:
                uri = item.get("uri")
                if uri and uri not in seen:
                    seen.add(uri)
                    all_tracks.append(self._to_track(item))
            if len(all_tracks) >= limit:
                break

        return all_tracks[:limit]

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
