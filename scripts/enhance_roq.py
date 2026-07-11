from __future__ import annotations

import csv
from pathlib import Path

TARGET = Path("playlists/roq_of_the_80s.csv")
ADDITIONS = Path("playlists/roq_of_the_80s_additions.csv")


def norm(value: str) -> str:
    return " ".join(value.casefold().replace("’", "'").split())


def load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


rows = load(TARGET) + load(ADDITIONS)
seen: set[tuple[str, str]] = set()
cleaned: list[dict[str, str]] = []

for row in rows:
    title = (row.get("Title") or "").strip()
    artist = (row.get("Artist") or "").strip()
    album = (row.get("Album") or "").strip()
    year = (row.get("Year") or "").strip()

    extras = row.get(None) or []
    if extras:
        artist = f"{artist},{album}"
        album = year
        year = str(extras[0]).strip()

    if not title or not artist:
        continue
    try:
        numeric_year = int(year)
    except ValueError:
        continue
    if numeric_year > 1989:
        continue

    key = (norm(title), norm(artist))
    if key in seen:
        continue
    seen.add(key)
    cleaned.append({"Title": title, "Artist": artist, "Album": album, "Year": year})

with TARGET.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=["Title", "Artist", "Album", "Year"])
    writer.writeheader()
    writer.writerows(cleaned)

print(f"Wrote {len(cleaned)} unique 1980s-era tracks to {TARGET}")
