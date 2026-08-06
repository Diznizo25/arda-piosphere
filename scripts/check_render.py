"""Temporary helper: query the Render API to inspect GEE-related config.
Reads RENDER_API_KEY from .env. Prints results to stdout.
"""
import json
import os
import sys
import urllib.request

from dotenv import load_dotenv

load_dotenv()
key = os.getenv("RENDER_API_KEY")
if not key:
    print("ERROR: RENDER_API_KEY not set in .env")
    sys.exit(1)

SERVICE_ID = "srv-d9nmbkm1egvs738cjqe0"


def api_get(path: str):
    req = urllib.request.Request(
        f"https://api.render.com/v1{path}",
        headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def mask(v: str) -> str:
    if not v:
        return "(empty)"
    if len(v) <= 8:
        return "***"
    return v[:4] + "..." + v[-4:]


def main():
    # GEE-related env var values (masked)
    envs = api_get(f"/services/{SERVICE_ID}/env-vars")
    print("=== GEE-related env vars (values masked) ===")
    for e in envs:
        ev = e.get("envVar", {})
        key_name = ev.get("key")
        if key_name and ("GEE" in key_name or "GOOGLE" in key_name or "GCS" in key_name):
            print(f"  {key_name} = {mask(ev.get('value', ''))}")

    # Secret files
    print("\n=== Secret files ===")
    try:
        secrets = api_get(f"/services/{SERVICE_ID}/secret-files")
        for s in secrets:
            sf = s.get("secretFile", {})
            print(f"  name={sf.get('name')} path={sf.get('path')}")
    except Exception as e:
        print(f"  could not fetch secret files: {e}")


if __name__ == "__main__":
    main()
