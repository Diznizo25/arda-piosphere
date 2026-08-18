"""Temporary helper: set an env var on the Render service via the API.
Reads RENDER_API_KEY from .env. Usage:
  python scripts/set_render_env.py KEY VALUE
"""
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("RENDER_API_KEY")
if not key:
    print("ERROR: RENDER_API_KEY not set in .env")
    sys.exit(1)

SERVICE_ID = "srv-d9nmbkm1egvs738cjqe0"


def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/set_render_env.py KEY VALUE")
        sys.exit(1)
    env_key, env_value = sys.argv[1], sys.argv[2]
    payload = [{"key": env_key, "value": env_value}]
    url = f"https://api.render.com/v1/services/{SERVICE_ID}/env-vars"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    resp = requests.put(url, json=payload, headers=headers, allow_redirects=False)
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")
    print(f"Location: {resp.headers.get('Location')}")
    if resp.status_code >= 400:
        sys.exit(1)
    print(f"Set {env_key} on Render service.")


if __name__ == "__main__":
    main()





