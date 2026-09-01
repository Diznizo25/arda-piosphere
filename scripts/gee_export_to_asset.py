"""
GEE compute + COG export job using GEE Assets as the FREE staging destination.

Why this exists: the GEE service account has 0 Drive storage quota and the GCP
project has no billing account, so neither the Drive nor GCS export paths work.
GEE Assets, however, are free and don't require billing or Drive quota — the
service account can export images to the project's asset folder.

Flow (per water point, scoped to its outer camel piosphere ring):
  1. build the Sentinel-2 composite + index stack via app.services.gee_indices
  2. export as a GEE Asset (projects/{project}/assets/piosphere/{water_source_id})
  3. once the task completes, download the asset as a GeoTIFF via the GEE API
  4. upload to R2 at the canonical key from app.services.storage.cog_key
  5. mark piosphere_zones.last_computed for that water point's camel row

Usage:
  python scripts/gee_export_to_asset.py --ward "Oldonyiro"
  python scripts/gee_export_to_asset.py --county "Isiolo"
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402
from app.db import get_pg_connection  # noqa: E402
from app.services import gee_indices  # noqa: E402
from app.services.gee_auth import init_earth_engine  # noqa: E402
from app.services.storage import upload_file  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gee_export_to_asset")

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
            cur.execute(
                FETCH_SQL,
                {"ward": ward, "county": county, "water_source_id": water_source_id},
            )
            return cur.fetchall()


def asset_id_for(water_source_id: str) -> str:
    settings = get_settings()
    return f"projects/{settings.gee_project_id}/assets/piosphere/{water_source_id}"


def asset_exists(water_source_id: str) -> bool:
    """True if the GEE asset already exists (so re-runs skip the export)."""
    import ee

    try:
        ee.data.getAsset(asset_id_for(water_source_id))
        return True
    except Exception:  # noqa: BLE001
        return False


def delete_asset(water_source_id: str) -> None:
    """Delete the GEE asset so a scheduled refresh re-exports with fresh data.

    R2 already holds the previous COG, so deleting the staging asset is safe —
    if the new export fails, the old COG simply stays served until the next run.
    """
    import ee

    asset_id = asset_id_for(water_source_id)
    try:
        ee.data.deleteAsset(asset_id)
        log.info(f"Deleted previous asset {asset_id} (force refresh)")
    except Exception as e:  # noqa: BLE001
        log.warning(f"Could not delete asset {asset_id} ({e}); export may fail "
                    f"if GEE refuses to overwrite it")


def submit_asset_task(water_source_id: str, geom_geojson: str, as_of_date: str,
                      composite_window_days: int, vci_years_back: int):
    """Submit a GEE batch export of the index stack to a GEE Asset (free)."""
    import ee

    region = ee.Geometry(__import__("json").loads(geom_geojson))
    month = datetime.fromisoformat(as_of_date).month
    image = gee_indices.build_stacked_image_for_month(
        region, as_of_date, month,
        composite_window_days=composite_window_days,
        vci_years_back=vci_years_back,
    )
    asset_id = asset_id_for(water_source_id)
    task = ee.batch.Export.image.toAsset(
        image=image,
        description=f"piosphere_{water_source_id}"[:100],
        assetId=asset_id,
        scale=10,
        crs="EPSG:4326",
        maxPixels=1e10,
    )
    task.start()
    return task


def download_asset_to_r2(water_source_id: str) -> bool:
    """Download a completed GEE asset as GeoTIFF and upload to R2."""
    import ee

    asset_id = asset_id_for(water_source_id)
    try:
        # Get a download URL for the asset image as a GeoTIFF. The image must
        # be wrapped in ee.Image() — passing the raw asset-id string fails with
        # "Image as JSON string not supported".
        dl = ee.data.getDownloadId({
            "image": ee.Image(asset_id),
            "scale": 10,
            "crs": "EPSG:4326",
            "format": "GEO_TIFF",
        })
        url = ee.data.makeDownloadUrl(dl)
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        resp = requests.get(url, timeout=600)
        resp.raise_for_status()
        tmp_path.write_bytes(resp.content)
        upload_file(tmp_path, water_source_id)
        tmp_path.unlink(missing_ok=True)
        log.info(f"Uploaded {water_source_id} to R2")
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"Failed to download/upload asset for {water_source_id}: {e}")
        return False


def poll_and_transfer(tasks: dict[str, object], poll_interval_s: int = 20,
                      timeout_s: int = 10800):
    pending = dict(tasks)
    done_ok: list[str] = []
    done_failed: list[str] = []
    elapsed = 0

    while pending and elapsed < timeout_s:
        for water_source_id, task in list(pending.items()):
            status = task.status()
            state = status.get("state")
            if state == "COMPLETED":
                if download_asset_to_r2(water_source_id):
                    with get_pg_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(MARK_COMPUTED_SQL, {"water_source_id": water_source_id})
                        conn.commit()
                    done_ok.append(water_source_id)
                else:
                    done_failed.append(water_source_id)
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
        log.warning(f"Timed out waiting on {len(pending)} tasks (still running in GEE).")

    return done_ok, done_failed


def run(ward: str | None, county: str | None, as_of_date: str,
        composite_window_days: int, vci_years_back: int, batch_size: int,
        timeout_s: int = 10800, water_source_id: str | None = None,
        export_only: bool = False, force: bool = False):
    init_earth_engine()

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
        skipped = 0
        for row in batch:
            ws_id = str(row["water_source_id"])
            if asset_exists(ws_id):
                if force:
                    # Scheduled refresh: delete the old asset so this run exports
                    # FRESH satellite data (asset_exists() would otherwise skip).
                    delete_asset(ws_id)
                else:
                    log.info(f"Asset already exists for {ws_id[:8]} — skipping export")
                    skipped += 1
                    continue
            task = submit_asset_task(
                ws_id, row["geom_geojson"], as_of_date,
                composite_window_days, vci_years_back,
            )
            tasks[ws_id] = task

        if skipped:
            log.info(f"{skipped} asset(s) already exported, not resubmitted.")

        if export_only:
            # Only create the GEE assets; the caller transfers them to R2 with
            # the tiled transfer script (single-shot download can exceed limits).
            pending = dict(tasks)
            done_ok, done_failed = [], []
            while pending:
                for ws_id, task in list(pending.items()):
                    state = task.status().get("state")
                    if state == "COMPLETED":
                        done_ok.append(ws_id)
                        pending.pop(ws_id)
                    elif state in ("FAILED", "CANCELLED"):
                        done_failed.append(ws_id)
                        pending.pop(ws_id)
                if pending:
                    log.info(f"{len(pending)} export tasks still running, waiting 20s...")
                    time.sleep(20)
            total_ok += len(done_ok) + skipped
            total_failed += len(done_failed)
        else:
            ok, failed = poll_and_transfer(tasks, timeout_s=timeout_s)
            total_ok += len(ok) + skipped
            total_failed += len(failed)

    log.info(f"Done. {total_ok} COGs computed + transferred, {total_failed} failed.")
    if total_failed:
        raise RuntimeError(f"{total_failed} water point(s) failed to export")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--ward", help="Restrict to a single ward (validation gate)")
    scope.add_argument("--county", help="Restrict to a full county (scale-up)")
    scope.add_argument("--water-source", help="Restrict to a single water_source id (pin flow)")
    parser.add_argument("--as-of-date", default=date.today().isoformat())
    parser.add_argument("--composite-window-days", type=int, default=30)
    parser.add_argument("--vci-years-back", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--timeout-s", type=int, default=10800)
    parser.add_argument("--export-only", action="store_true",
                        help="Only create GEE assets; skip the R2 upload (the tiled "
                             "transfer script uploads to R2 afterwards).")
    parser.add_argument("--force", action="store_true",
                        help="Delete existing GEE assets first so a scheduled refresh "
                             "re-exports with fresh satellite data.")
    args = parser.parse_args()
    run(args.ward, args.county, args.as_of_date, args.composite_window_days,
        args.vci_years_back, args.batch_size, args.timeout_s, args.water_source,
        args.export_only, args.force)


if __name__ == "__main__":
    main()
