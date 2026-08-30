"""Trigger the build-water-points GitHub Actions workflow (uses stored git credential)."""
from __future__ import annotations

import subprocess
import sys

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
    token = get_token()
    if not token:
        print("no stored credential")
        return 1
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW}/dispatches"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = httpx.post(url, headers=headers, json={"ref": "main"}, timeout=30)
    print("dispatch status:", r.status_code)
    return 0 if r.status_code in (204, 200) else 1


if __name__ == "__main__":
    sys.exit(main())
