import re
from rapidfuzz import fuzz
from models import InputTrack, SpotifyTrack, MatchResult

BAD_VERSION_WORDS = [
    "karaoke", "tribute", "cover", "as made famous", "originally performed",
    "re-record", "rerecorded", "re recorded", "sound alike", "instrumental",
    "demo", "rehearsal", "backing track", "remix", "club mix", "sped up",
    "slowed", "8 bit", "lofi", "lullaby"
]

LIVE_WORDS = [
    "live", "unplugged", "concert", "at wembley", "at budokan", "live at",
    "live from", "mtv unplugged"
]

REMASTER_WORDS = [
    "remaster", "remastered", "anniversary", "deluxe", "expanded"
]

COMPILATION_WORDS = [
    "greatest hits", "best of", "essentials", "anthology", "collection",
    "hits", "playlist", "very best", "gold", "icon"
]

SOUNDTRACK_WORDS = [
    "soundtrack", "motion picture", "music from"
]


def normalize(value: str) -> str:
    value = value or ""
    value = value.lower()
    value = value.replace("&", "and")
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"\[[^\]]*\]", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_keep_versions(value: str) -> str:
    value = value or ""
    value = value.lower()
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def contains_any(text: str, words: list[str]) -> bool:
    t = normalize_keep_versions(text)
    return any(w in t for w in words)


def version_text(track: SpotifyTrack) -> str:
    return f"{track.title} {track.album}"


def is_bad_version(track: SpotifyTrack) -> bool:
    return contains_any(version_text(track), BAD_VERSION_WORDS)


def is_live_version(track: SpotifyTrack) -> bool:
    return contains_any(version_text(track), LIVE_WORDS)


def is_compilation(track: SpotifyTrack) -> bool:
    return (track.album_type or "").lower() == "compilation" or contains_any(track.album, COMPILATION_WORDS)


def release_year(track: SpotifyTrack) -> int:
    if not track.release_date:
        return 9999
    try:
        return int(track.release_date[:4])
    except Exception:
        return 9999


def duration_bucket(duration_ms: int) -> int:
    # 3-second buckets are close enough to group the same recording across releases.
    return round((duration_ms or 0) / 3000)


def duplicate_key(track: SpotifyTrack) -> str:
    if track.isrc:
        return f"isrc:{track.isrc}"
    return f"fuzzy:{normalize(track.title)}|{normalize(track.artist)}|{duration_bucket(track.duration_ms)}"


def score_candidate(
    wanted: InputTrack,
    candidate: SpotifyTrack,
    allow_live: bool = False,
    allow_remasters: bool = True,
) -> MatchResult:
    wanted_title = normalize(wanted.title)
    wanted_artist = normalize(wanted.artist)
    wanted_album = normalize(wanted.album)
    wanted_year = None
    if wanted.year:
        try:
            wanted_year = int(str(wanted.year)[:4])
        except Exception:
            wanted_year = None

    got_title = normalize(candidate.title)
    got_artist = normalize(candidate.artist)
    got_album = normalize(candidate.album)

    title_score = fuzz.token_set_ratio(wanted_title, got_title)
    artist_score = fuzz.token_set_ratio(wanted_artist, got_artist)
    album_score = fuzz.token_set_ratio(wanted_album, got_album) if wanted_album else 0

    score = (title_score * 0.60) + (artist_score * 0.32)
    reasons = [
        f"title={title_score:.0f}",
        f"artist={artist_score:.0f}",
    ]

    if wanted_album:
        score += album_score * 0.08
        reasons.append(f"album={album_score:.0f}")

    vt = version_text(candidate)
    album_type = (candidate.album_type or "").lower()

    if is_bad_version(candidate):
        return MatchResult(wanted, candidate, 0, "REJECTED", "bad version: karaoke/tribute/cover/remix/demo")

    if is_live_version(candidate):
        if allow_live:
            score -= 5
            reasons.append("live allowed -5")
        else:
            score -= 55
            reasons.append("live penalty -55")

    if contains_any(vt, REMASTER_WORDS):
        if allow_remasters:
            score -= 2
            reasons.append("remaster -2")
        else:
            score -= 20
            reasons.append("remaster -20")

    if album_type == "album":
        score += 7
        reasons.append("album type +7")
    elif album_type == "single":
        score += 2
        reasons.append("single type +2")
    elif album_type == "compilation":
        score -= 8
        reasons.append("compilation type -8")

    if contains_any(candidate.album, COMPILATION_WORDS):
        score -= 7
        reasons.append("greatest/essentials collection -7")

    if contains_any(candidate.album, SOUNDTRACK_WORDS):
        score -= 4
        reasons.append("soundtrack -4")

    year = release_year(candidate)
    if wanted_year:
        if year == wanted_year:
            score += 8
            reasons.append("exact wanted year +8")
        elif abs(year - wanted_year) <= 1:
            score += 4
            reasons.append("near wanted year +4")
        elif year > wanted_year + 5:
            score -= 6
            reasons.append("late reissue year -6")
    else:
        if 1978 <= year <= 1993:
            score += 5
            reasons.append("era release +5")
        elif year >= 2000:
            score -= 4
            reasons.append("modern release -4")

    if title_score >= 85 and artist_score >= 85:
        score += min(candidate.popularity / 12, 7)
        reasons.append(f"popularity +{min(candidate.popularity / 12, 7):.1f}")

    if title_score < 70:
        score -= 25
        reasons.append("weak title -25")

    if artist_score < 75:
        score -= 35
        reasons.append("weak artist -35")

    status = "MATCH" if score >= 78 else "NO_MATCH"
    return MatchResult(wanted, candidate, round(score, 2), status, "; ".join(reasons))


def pick_best_match(
    wanted: InputTrack,
    candidates: list[SpotifyTrack],
    allow_live: bool = False,
    allow_remasters: bool = True,
) -> MatchResult:
    if not candidates:
        return MatchResult(wanted, None, 0, "NO_MATCH", "no Spotify search results")

    scored = [
        score_candidate(wanted, c, allow_live=allow_live, allow_remasters=allow_remasters)
        for c in candidates
    ]
    scored.sort(key=lambda r: r.score, reverse=True)

    # Prefer clean studio matches over live/compilation versions when scores are close enough.
    clean_matches = [
        r for r in scored
        if r.spotify_track
        and r.status == "MATCH"
        and not is_bad_version(r.spotify_track)
        and (allow_live or not is_live_version(r.spotify_track))
        and not is_compilation(r.spotify_track)
    ]
    if clean_matches:
        return clean_matches[0]

    # If all good candidates are compilations, prefer those over live versions.
    non_live_matches = [
        r for r in scored
        if r.spotify_track
        and r.status == "MATCH"
        and not is_bad_version(r.spotify_track)
        and (allow_live or not is_live_version(r.spotify_track))
    ]
    if non_live_matches:
        return non_live_matches[0]

    return scored[0]
