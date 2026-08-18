"""List GEE export tasks + assets in the piosphere folder."""
import json
import urllib.request

import ee

ee.Initialize(
    project="protean-tooling-466007-r0",
    credentials=ee.ServiceAccountCredentials(
        "arda-piosphere-gee@protean-tooling-466007-r0.iam.gserviceaccount.com",
        "secrets/gee-service-account.json",
    ),
)

print("=== EXPORT TASKS ===")
tasks = ee.batch.Task.list()
for t in tasks:
    s = t.status()
    if s.get("task_type") == "EXPORT_IMAGE":
        print(f"{s.get('id')} | {s.get('state')} | {s.get('description')} | {s.get('error_message')}")

print("=== PIOSPHERE ASSETS ===")
try:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_file(
        "secrets/gee-service-account.json",
        scopes=["https://www.googleapis.com/auth/earthengine"],
    )
    creds.refresh(Request())
    url = "https://earthengine.googleapis.com/v1alpha/projects/protean-tooling-466007-r0/assets/piosphere"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {creds.token}"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
    for a in data.get("assets", []):
        print(a.get("name"), "|", a.get("type"))
except Exception as e:
    print("Error listing assets:", e)


