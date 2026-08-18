"""Test: export a tiny image to a GEE Asset (free, no billing/Drive quota needed)."""
import time
import ee

ee.Initialize(
    project="protean-tooling-466007-r0",
    credentials=ee.ServiceAccountCredentials(
        "arda-piosphere-gee@protean-tooling-466007-r0.iam.gserviceaccount.com",
        "secrets/gee-service-account.json",
    ),
)

ASSET_ID = "projects/protean-tooling-466007-r0/assets/quota_test_asset"

# Tiny test image (SRTM, well-known global asset)
img = (
    ee.Image("USGS/SRTMGL1_003")
    .select("elevation")
    .clip(ee.Geometry.Point([37.5, 0.5]).buffer(1000))
)

task = ee.batch.Export.image.toAsset(
    image=img,
    description="quota_test_asset",
    assetId=ASSET_ID,
    scale=1000,
    maxPixels=1e10,
)
task.start()
print("Task started:", task.id, flush=True)

for i in range(12):
    time.sleep(5)
    s = task.status()
    state = s.get("state")
    print(f"[{i}] state={state} error={s.get('error_message')}", flush=True)
    if state in ("COMPLETED", "FAILED", "CANCELLED"):
        break

print("Final status:", task.status(), flush=True)
