"""Download a workflow run's failing step log (uses stored git credential)."""
from __future__ import annotations

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx

OWNER = "Diznizo25"
REPO = "arda-piosphere"
RUN_ID = sys.argv[1] if len(sys.argv) > 1 else "33328305381"


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
        print("no token")
        return 1
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/runs/{RUN_ID}/logs"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    r = httpx.get(url, headers=headers, timeout=90, follow_redirects=True)
    print("logs status:", r.status_code)
    if r.status_code != 200:
        print(r.text[:500])
        return 1
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = z.namelist()
        print("files:", names)
        for n in names:
            if "Build pending" in n:
                text = z.read(n).decode("utf-8", errors="replace")
                Path("last_step_log.txt").write_text(text[-12000:], encoding="utf-8")
                print("wrote last_step_log.txt chars:", len(text))
    except zipfile.BadZipFile:
        Path("last_step_log.txt").write_text(r.text[-12000:], encoding="utf-8")
        print("wrote raw body")
    return 0


if __name__ == "__main__":
    sys.exit(main())
