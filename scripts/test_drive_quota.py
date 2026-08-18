"""Test: submit a tiny GEE export to the personal Drive folder ID and check it doesn't fail with quota error."""
import time
import ee

FOLDER_ID = "1fl6yUjrKqJm_rLLflmZkBoActfT6gpYJ"

ee.Initialize(
    project="protean-tooling-466007-r0",
    credentials=ee.ServiceAccountCredentials(
        "arda-piosphere-gee@protean-tooling-466007-r0.iam.gserviceaccount.com",
        "secrets/gee-service-account.json",
    ),
)

# Tiny test export to the folder ID (use SRTM, a well-known global asset)
img = (
    ee.Image("USGS/SRTMGL1_003")
    .select("elevation")
    .clip(ee.Geometry.Point([37.5, 0.5]).buffer(1000))
)
task = ee.batch.Export.image.toDrive(
    image=img,
    description="quota_test",
    folder=FOLDER_ID,
    fileNamePrefix="quota_test",
    scale=1000,
)
task.start()
print("Task started:", task.id, flush=True)

# Poll for up to 60s to see if it fails with quota error
for i in range(12):
    time.sleep(5)
    s = task.status()
    state = s.get("state")
    print(f"[{i}] state={state} error={s.get('error_message')}", flush=True)
    if state in ("COMPLETED", "FAILED", "CANCELLED"):
        break

print("Final status:", task.status(), flush=True)
