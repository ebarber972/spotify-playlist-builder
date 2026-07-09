import re
import subprocess
import sys
import threading
import time
import uuid
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

JOBS: dict[str, dict] = {}
PROGRESS_RE = re.compile(r"^(\d+)/(\d+)\s+Searching:\s+(.+)$")
MAX_LOG_LINES = 250


class BuildRequest(BaseModel):
    dry_run: bool = False
    playlist_id: Optional[str] = None
    name: Optional[str] = None
    search_limit: int = 50


def make_command(action: str, playlist_key: str, request: BuildRequest) -> list[str]:
    if playlist_key not in PLAYLISTS:
        raise HTTPException(status_code=404, detail=f"Unknown playlist: {playlist_key}")

    playlist = PLAYLISTS[playlist_key]
    name = request.name or playlist["name"]

    if action == "build":
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
    elif action == "sync":
        if not request.playlist_id:
            raise HTTPException(status_code=400, detail="sync requires playlist_id")
        args = [
            "sync",
            playlist["csv"],
            "--playlist-id",
            request.playlist_id,
            "--search-limit",
            str(request.search_limit),
        ]
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    if request.dry_run:
        args.append("--dry-run")

    return [sys.executable, "-u", "playlist_builder.py", *args]


def append_log(job: dict, line: str) -> None:
    job.setdefault("log_tail", []).append(line)
    job["log_tail"] = job["log_tail"][-MAX_LOG_LINES:]
    job["last_output_at"] = time.time()

    match = PROGRESS_RE.match(line.strip())
    if match:
        current = int(match.group(1))
        total = int(match.group(2))
        track = match.group(3)
        job["progress"] = {
            "current": current,
            "total": total,
            "percent": round((current / total) * 100, 2) if total else 0,
        }
        job["current_track"] = track


def read_stream(job_id: str, stream, key: str) -> None:
    job = JOBS[job_id]
    for line in iter(stream.readline, ""):
        line = line.rstrip("\n")
        if not line:
            continue
        if key == "stdout":
            append_log(job, line)
        else:
            job.setdefault("stderr_tail", []).append(line)
            job["stderr_tail"] = job["stderr_tail"][-MAX_LOG_LINES:]
            job["last_output_at"] = time.time()
    stream.close()


def cleanup_finished_jobs() -> None:
    for job_id, job in JOBS.items():
        proc: subprocess.Popen = job.get("process")
        if job.get("status") == "running" and proc and proc.poll() is not None:
            job["finished_at"] = time.time()
            job["returncode"] = proc.returncode
            job["success"] = proc.returncode == 0
            job["status"] = "completed" if proc.returncode == 0 else "failed"
            job.pop("process", None)


def running_job_for(action: str, playlist_key: str) -> Optional[dict]:
    cleanup_finished_jobs()
    for job_id, job in JOBS.items():
        if job.get("status") == "running" and job.get("action") == action and job.get("playlist_key") == playlist_key:
            return {"job_id": job_id, **public_job(job)}
    return None


def public_job(job: dict, include_output: bool = False) -> dict:
    data = {k: v for k, v in job.items() if k != "process"}
    if not include_output:
        data.pop("log_tail", None)
        data.pop("stderr_tail", None)
    if data.get("started_at") and data.get("finished_at"):
        data["elapsed_seconds"] = round(data["finished_at"] - data["started_at"], 2)
    elif data.get("started_at"):
        data["elapsed_seconds"] = round(time.time() - data["started_at"], 2)
    return data


def start_job(action: str, playlist_key: str, request: BuildRequest) -> dict:
    existing = running_job_for(action, playlist_key)
    if existing:
        return {
            "success": False,
            "status": "already_running",
            "message": f"A {action} job for {playlist_key} is already running.",
            "existing_job": existing,
        }

    command = make_command(action, playlist_key, request)
    job_id = f"{playlist_key}-{action}-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    proc = subprocess.Popen(
        command,
        cwd=Path(__file__).parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    JOBS[job_id] = {
        "job_id": job_id,
        "playlist_key": playlist_key,
        "action": action,
        "status": "running",
        "success": None,
        "dry_run": request.dry_run,
        "search_limit": request.search_limit,
        "command": " ".join(command),
        "pid": proc.pid,
        "started_at": time.time(),
        "finished_at": None,
        "returncode": None,
        "progress": None,
        "current_track": None,
        "last_output_at": None,
        "log_tail": [],
        "stderr_tail": [],
        "process": proc,
    }

    threading.Thread(target=read_stream, args=(job_id, proc.stdout, "stdout"), daemon=True).start()
    threading.Thread(target=read_stream, args=(job_id, proc.stderr, "stderr"), daemon=True).start()

    return {
        "success": True,
        "status": "started",
        "job_id": job_id,
        "pid": proc.pid,
        "command": " ".join(command),
    }


@app.get("/health")
def health() -> dict:
    cleanup_finished_jobs()
    return {"status": "ok", "jobs": len(JOBS)}


@app.get("/jobs")
def list_jobs() -> dict:
    cleanup_finished_jobs()
    return {"jobs": [{"job_id": job_id, **public_job(job)} for job_id, job in JOBS.items()]}


@app.get("/jobs/{job_id}")
def get_job(job_id: str, include_output: bool = True) -> dict:
    cleanup_finished_jobs()
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return public_job(JOBS[job_id], include_output=include_output)


@app.post("/jobs/{job_id}/kill")
def kill_job(job_id: str) -> dict:
    cleanup_finished_jobs()
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    job = JOBS[job_id]
    proc = job.get("process")
    if not proc:
        return {"success": False, "status": job.get("status"), "message": "Job is not running."}
    proc.terminate()
    job["status"] = "killed"
    job["finished_at"] = time.time()
    job["success"] = False
    job["returncode"] = None
    job.pop("process", None)
    return {"success": True, "status": "killed", "job_id": job_id}


@app.post("/build/{playlist_key}")
def build_playlist(playlist_key: str, request: BuildRequest) -> dict:
    return start_job("build", playlist_key, request)


@app.post("/sync/{playlist_key}")
def sync_playlist(playlist_key: str, request: BuildRequest) -> dict:
    return start_job("sync", playlist_key, request)
