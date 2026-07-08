from dataclasses import dataclass
from typing import Optional

@dataclass
class InputTrack:
    title: str
    artist: str
    album: str = ""
    year: str = ""
    row_number: int = 0

@dataclass
class SpotifyTrack:
    uri: str
    title: str
    artist: str
    album: str
    album_type: str
    release_date: str
    duration_ms: int
    popularity: int
    explicit: bool
    isrc: str = ""
    track_number: int = 0
    disc_number: int = 0

@dataclass
class MatchResult:
    input_track: InputTrack
    spotify_track: Optional[SpotifyTrack]
    score: float
    status: str
    reason: str
