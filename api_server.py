import csv
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Spotify Playlist Builder API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

APP_DIR = Path(__file__).parent
PLAYLIST_DIR = APP_DIR / "playlists"
TARGETS_FILE = APP_DIR / "config" / "playlist_targets.csv"
DEFAULT_PLAYLIST_PREFIX = "The Sony Walkman Sessions"
JOBS: dict[str, dict] = {}
JOB_QUEUE: "queue.Queue[str]" = queue.Queue()
QUEUE_COOLDOWN_SECONDS = 10
PROGRESS_RE = re.compile(r"^(\d+)/(\d+)\s+Searching:\s+(.+)$")
MAX_LOG_LINES = 250


class BuildRequest(BaseModel):
    dry_run: bool = False
    playlist_id: Optional[str] = None
    name: Optional[str] = None
    search_limit: int = 50


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def humanize_playlist_name(stem: str) -> str:
    words = re.split(r"[_\-]+", stem.strip())
    pretty_words = []
    for word in words:
        lower = word.lower()
        if lower in {"80s", "90s", "70s", "60s"}:
            pretty_words.append(lower)
        elif lower in {"kroq", "roq"}:
            pretty_words.append(lower.upper())
        elif lower in {"rnb", "r&b"}:
            pretty_words.append("R&B")
        else:
            pretty_words.append(word.capitalize())
    return " ".join(pretty_words).strip()


def playlist_targets() -> dict[str, dict]:
    targets: dict[str, dict] = {}
    if not TARGETS_FILE.exists():
        return targets

    with TARGETS_FILE.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = (row.get("playlist_key") or "").strip()
            if not key or key.startswith("#"):
                continue
            targets[key] = {
                "playlist_id": (row.get("playlist_id") or "").strip(),
                "name": (row.get("playlist_name") or row.get("name") or "").strip(),
                "csv": (row.get("csv_file") or "").strip(),
            }
    return targets


def playlist_catalog() -> dict[str, dict]:
    catalog: dict[str, dict] = {}

    if PLAYLIST_DIR.exists():
        for csv_path in sorted(PLAYLIST_DIR.glob("*.csv")):
            rel_path = csv_path.relative_to(APP_DIR).as_posix()
            key = slugify(csv_path.stem)
            if not key:
                continue

            catalog[key] = {
                "csv": rel_path,
                "name": f"{DEFAULT_PLAYLIST_PREFIX}: {humanize_playlist_name(csv_path.stem)}",
            }

    for key, target in playlist_targets().items():
        configured_csv = target.get("csv")
        if configured_csv:
            csv_path = APP_DIR / configured_csv
            if not csv_path.is_file() or csv_path.suffix.lower() != ".csv":
                continue
            catalog[key] = {
                "csv": configured_csv,
                "name": f"{DEFAULT_PLAYLIST_PREFIX}: {humanize_playlist_name(csv_path.stem)}",
            }
        elif key not in catalog:
            # A target without a matching CSV is invalid. Never guess or fall back
            # to another playlist.
            continue
        if target.get("playlist_id"):
            catalog[key]["playlist_id"] = target["playlist_id"]
        if target.get("name"):
            catalog[key]["name"] = target["name"]

    return catalog


def make_command(action: str, playlist_key: str, request: BuildRequest) -> list[str]:
    playlists = playlist_catalog()
    if playlist_key not in playlists:
        raise HTTPException(status_code=404, detail=f"Unknown playlist: {playlist_key}")

    playlist = playlists[playlist_key]
    name = request.name or playlist["name"]
    playlist_id = request.playlist_id or playlist.get("playlist_id")

    if action == "build":
        args = [
            "build",
            playlist["csv"],
            "--name",
            name,
            "--search-limit",
            str(request.search_limit),
        ]
        if playlist_id:
            args.extend(["--playlist-id", playlist_id])
    elif action == "sync":
        if not playlist_id:
            raise HTTPException(status_code=400, detail="sync requires playlist_id (none provided and none saved for this playlist)")
        args = [
            "sync",
            playlist["csv"],
            "--playlist-id",
            playlist_id,
            "--search-limit",
            str(request.search_limit),
        ]
        if name:
            args.extend(["--name", name])
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


def finish_job(job: dict, returncode: int) -> None:
    if job.get("status") != "running":
        return
    job["finished_at"] = time.time()
    job["returncode"] = returncode
    job["success"] = returncode == 0
    job["status"] = "completed" if returncode == 0 else "failed"
    job.pop("process", None)


def cleanup_finished_jobs() -> None:
    for job in list(JOBS.values()):
        proc: Optional[subprocess.Popen] = job.get("process")
        if job.get("status") == "running" and proc:
            returncode = proc.poll()
            if returncode is not None:
                finish_job(job, returncode)


def _launch_subprocess(job_id: str) -> None:
    job = JOBS.get(job_id)
    if job is None:
        return

    command = job["command_list"]
    proc = subprocess.Popen(
        command,
        cwd=APP_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    job["status"] = "running"
    job["pid"] = proc.pid
    job["started_at"] = time.time()
    job["process"] = proc

    threading.Thread(target=read_stream, args=(job_id, proc.stdout, "stdout"), daemon=True).start()
    threading.Thread(target=read_stream, args=(job_id, proc.stderr, "stderr"), daemon=True).start()


def _queue_worker() -> None:
    # Only one Spotify-hitting job runs at a time, regardless of caller.
    # Everything else waits here until its turn, preventing concurrent bursts.
    while True:
        job_id = JOB_QUEUE.get()
        try:
            job = JOBS.get(job_id)
            if job is None or job.get("status") != "queued":
                continue

            try:
                _launch_subprocess(job_id)
            except Exception as exc:
                job["status"] = "failed"
                job["success"] = False
                job["finished_at"] = time.time()
                job["returncode"] = None
                job.setdefault("stderr_tail", []).append(f"Failed to start job: {exc}")
                continue

            while True:
                job = JOBS.get(job_id)
                if job is None or job.get("status") != "running":
                    break

                proc: Optional[subprocess.Popen] = job.get("process")
                if proc is None:
                    job["status"] = "failed"
                    job["success"] = False
                    job["finished_at"] = time.time()
                    job.setdefault("stderr_tail", []).append("Running job lost its subprocess handle.")
                    break

                returncode = proc.poll()
                if returncode is not None:
                    finish_job(job, returncode)
                    break

                time.sleep(1)

            time.sleep(QUEUE_COOLDOWN_SECONDS)
        finally:
            JOB_QUEUE.task_done()


threading.Thread(target=_queue_worker, daemon=True).start()


def running_job_for(action: str, playlist_key: str) -> Optional[dict]:
    cleanup_finished_jobs()
    for job_id, job in JOBS.items():
        if job.get("status") in ("running", "queued") and job.get("action") == action and job.get("playlist_key") == playlist_key:
            return {"job_id": job_id, **public_job(job)}
    return None


def public_job(job: dict, include_output: bool = False) -> dict:
    data = {k: v for k, v in job.items() if k not in {"process", "command_list"}}
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
        existing_status = existing.get("status", "running")
        return {
            "success": False,
            "status": existing_status,
            "message": f"A {action} job for {playlist_key} is already {existing_status}.",
            "existing_job": existing,
        }

    command = make_command(action, playlist_key, request)
    job_id = f"{playlist_key}-{action}-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    JOBS[job_id] = {
        "job_id": job_id,
        "playlist_key": playlist_key,
        "action": action,
        "status": "queued",
        "success": None,
        "dry_run": request.dry_run,
        "search_limit": request.search_limit,
        "name": request.name,
        "command": " ".join(command),
        "command_list": command,
        "pid": None,
        "started_at": None,
        "queued_at": time.time(),
        "finished_at": None,
        "returncode": None,
        "progress": None,
        "current_track": None,
        "last_output_at": None,
        "log_tail": [],
        "stderr_tail": [],
    }

    JOB_QUEUE.put(job_id)

    return {
        "success": True,
        "status": "queued",
        "job_id": job_id,
        "queue_position": JOB_QUEUE.qsize(),
        "command": " ".join(command),
    }


@app.get("/health")
def health() -> dict:
    cleanup_finished_jobs()
    return {"status": "ok", "jobs": len(JOBS)}


@app.get("/playlists")
def list_playlists() -> dict:
    return {"playlists": playlist_catalog()}


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

    if job.get("status") == "queued":
        job["status"] = "killed"
        job["finished_at"] = time.time()
        job["success"] = False
        return {"success": True, "status": "killed", "job_id": job_id}

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
    playlist = playlist_catalog().get(playlist_key)
    if playlist is None:
        raise HTTPException(status_code=404, detail=f"Unknown playlist: {playlist_key}")

    # Callers such as n8n may submit every playlist through the sync route.
    # A playlist without a Spotify ID has never been built, so safely promote
    # that first request to a build instead of rejecting it.
    playlist_id = request.playlist_id or playlist.get("playlist_id")
    if not playlist_id:
        return start_job("build", playlist_key, request)

    return start_job("sync", playlist_key, request)
