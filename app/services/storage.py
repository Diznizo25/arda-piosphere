"""
Object storage (Cloudflare R2 / S3-compatible) access for COGs, plus the
naming convention that both the export job and the read path rely on.

One COG per water point, scoped to that point's outer (camel) piosphere ring —
not one COG per ward, and never per-county. Keeps the naming convention
independent of any DB column so the schema doesn't need a raster-path field:
both sides (writer in scripts/gee_compute_export.py, reader in
app/services/raster_read.py) derive the same key from water_source_id alone.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

from app.config import get_settings


def cog_key(water_source_id: str) -> str:
    """Object key for a given water point's stacked-index COG."""
    return f"cogs/{water_source_id}/indices.tif"


def cog_uri(water_source_id: str) -> str:
    """rasterio-readable URI. Prefers a public/CDN base URL if configured
    (rasterio can stream-read over HTTP via /vsicurl/); falls back to the
    S3-compatible endpoint via GDAL's /vsis3/ virtual filesystem."""
    settings = get_settings()
    key = cog_key(water_source_id)
    if settings.cog_public_base_url:
        return f"/vsicurl/{settings.cog_public_base_url.rstrip('/')}/{key}"
    return f"/vsis3/{settings.r2_bucket_name}/{key}"


@lru_cache
def get_s3_client():
    settings = get_settings()
    if not settings.r2_endpoint_url:
        raise RuntimeError(
            "R2_ENDPOINT_URL is not set. Copy .env.example to .env and fill in your "
            "Cloudflare R2 (or other S3-compatible) credentials."
        )
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=BotoConfig(signature_version="s3v4"),
        region_name="auto",
    )


def upload_file(local_path: Path, water_source_id: str, transfer_config=None) -> str:
    """Upload a local COG file to the water point's canonical key. Returns the
    object key. Sets Content-Type so downstream HTTP/CDN reads behave.

    `transfer_config` (boto3.s3.transfer.TransferConfig) lets callers tune the
    multipart upload for flaky links (larger parts / more attempts / lower
    concurrency). Defaults to the boto3 defaults when omitted.
    """
    settings = get_settings()
    client = get_s3_client()
    key = cog_key(water_source_id)
    client.upload_file(
        str(local_path),
        settings.r2_bucket_name,
        key,
        ExtraArgs={"ContentType": "image/tiff"},
        Config=transfer_config,
    )
    return key


def download_bytes(gcs_local_path: Path) -> bytes:
    return gcs_local_path.read_bytes()
