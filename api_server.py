import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Spotify Playlist Builder API")

PLAYLISTS = {
    "hair-metal": {
        "csv": "playlists/hair_metal_master_database.csv",
        "name": "The Sony Walkman Sessions: Arena Rock & Hair Metal",
    },
    "starter": {
        "csv": "playlists/hair_metal_starter.csv",
        "name": "Docker Spotify Test",
    },
}


class BuildRequest(BaseModel):
    dry_run: bool = False
    playlist_id: Optional[str] = None
    name: Optional[str] = None
    search_limit: int = 50


def run_builder(args: list[str]) -> dict:
    command = [sys.executable, "playlist_builder.py", *args]
    result = subprocess.run(
        command,
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
    )

    return {
        "command": " ".join(command),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "success": result.returncode == 0,
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/build/{playlist_key}")
def build_playlist(playlist_key: str, request: BuildRequest) -> dict:
    if playlist_key not in PLAYLISTS:
        raise HTTPException(status_code=404, detail=f"Unknown playlist: {playlist_key}")

    playlist = PLAYLISTS[playlist_key]
    name = request.name or playlist["name"]

    args = [
        "build",
        playlist["csv"],
        "--name",
        name,
        "--search-limit",
        str(request.search_limit),
    ]

    if request.playlist_id:
        args.extend(["--playlist-id", request.playlist_id])

    if request.dry_run:
        args.append("--dry-run")

    result = run_builder(args)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result)
    return result


@app.post("/sync/{playlist_key}")
def sync_playlist(playlist_key: str, request: BuildRequest) -> dict:
    if playlist_key not in PLAYLISTS:
        raise HTTPException(status_code=404, detail=f"Unknown playlist: {playlist_key}")
    if not request.playlist_id:
        raise HTTPException(status_code=400, detail="sync requires playlist_id")

    playlist = PLAYLISTS[playlist_key]
    args = [
        "sync",
        playlist["csv"],
        "--playlist-id",
        request.playlist_id,
        "--search-limit",
        str(request.search_limit),
    ]

    if request.dry_run:
        args.append("--dry-run")

    result = run_builder(args)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result)
    return result
