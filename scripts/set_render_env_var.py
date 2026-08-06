"""Set a single env var on the Render service via the API (urllib-based).
Usage: python scripts/set_render_env_var.py KEY VALUE
"""
import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()
key = os.getenv("RENDER_API_KEY")
if not key:
    print("ERROR: RENDER_API_KEY not set in .env")
    sys.exit(1)

SERVICE_ID = "srv-d9nmbkm1egvs738cjqe0"


def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/set_render_env_var.py KEY VALUE")
        sys.exit(1)
    env_key, env_value = sys.argv[1], sys.argv[2]
    body = json.dumps({"envVars": [{"key": env_key, "value": env_value}]}).encode()
    req = urllib.request.Request(
        f"https://api.render.com/v1/services/{SERVICE_ID}/env-vars",
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"Status: {resp.status}")
            print(f"Response: {resp.read().decode()}")
    except urllib.error.HTTPError as e:
        print(f"HTTPError: {e.code}")
        print(f"Response: {e.read().decode()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
