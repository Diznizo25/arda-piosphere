"""Earth Engine service-account auth, isolated so it's called exactly once
per process (scripts/gee_compute_export.py), never from request-serving code
(see architecture principle #1: FastAPI never calls GEE live)."""
from __future__ import annotations

import logging

import ee

from app.config import get_settings

log = logging.getLogger(__name__)

_initialized = False


def init_earth_engine() -> None:
    global _initialized
    if _initialized:
        return

    settings = get_settings()
    if not settings.gee_service_account_email or not settings.gee_service_account_key_path:
        raise RuntimeError(
            "GEE_SERVICE_ACCOUNT_EMAIL / GEE_SERVICE_ACCOUNT_KEY_PATH are not set. "
            "Create a GCP service account with Earth Engine access, download its "
            "JSON key to secrets/gee-service-account.json, and set both values in .env."
        )

    credentials = ee.ServiceAccountCredentials(
        settings.gee_service_account_email, settings.gee_service_account_key_path
    )
    ee.Initialize(credentials, project=settings.gee_project_id or None)
    _initialized = True
    log.info("Earth Engine initialized with service account %s", settings.gee_service_account_email)
