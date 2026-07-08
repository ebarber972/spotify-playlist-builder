from collections import deque, defaultdict
from models import InputTrack

def space_artists(tracks: list[InputTrack], artist_gap: int = 10) -> list[InputTrack]:
    queues = defaultdict(deque)
    for t in tracks:
        queues[t.artist.lower()].append(t)

    last_seen = {}
    output = []
    total = len(tracks)

    while len(output) < total:
        best_artist = None
        best_wait = -999999

        for artist, q in queues.items():
            if not q:
                continue
            last = last_seen.get(artist, -999999)
            wait = len(output) - last
            if wait >= artist_gap:
                best_artist = artist
                break
            if wait > best_wait:
                best_wait = wait
                best_artist = artist

        if best_artist is None:
            break

        output.append(queues[best_artist].popleft())
        last_seen[best_artist] = len(output) - 1

    return output
