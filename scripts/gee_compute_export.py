"""
Scheduled GEE compute + COG export job (architecture principle #1:
precompute-first — this is the ONLY place GEE gets called; FastAPI never
calls GEE live during a WhatsApp conversation).

For each water point in scope (ward or county), scoped to its outer (camel)
piosphere ring — never a whole ward's extent:
  1. build the Sentinel-2 composite + index stack (NDVI/NDRE/SATVI/BSI/NDMI/
     NDWI/VCI/JRC GSW) via app.services.gee_indices
  2. export as a Cloud-Optimized GeoTIFF to a GCS staging bucket (GEE can't
     export directly to R2)
  3. once each export task finishes, download from GCS and re-upload to R2
     at the canonical key from app.services.storage.cog_key
  4. mark piosphere_zones.last_computed for that water point's camel row

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
"""

MARK_COMPUTED_SQL = """
update piosphere_zones
set last_computed = now()
where water_source_id = %(water_source_id)s and species = 'camel'
"""


def fetch_scope(ward: str | None, county: str | None) -> list[dict]:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(FETCH_SQL, {"ward": ward, "county": county})
            return cur.fetchall()


def submit_export_task(water_source_id: str, geom_geojson: str, as_of_date: str,
                        composite_window_days: int, vci_years_back: int, gcs_bucket: str):
    import ee
    import json

    region = ee.Geometry(json.loads(geom_geojson))
    month = datetime.fromisoformat(as_of_date).month
    image = gee_indices.build_stacked_image_for_month(
        region, as_of_date, month,
        composite_window_days=composite_window_days,
        vci_years_back=vci_years_back,
    )

    task = ee.batch.Export.image.toCloudStorage(
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
    task.start()
    return task


def poll_and_transfer(tasks: dict[str, "ee.batch.Task"], gcs_bucket: str,
                       poll_interval_s: int = 20, timeout_s: int = 3600):
    from google.cloud import storage as gcs_storage

    gcs_client = gcs_storage.Client()
    bucket = gcs_client.bucket(gcs_bucket)

    pending = dict(tasks)
    done_ok: list[str] = []
    done_failed: list[str] = []
    elapsed = 0

    while pending and elapsed < timeout_s:
        for water_source_id, task in list(pending.items()):
            status = task.status()
            state = status.get("state")
            if state in ("COMPLETED",):
                _transfer_one(water_source_id, bucket)
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


def _transfer_one(water_source_id: str, gcs_bucket) -> None:
    """Download the finished COG from GCS and re-upload to R2 at the
    canonical per-water-point key, then mark last_computed."""
    blob_name = f"cogs/{water_source_id}/indices.tif"
    blob = gcs_bucket.blob(blob_name)
    if not blob.exists():
        log.error(f"Expected GCS blob {blob_name} not found after task completion")
        return

    with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
        blob.download_to_filename(tmp.name)
        upload_file(Path(tmp.name), water_source_id)

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(MARK_COMPUTED_SQL, {"water_source_id": water_source_id})
        conn.commit()
    log.info(f"Transferred + marked computed: {water_source_id}")


def run(ward: str | None, county: str | None, as_of_date: str,
        composite_window_days: int, vci_years_back: int, batch_size: int):
    settings = get_settings()
    if not settings.gee_export_gcs_bucket:
        raise RuntimeError("GEE_EXPORT_GCS_BUCKET is not set in .env — needed as a staging "
                            "area since GEE can't export directly to R2.")

    init_earth_engine()

    rows = fetch_scope(ward, county)
    log.info(f"Scope ward={ward!r} county={county!r}: {len(rows)} water points to compute")
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
                composite_window_days, vci_years_back, settings.gee_export_gcs_bucket,
            )
            tasks[str(row["water_source_id"])] = task

        ok, failed = poll_and_transfer(tasks, settings.gee_export_gcs_bucket)
        total_ok += len(ok)
        total_failed += len(failed)

    log.info(f"Done. {total_ok} COGs computed + transferred, {total_failed} failed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--ward", help="Restrict to a single ward (validation gate)")
    scope.add_argument("--county", help="Restrict to a full county (scale-up)")
    parser.add_argument("--as-of-date", default=date.today().isoformat())
    parser.add_argument("--composite-window-days", type=int, default=30)
    parser.add_argument("--vci-years-back", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=25,
                         help="GEE export tasks submitted+polled together, to avoid "
                              "hammering the batch quota on a county-wide run")
    args = parser.parse_args()
    run(args.ward, args.county, args.as_of_date, args.composite_window_days,
        args.vci_years_back, args.batch_size)


if __name__ == "__main__":
    main()
