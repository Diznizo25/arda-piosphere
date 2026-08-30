"""Check R2 state: list all COG objects with sizes, compare with local merged.tif.

Usage:
  python scripts/check_r2_state.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.storage import get_s3_client  # noqa: E402
from app.config import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    client = get_s3_client()

    paginator = client.get_paginator("list_objects_v2")
    rows = []
    for page in paginator.paginate(Bucket=settings.r2_bucket_name):
        for obj in page.get("Contents", []):
            rows.append((obj["Key"], obj["Size"], obj["LastModified"]))

    rows.sort(key=lambda r: r[0])
    print(f"Bucket {settings.r2_bucket_name}: {len(rows)} objects\n")
    print(f"{'key':<55} {'size_mb':>10}  {'local_merged_mb':>15}  {'match?':<8} last_modified")
    print("-" * 120)

    local_dir = Path("data/tiles")
    for key, size, last_mod in rows:
        ws_id = key.split("/")[1] if key.startswith("cogs/") and len(key.split("/")) > 1 else None
        local_sz = None
        if ws_id:
            m = local_dir / ws_id / "merged.tif"
            if m.exists():
                local_sz = m.stat().st_size
        size_mb = size / 1e6
        local_mb = (local_sz or 0) / 1e6
        match = ""
        if local_sz is not None:
            match = "OK" if abs(size - local_sz) < 1_000_000 else "DIFF!"
        print(f"{key:<55} {size_mb:>10.1f}  {local_mb:>15.1f}  {match:<8} {last_mod}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
