"""
Scheduled GEE compute + COG export job (architecture principle #1:
precompute-first — this is the ONLY place GEE gets called; FastAPI never
calls GEE live during a WhatsApp conversation).

For each water point in scope (ward or county), scoped to its outer (camel)
piosphere ring — never a whole ward's extent:
  1. build the Sentinel-2 composite + index stack (NDVI/NDRE/SATVI/BSI/NDMI/
     NDWI/VCI/JRC GSW) via app.services.gee_indices
  2. export as a Cloud-Optimized GeoTIFF to a staging destination (GEE can't
     export directly to R2). Two supported staging destinations:
       - Google Cloud Storage (GEE_EXPORT_GCS_BUCKET) — requires a billing
         account
       - Google Drive (GEE_EXPORT_DRIVE_FOLDER) — free, no billing needed.
         The Drive folder must be shared with the GEE service account email.
  3. once each export task finishes, download from the staging destination and
     re-upload to R2 at the canonical key from app.services.storage.cog_key
  4. mark piosphere_zones.last_computed for that water point's camel row

When using the Drive staging path, each COG is DELETED from Drive immediately
after it is successfully uploaded to R2, so Drive only ever holds the current
batch (never the whole county) and does not accumulate.

Usage:
  python scripts/gee_compute_export.py --ward "Oldonyiro"
  python scripts/gee_compute_export.py --county "Isiolo"

Cost/runtime scales with number of water points in scope, not land area —
each export region is a single water point's ~20-30km buffer, not the ward.
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402
from app.db import get_pg_connection  # noqa: E402
from app.services import gee_indices  # noqa: E402
from app.services.gee_auth import init_earth_engine  # noqa: E402
from app.services.storage import upload_file  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gee_compute_export")

FETCH_SQL = """
select
    pz.water_source_id,
    pz.radius_km,
    st_asgeojson(pz.geom) as geom_geojson
from piosphere_zones pz
join water_sources ws on ws.id = pz.water_source_id
where pz.species = 'camel'
  and (%(ward)s::text is null or ws.ward = %(ward)s)
  and (%(county)s::text is null or ws.county = %(county)s)
  and (%(water_source_id)s::uuid is null or ws.id = %(water_source_id)s)
"""

MARK_COMPUTED_SQL = """
update piosphere_zones
set last_computed = now()
where water_source_id = %(water_source_id)s and species = 'camel'
"""


def fetch_scope(ward: str | None, county: str | None, water_source_id: str | None = None) -> list[dict]:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(FETCH_SQL, {"ward": ward, "county": county, "water_source_id": water_source_id})
            return cur.fetchall()


def _export_task(image, water_source_id: str, region, gcs_bucket: str | None,
                 drive_folder: str | None):
    """Submit a GEE batch export to either GCS or Drive, whichever is configured."""
    import ee

    if gcs_bucket:
        return ee.batch.Export.image.toCloudStorage(
            image=image,
            description=f"piosphere_{water_source_id}"[:100],
            bucket=gcs_bucket,
            fileNamePrefix=f"cogs/{water_source_id}/indices",
            region=region,
            scale=10,
            crs="EPSG:4326",
            maxPixels=1e10,
            fileFormat="GeoTIFF",
            formatOptions={"cloudOptimized": True},
        )
    # Drive path (free, no billing). fileNamePrefix is the file name inside the
    # shared Drive folder.
    return ee.batch.Export.image.toDrive(
        image=image,
        description=f"piosphere_{water_source_id}"[:100],
        folder=drive_folder,
        fileNamePrefix=f"cogs_{water_source_id}_indices",
        region=region,
        scale=10,
        crs="EPSG:4326",
        maxPixels=1e10,
        fileFormat="GeoTIFF",
        formatOptions={"cloudOptimized": True},
    )


def submit_export_task(water_source_id: str, geom_geojson: str, as_of_date: str,
                       composite_window_days: int, vci_years_back: int,
                       gcs_bucket: str | None, drive_folder: str | None):
    import json

    region = __import__("ee").Geometry(json.loads(geom_geojson))
    month = datetime.fromisoformat(as_of_date).month
    image = gee_indices.build_stacked_image_for_month(
        region, as_of_date, month,
        composite_window_days=composite_window_days,
        vci_years_back=vci_years_back,
    )

    task = _export_task(image, water_source_id, region, gcs_bucket, drive_folder)
    task.start()
    return task


def _drive_service():
    """Build a Google Drive API client authenticated with the same service
    account credentials used for Earth Engine."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    settings = get_settings()
    creds = service_account.Credentials.from_service_account_file(
        settings.gee_service_account_key_path,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds)


def _find_drive_file(service, folder_name: str, file_name: str) -> str | None:
    """Locate the exported file inside the shared Drive folder by name.
    Returns the Drive file id, or None if not found yet."""
    # The folder may be shared with the service account; search by name.
    q = (
        f"name = '{file_name}' and "
        f"mimeType = 'image/tiff' and trashed = false"
    )
    results = service.files().list(q=q, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    return None


def _download_and_transfer_drive(service, folder_name: str, file_name: str,
                                 water_source_id: str) -> bool:
    """Download a finished COG from Drive, upload to R2, then DELETE the Drive
    copy so Drive never accumulates. Returns True on success."""
    from googleapiclient.http import MediaIoBaseDownload

    file_id = _find_drive_file(service, folder_name, file_name)
    if not file_id:
        log.error(f"Drive file {file_name} not found for {water_source_id}")
        return False

    with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
        request = service.files().get_media(fileId=file_id)
        downloader = MediaIoBaseDownload(tmp, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        tmp.flush()
        upload_file(Path(tmp.name), water_source_id)

    # Auto-delete the Drive copy now that it's safely in R2.
    try:
        service.files().delete(fileId=file_id).execute()
        log.info(f"Deleted Drive staging copy for {water_source_id}")
    except Exception as e:  # noqa: BLE001
        log.warning(f"Could not delete Drive copy for {water_source_id}: {e}")

    return True


def _transfer_one(water_source_id: str, gcs_bucket: str | None,
                  drive_folder: str | None, drive_service=None) -> None:
    """Download the finished COG from the configured staging destination and
    re-upload to R2 at the canonical per-water-point key, then mark
    last_computed."""
    if gcs_bucket:
        from google.cloud import storage as gcs_storage

        gcs_client = gcs_storage.Client()
        bucket = gcs_client.bucket(gcs_bucket)
        blob_name = f"cogs/{water_source_id}/indices.tif"
        blob = bucket.blob(blob_name)
        if not blob.exists():
            log.error(f"Expected GCS blob {blob_name} not found after task completion")
            return
        with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
            blob.download_to_filename(tmp.name)
            upload_file(Path(tmp.name), water_source_id)
    else:
        file_name = f"cogs_{water_source_id}_indices.tif"
        ok = _download_and_transfer_drive(
            drive_service, drive_folder, file_name, water_source_id
        )
        if not ok:
            return

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(MARK_COMPUTED_SQL, {"water_source_id": water_source_id})
        conn.commit()
    log.info(f"Transferred + marked computed: {water_source_id}")


def poll_and_transfer(tasks: dict[str, "ee.batch.Task"], gcs_bucket: str | None,
                      drive_folder: str | None, drive_service=None,
                      poll_interval_s: int = 20, timeout_s: int = 10800):

    pending = dict(tasks)
    done_ok: list[str] = []
    done_failed: list[str] = []
    elapsed = 0

    while pending and elapsed < timeout_s:
        for water_source_id, task in list(pending.items()):
            status = task.status()
            state = status.get("state")
            if state in ("COMPLETED",):
                _transfer_one(water_source_id, gcs_bucket, drive_folder, drive_service)
                done_ok.append(water_source_id)
                pending.pop(water_source_id)
            elif state in ("FAILED", "CANCELLED"):
                log.error(f"GEE task failed for {water_source_id}: {status.get('error_message')}")
                done_failed.append(water_source_id)
                pending.pop(water_source_id)
        if pending:
            log.info(f"{len(pending)} export tasks still running, waiting {poll_interval_s}s...")
            time.sleep(poll_interval_s)
            elapsed += poll_interval_s

    if pending:
        log.warning(f"Timed out waiting on {len(pending)} tasks (still running in GEE, "
                    f"will complete eventually — re-run later to pick up + transfer them).")

    return done_ok, done_failed


def run(ward: str | None, county: str | None, as_of_date: str,
        composite_window_days: int, vci_years_back: int, batch_size: int,
        timeout_s: int = 10800, water_source_id: str | None = None):

    settings = get_settings()
    gcs_bucket = settings.gee_export_gcs_bucket
    drive_folder = settings.gee_export_drive_folder

    if not gcs_bucket and not drive_folder:
        raise RuntimeError(
            "Neither GEE_EXPORT_GCS_BUCKET nor GEE_EXPORT_DRIVE_FOLDER is set in .env. "
            "Set one of them as the GEE staging destination (Drive is free and needs "
            "no billing account)."
        )
    if gcs_bucket and drive_folder:
        log.warning("Both GCS and Drive staging are set — using GCS (GEE_EXPORT_GCS_BUCKET).")

    init_earth_engine()

    # Build the Drive API client once if we're using the Drive staging path.
    drive_service = None
    if not gcs_bucket:
        drive_service = _drive_service()

    rows = fetch_scope(ward, county, water_source_id)
    log.info(f"Scope ward={ward!r} county={county!r} water_source={water_source_id!r}: "
             f"{len(rows)} water points to compute")
    if not rows:
        log.warning("Nothing in scope — did you run generate_piosphere_zones.py for this ward/county?")
        return

    total_ok, total_failed = 0, 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        log.info(f"Submitting batch {i // batch_size + 1} ({len(batch)} water points)...")
        tasks = {}
        for row in batch:
            task = submit_export_task(
                str(row["water_source_id"]), row["geom_geojson"], as_of_date,
                composite_window_days, vci_years_back, gcs_bucket, drive_folder,
            )
            tasks[str(row["water_source_id"])] = task

        ok, failed = poll_and_transfer(
            tasks, gcs_bucket, drive_folder, drive_service, timeout_s=timeout_s
        )
        total_ok += len(ok)
        total_failed += len(failed)


    log.info(f"Done. {total_ok} COGs computed + transferred, {total_failed} failed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--ward", help="Restrict to a single ward (validation gate)")
    scope.add_argument("--county", help="Restrict to a full county (scale-up)")
    scope.add_argument("--water-source", help="Restrict to a single water_source id (pin flow)")
    parser.add_argument("--as-of-date", default=date.today().isoformat())
    parser.add_argument("--composite-window-days", type=int, default=30)
    parser.add_argument("--vci-years-back", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=25,
                         help="GEE export tasks submitted+polled together, to avoid "
                              "hammering the batch quota on a county-wide run")
    parser.add_argument("--timeout-s", type=int, default=10800,
                         help="How long to poll each batch before giving up and "
                              "moving on (seconds). Default 10800 = 3 hours, since "
                              "heavy 10m COG exports can take >1 hour each.")
    args = parser.parse_args()
    run(args.ward, args.county, args.as_of_date, args.composite_window_days,
        args.vci_years_back, args.batch_size, args.timeout_s, args.water_source)



if __name__ == "__main__":
    main()
