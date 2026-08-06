"""On-demand pipeline triggers.

The web service normally never calls GEE live — precompute happens in
scripts/gee_compute_export.py on a schedule. These endpoints let you trigger
the precompute scripts (piosphere zones + GEE export) on-demand from the same
deployed environment, so they inherit the web service's env vars and the GEE
service-account secret file. Useful for the Oldonyiro validation gate and for
re-running a ward/county without a separate cron job.

Each trigger launches the script as a subprocess in the background and returns
a task id; poll GET /run/status/{task_id} for progress. State is in-memory, so
it is per-instance only (fine for a single-instance free service).
"""
from __future__ import annotations

import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/run", tags=["run"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# task_id -> {"status", "started_at", "finished_at", "exit_code", "log"}
_TASKS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _run_script(script: str, args: list[str], task_id: str) -> None:
    """Run a pipeline script as a subprocess, capturing output into _TASKS."""
    cmd = ["python", str(BASE_DIR / "scripts" / script), *args]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log_lines: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            log_lines.append(line.rstrip())
        proc.wait()
        with _LOCK:
            _TASKS[task_id]["status"] = "finished" if proc.returncode == 0 else "failed"
            _TASKS[task_id]["exit_code"] = proc.returncode
            _TASKS[task_id]["finished_at"] = time.time()
            _TASKS[task_id]["log"] = log_lines
    except Exception as e:  # noqa: BLE001
        with _LOCK:
            _TASKS[task_id]["status"] = "failed"
            _TASKS[task_id]["exit_code"] = -1
            _TASKS[task_id]["finished_at"] = time.time()
            _TASKS[task_id]["log"] = [f"Failed to launch subprocess: {e}"]


def _start(script: str, args: list[str]) -> dict:
    task_id = uuid.uuid4().hex
    with _LOCK:
        _TASKS[task_id] = {
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "exit_code": None,
            "log": [],
        }
    threading.Thread(target=_run_script, args=(script, args, task_id), daemon=True).start()
    return {"task_id": task_id, "status": "running", "script": script, "args": args}


@router.post("/piosphere-zones")
def run_piosphere_zones(ward: str | None = None, county: str | None = None) -> dict:
    """Trigger generate_piosphere_zones.py. Pass exactly one of ward/county."""
    if bool(ward) == bool(county):
        raise HTTPException(status_code=400, detail="Provide exactly one of 'ward' or 'county'.")
    args = ["--ward", ward] if ward else ["--county", county]
    return _start("generate_piosphere_zones.py", args)


@router.post("/gee-export")
def run_gee_export(ward: str | None = None, county: str | None = None) -> dict:
    """Trigger gee_compute_export.py. Pass exactly one of ward/county."""
    if bool(ward) == bool(county):
        raise HTTPException(status_code=400, detail="Provide exactly one of 'ward' or 'county'.")
    args = ["--ward", ward] if ward else ["--county", county]
    return _start("gee_compute_export.py", args)


@router.get("/status/{task_id}")
def run_status(task_id: str) -> dict:
    """Poll the status + tail of a triggered pipeline run."""
    with _LOCK:
        task = _TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown task_id.")
    return {"task_id": task_id, **task}
