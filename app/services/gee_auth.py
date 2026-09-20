"""Earth Engine service-account auth, isolated so it's called exactly once
per process (scripts/gee_compute_export.py), never from request-serving code
(see architecture principle #1: FastAPI never calls GEE live).

`ee` is imported INSIDE the function on purpose: the web service only needs it for
the optional /health/gee diagnostic, and importing earthengine-api at module load
would force every container (and every unit test) to carry a batch-compute
dependency it never uses. ImportError is reported as "GEE unavailable", not a crash.
"""
from __future__ import annotations

import logging

from app.config import get_settings

log = logging.getLogger(__name__)

_initialized = False


def init_earth_engine() -> None:
    global _initialized
    if _initialized:
        return

    import ee  # lazy: see the module docstring

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
