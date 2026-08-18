"""Create the GEE 'piosphere' assets subfolder (root assets folder now exists)."""
import json
import urllib.parse
import urllib.request

from google.auth.transport.requests import Request
from google.oauth2 import service_account

SCOPES = ["https://www.googleapis.com/auth/earthengine"]

creds = service_account.Credentials.from_service_account_file(
    "secrets/gee-service-account.json", scopes=SCOPES
)
creds.refresh(Request())
token = creds.token
print("Got token")

# Create the 'piosphere' folder under the project assets root.
asset_id = "piosphere"
url = (
    "https://earthengine.googleapis.com/v1alpha/projects/protean-tooling-466007-r0/assets"
    + "?" + urllib.parse.urlencode({"assetId": asset_id})
)
body = json.dumps({"type": "FOLDER"}).encode()
req = urllib.request.Request(
    url,
    data=body,
    method="POST",
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    },
)
try:
    with urllib.request.urlopen(req) as resp:
        print("Status:", resp.status)
        print("Response:", resp.read().decode())
except urllib.error.HTTPError as e:
    print("HTTPError:", e.code)
    print("Response:", e.read().decode())

# List assets under the root
try:
    list_url = "https://earthengine.googleapis.com/v1alpha/projects/protean-tooling-466007-r0/assets"
    list_req = urllib.request.Request(
        list_url,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(list_req) as resp:
        print("Assets:", resp.read().decode())
except Exception as e:
    print("Error listing:", e)
