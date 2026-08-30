"""Verify all GEE piosphere assets are present in R2 (nothing pending)."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")
from app.config import get_settings  # noqa: E402
from app.services.gee_auth import init_earth_engine  # noqa: E402
from app.services.storage import cog_key, get_s3_client  # noqa: E402


def asset_exists(water_source_id: str) -> bool:
    settings = get_settings()
    client = get_s3_client()
    try:
        client.head_object(Bucket=settings.r2_bucket_name, Key=cog_key(water_source_id))
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    import ee

    init_earth_engine()
    settings = get_settings()
    folder = f"projects/{settings.gee_project_id}/assets/piosphere"
    result = ee.data.listAssets({"parent": folder})
    assets = [
        a["name"] for a in result.get("assets", []) if a.get("type") == "IMAGE"
    ]
    print(f"GEE piosphere assets: {len(assets)}")
    for name in assets:
        ws_id = name.rsplit("/", 1)[-1]
        ok = asset_exists(ws_id)
        print(f"  {ws_id[:8]}  in R2: {ok}")


if __name__ == "__main__":
    main()
