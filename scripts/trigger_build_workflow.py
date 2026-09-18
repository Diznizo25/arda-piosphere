"""Trigger a GitHub Actions workflow (uses the stored git credential).

Usage:
  python scripts/trigger_build_workflow.py                       # build-water-points
  python scripts/trigger_build_workflow.py refresh-environment.yml
  python scripts/trigger_build_workflow.py refresh-environment.yml --wait
"""
from __future__ import annotations

import subprocess
import sys
import time

import httpx

OWNER = "Diznizo25"
REPO = "arda-piosphere"
WORKFLOW = "build-water-points.yml"


def get_token() -> str | None:
    prompt = "protocol=https\nhost=github.com\n\n"
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input=prompt,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    creds = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    return creds.get("password")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    wait = "--wait" in sys.argv
    workflow = args[0] if args else WORKFLOW

    token = get_token()
    if not token:
        print("no stored credential")
        return 1
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/{workflow}/dispatches"
    r = httpx.post(url, headers=headers, json={"ref": "main"}, timeout=30)
    print("dispatch status:", r.status_code)
    if r.status_code not in (204, 200):
        return 1
    if not wait:
        return 0

    # Poll the newest run of this workflow until it finishes (bounded).
    runs_url = (f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/"
                f"{workflow}/runs?per_page=1")
    for _ in range(40):
        time.sleep(15)
        try:
            run = httpx.get(runs_url, headers=headers, timeout=30).json()["workflow_runs"][0]
        except Exception:  # noqa: BLE001
            continue
        status, conclusion = run.get("status"), run.get("conclusion")
        print(f"  run {run.get('run_number')}: {status} {conclusion or ''}")
        if status == "completed":
            return 0 if conclusion == "success" else 1
    print("timed out waiting for the run")
    return 1


if __name__ == "__main__":
    sys.exit(main())
