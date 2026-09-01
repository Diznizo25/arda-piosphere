"""Restore the COMPLETE web-service env set on Render from the local .env.

Important: Render's env-var PUT replaces the entire set, and GET can return a
partial list (some vars were dropped by a previous merge-then-PUT). So this
script builds the full payload from the explicit key list below, pulling values
from the local .env, and sends ONE PUT with everything.
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values, load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

key = os.getenv("RENDER_API_KEY")
SERVICE_ID = "srv-d9nmbkm1egvs738cjqe0"
if not key:
    print("ERROR: RENDER_API_KEY not set")
    sys.exit(1)

local = dotenv_values(".env")

# Complete list of env vars the web service needs (render.yaml + runtime).
KEYS = [
    "ENVIRONMENT", "LOG_LEVEL",
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "DATABASE_URL",
    "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME", "R2_ENDPOINT_URL", "COG_PUBLIC_BASE_URL",
    "GEE_SERVICE_ACCOUNT_EMAIL", "GEE_SERVICE_ACCOUNT_KEY_PATH",
    "GEE_PROJECT_ID", "GEE_EXPORT_GCS_BUCKET", "GEE_EXPORT_DRIVE_FOLDER",
    "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_BUSINESS_ACCOUNT_ID", "WHATSAPP_VERIFY_TOKEN",
    "WHATSAPP_APP_SECRET", "WPDX_API_KEY",
    "APP_PUBLIC_BASE_URL",
    "DASHBOARD_TOKEN",
    "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_MODEL",
    "AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION",
    # GDAL/vsis3 R2 access (the rasterio read path uses /vsis3/ + standard AWS
    # env vars; boto3 in storage.py uses the R2_* vars directly).
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_S3_ENDPOINT",
    "AWS_VIRTUAL_HOSTING", "AWS_REGION",
]

# Production overrides (local .env is development).
OVERRIDES = {
    "ENVIRONMENT": "production",
    "APP_PUBLIC_BASE_URL": "https://arda-piosphere.onrender.com",
    "DASHBOARD_TOKEN": os.getenv("DASHBOARD_TOKEN", ""),
    "AZURE_OPENAI_MODEL": "gpt-5-mini",
    "AZURE_SPEECH_REGION": "southafricanorth",
    "AWS_ACCESS_KEY_ID": local.get("R2_ACCESS_KEY_ID", ""),
    "AWS_SECRET_ACCESS_KEY": local.get("R2_SECRET_ACCESS_KEY", ""),
    # Bare host, no scheme — this is required for GDAL /vsis3/ to reach R2.
    "AWS_S3_ENDPOINT": local.get("R2_ENDPOINT_URL", "")
    .removeprefix("https://").removeprefix("http://").rstrip("/"),
    "AWS_VIRTUAL_HOSTING": "FALSE",
    "AWS_REGION": "auto",
}

payload = []
missing = []
for k in KEYS:
    value = OVERRIDES.get(k, local.get(k, ""))
    if value is None:
        value = ""
    if not value:
        missing.append(k)
        continue  # empty optional vars (COG_PUBLIC_BASE_URL, WPDX_API_KEY, GCS bucket)
    payload.append({"key": k, "value": value})

if missing:
    print("SKIPPED (empty optional):", ", ".join(missing))

print(f"PUT {len(payload)} env vars")
url = f"https://api.render.com/v1/services/{SERVICE_ID}/env-vars"
headers = {"Authorization": f"Bearer {key}"}
resp = requests.put(url, json=payload, headers=headers, timeout=60, allow_redirects=False)
print("PUT status:", resp.status_code)
if resp.status_code >= 400:
    print(resp.text)
    sys.exit(1)
try:
    created = sorted(e["envVar"]["key"] for e in resp.json())
    print("PUT reported keys (%d): %s" % (len(created), ", ".join(created)))
    print("PUT missing: %s" % ", ".join(k for k in KEYS if k not in created))
except Exception:  # noqa: BLE001
    print(resp.text[:500])
print("Full env set restored on Render.")
