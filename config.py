from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env", override=True)

@dataclass(frozen=True)
class SpotifyConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    default_public: bool = False

def get_config() -> SpotifyConfig:
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback").strip()
    default_public = os.getenv("SPOTIFY_PUBLIC", "false").lower() in ("1", "true", "yes", "y")

    missing = []
    if not client_id:
        missing.append("SPOTIFY_CLIENT_ID")
    if not client_secret:
        missing.append("SPOTIFY_CLIENT_SECRET")

    if missing:
        raise RuntimeError(
            "Missing Spotify config: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill it in."
        )

    return SpotifyConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        default_public=default_public,
    )
