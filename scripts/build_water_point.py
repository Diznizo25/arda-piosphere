"""Build a single water point end-to-end, with herder-facing progress.

Runs the same single-point pipeline as scripts/refresh_indices.py --water-source
but scoped to ONE point and instrumented: after every stage it updates the
water_point_builds row (app/services/build_tracker.py) and notifies the herder
via the web service (/dev/notify) with a rendered progress-bar image.

Runs as a scheduled GitHub Actions job (.github/workflows/build-water-points.yml,
every 10 min) that drains the pending queue, or manually from the export
machine:

    python scripts/build_water_point.py --water-source <id>
    python scripts/build_water_point.py --process-pending

Stages (progress %): zones(15) -> compute(45) -> transfer(90) -> done(100).
On failure the build row is marked failed. Never runs GEE live in the web
instance — this is the export runner.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import subprocess
import sys
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services import build_tracker  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_water_point")

SCRIPTS = Path(__file__).resolve().parent

NOTIFY_URL = "https://arda-piosphere.onrender.com/dev/notify"

MSG = {
    "start": {
        "swahili": "Tunaanza kujenga chanzo chako cha maji.",
        "english": "Starting to build your water point.",
    },
    "zones": {
        "swahili": "Hatua 1/3: Kuandaa maeneo ya kuzunguka (zones)...",
        "english": "Step 1/3: Preparing grazing zones...",
    },
    "compute": {
        "swahili": "Hatua 2/3: Kupakua data ya satelaiti (inachukua dakika kadhaa)...",
        "english": "Step 2/3: Downloading satellite data (takes several minutes)...",
    },
    "transfer": {
        "swahili": "Hatua 3/3: Kusafirisha ramani ya malisho...",
        "english": "Step 3/3: Uploading the pasture map...",
    },
    "done": {
        "swahili": "Chanzo chako kimekamilika! Sasa unaweza kupata ushauri na ramani. "
                   "Tuma 'map' uone ramani yake.",
        "english": "Your water point is ready! You can now get advice and its map. "
                   "Send 'map' to see it.",
    },
    "failed": {
        "swahili": "Samahani, ujenzi wa chanzo chako ulikwama. Tutaendelea kujaribu "
                   "tena — tumie 'status' kuangalia maendeleo.",
        "english": "Sorry, building your water point hit an issue. We will keep "
                   "retrying — send 'status' to check progress.",
    },
}

def _build_key() -> str:
    import os

    url = os.environ.get("DATABASE_URL", "")
    return hashlib.sha256(url.encode()).hexdigest()


def notify(build, stage: str, progress: int, done: bool = False) -> None:
    """Best-effort push of a progress update through the web service. Fail-open:
    the DB row is the source of truth, the WhatsApp push is a courtesy."""
    text = MSG[stage].get(build.language, MSG[stage]["swahili"])
    try:
        resp = httpx.post(
            NOTIFY_URL,
            json={
                "phone": build.creator_phone,
                "text": text,
                "stage": stage,
                "progress": progress,
                "water_source_id": build.water_source_id,
                "language": build.language,
                "done": done,
            },
            headers={"X-Build-Key": _build_key()},
            timeout=60,
        )
        if resp.status_code != 200:
            log.warning("notify returned %s: %.160s", resp.status_code, resp.text)
    except Exception:  # noqa: BLE001
        log.exception("notify failed for %s (non-fatal)", build.water_source_id)


def _run(script: str, args: list[str]) -> None:
    cmd = [sys.executable, str(SCRIPTS / script), *args]
    log.info("Running: %s", " ".join(cmd))
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"{script} failed with exit code {proc.returncode}")


def build_one(water_source_id: str) -> bool:
    """Run the pipeline for one water source, updating progress at each stage."""
    build = build_tracker.get_build(water_source_id)
    if build is None:
        log.error("No build record for water_source_id=%s", water_source_id)
        return False

    build_tracker.update_build(water_source_id, "running",
                               stage=MSG["start"].get(build.language), stage_index=0,
                               stage_total=3, progress=0)
    try:
        _run("generate_piosphere_zones.py", ["--water-source", water_source_id])
        build = build_tracker.get_build(water_source_id)
        build_tracker.update_build(water_source_id, "running",
                                   stage=MSG["zones"].get(build.language),
                                   stage_index=1, stage_total=3, progress=15)
        notify(build, "zones", 15)

        _run("gee_compute_export.py",
             ["--water-source", water_source_id, "--as-of-date", date.today().isoformat()])
        build = build_tracker.get_build(water_source_id)
        build_tracker.update_build(water_source_id, "running",
                                   stage=MSG["compute"].get(build.language),
                                   stage_index=2, stage_total=3, progress=45)
        notify(build, "compute", 45)

        _run("transfer_assets_to_r2.py", ["--asset", water_source_id, "--force"])
        build = build_tracker.get_build(water_source_id)
        build_tracker.update_build(water_source_id, "running",
                                   stage=MSG["transfer"].get(build.language),
                                   stage_index=3, stage_total=3, progress=90)
        notify(build, "transfer", 90)
    except Exception as e:  # noqa: BLE001
        log.exception(f"Build failed for {water_source_id}")
        build = build_tracker.get_build(water_source_id)
        build_tracker.update_build(water_source_id, "failed",
                                   stage=MSG["failed"].get(build.language),
                                   stage_index=None, stage_total=3, progress=100,
                                   error=str(e)[:500])
        notify(build, "failed", 100)
        return False

    build = build_tracker.get_build(water_source_id)
    build_tracker.update_build(water_source_id, "done",
                               stage=MSG["done"].get(build.language),
                               stage_index=3, stage_total=3, progress=100)
    notify(build, "done", 100, done=True)
    log.info("Water point %s is live.", water_source_id)
    return True


def process_pending() -> int:
    builds = build_tracker.claim_pending_builds()
    if not builds:
        log.info("No pending water-point builds.")
        return 0
    log.info("Claimed %d pending build(s).", len(builds))
    ok, failed = 0, 0
    for build in builds:
        if build_one(build.water_source_id):
            ok += 1
        else:
            failed += 1
    log.info("Done: %d built, %d failed.", ok, failed)
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--water-source", help="Build this one water_source id")
    group.add_argument("--process-pending", action="store_true",
                       help="Claim and build every pending water point")
    args = parser.parse_args()

    if args.water_source:
        return 0 if build_one(args.water_source) else 1
    return 1 if process_pending() else 0


if __name__ == "__main__":
    sys.exit(main())

