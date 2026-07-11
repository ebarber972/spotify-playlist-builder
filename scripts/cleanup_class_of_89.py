from __future__ import annotations

import csv
import re
from pathlib import Path

TARGET = Path("playlists/sony_walkman_sessions_class_of_89.csv")

# Rows that are technically valid songs but clearly wrong for this specific playlist.
EXCLUDE_EXACT = {
    ("rock of ages", "the chipmunks & the chipettes"),
}

# Prefer the original album-era versions already represented elsewhere in the file.
VERSION_MARKERS = re.compile(r"\b(live|remix|re-recorded|karaoke|tribute)\b", re.I)


def norm(value: str) -> str:
    value = (value or "").casefold().replace("’", "'")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def parse_year(value: str) -> int | None:
    match = re.search(r"\b(19\d{2}|20\d{2})\b", value or "")
    return int(match.group(1)) if match else None


with TARGET.open(newline="", encoding="utf-8-sig") as handle:
    rows = list(csv.DictReader(handle))

seen: set[tuple[str, str]] = set()
cleaned: list[dict[str, str]] = []
removed: list[tuple[str, str, str]] = []

for row in rows:
    title = (row.get("Title") or "").strip()
    artist = (row.get("Artist") or "").strip()
    album = (row.get("Album") or "").strip()
    year_text = (row.get("Year") or "").strip()
    year = parse_year(year_text)

    if not title or not artist:
        removed.append((title, artist, "missing title or artist"))
        continue

    key = (norm(title), norm(artist))

    if key in EXCLUDE_EXACT:
        removed.append((title, artist, "wrong novelty/cover version"))
        continue

    if year and year > 1989:
        removed.append((title, artist, f"post-1989 release ({year})"))
        continue

    if key in seen:
        removed.append((title, artist, "duplicate title/artist"))
        continue

    # Only reject an explicitly labelled alternate version when the normal version
    # has already been retained. This keeps unique live-era essentials intact.
    if VERSION_MARKERS.search(title) and (norm(VERSION_MARKERS.sub("", title)), norm(artist)) in seen:
        removed.append((title, artist, "alternate version duplicate"))
        continue

    seen.add(key)
    cleaned.append({"Title": title, "Artist": artist, "Album": album, "Year": year_text})

with TARGET.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=["Title", "Artist", "Album", "Year"])
    writer.writeheader()
    writer.writerows(cleaned)

report = Path("reports/class_of_89_cleanup_report.csv")
report.parent.mkdir(parents=True, exist_ok=True)
with report.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(["Title", "Artist", "Reason"])
    writer.writerows(removed)

print(f"Kept {len(cleaned)} tracks; removed {len(removed)} rows")
print(f"Wrote cleanup report to {report}")
# Triggered after workflow creation.
