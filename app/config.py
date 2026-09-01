"""
Central configuration for the Arda Link Piosphere service.

Two layers, on purpose:
  - Settings: secrets / environment-specific values, loaded from .env
  - SpeciesRingConfig: tunable domain parameters (ring radii), loaded from YAML so
    they can change without a code deploy.
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"

Species = Literal["cattle", "shoat", "camel"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    # Supabase / Postgres
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    database_url: str = ""

    # Object storage (R2 / S3-compatible)
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "arda-piosphere-cogs"
    r2_endpoint_url: str = ""
    cog_public_base_url: str = ""

    # Public base URL of this app, used to build absolute links (e.g. the
    # WhatsApp map image URL). On Render it is the .onrender.com domain.
    app_public_base_url: str = ""

    # Optional bearer token protecting /dashboard (ops). Empty = dashboard open
    # (dev only); on Render set DASHBOARD_TOKEN in the environment.
    dashboard_token: str = ""

    # Google Earth Engine
    gee_service_account_email: str = ""
    gee_service_account_key_path: str = "./secrets/gee-service-account.json"
    gee_project_id: str = ""
    # GEE can export to GCS (requires a billing account) OR to Google Drive
    # (free). We support both: set GEE_EXPORT_GCS_BUCKET for the GCS path, or
    # GEE_EXPORT_DRIVE_FOLDER for the free Drive path. The Drive folder must be
    # shared with the GEE service account email.
    gee_export_gcs_bucket: str = ""
    gee_export_drive_folder: str = ""

    # WhatsApp
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""

    whatsapp_verify_token: str = "change-me-webhook-verify-token"
    whatsapp_app_secret: str = ""

    # Data sources
    wpdx_api_key: str = ""

    # Azure OpenAI (GPT-5-mini) — optional smart layer. If unset, the app
    # silently uses the deterministic rule-based path (fail-open).
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_model: str = "gpt-5-mini"

    # Azure AI Speech — for transcribing herders' WhatsApp voice notes.
    azure_speech_key: str = ""
    azure_speech_region: str = ""

    species_rings_path: Path = CONFIG_DIR / "species_rings.yaml"
    advisory_thresholds_path: Path = CONFIG_DIR / "advisory_thresholds.yaml"

    # Guard against stray whitespace/newlines sneaking into env values (e.g. a
    # trailing newline when a secret is pasted into GitHub Actions). Trimming
    # connection strings and credentials here prevents "database X\n does not
    # exist" style failures caused by a copied newline.
    @field_validator(
        "database_url",
        "supabase_url",
        "supabase_service_role_key",
        "r2_account_id",
        "r2_access_key_id",
        "r2_secret_access_key",
        "r2_bucket_name",
        "r2_endpoint_url",
        "gee_project_id",
        "gee_service_account_email",
        "gee_export_drive_folder",
        "gee_export_gcs_bucket",
    )
    @classmethod
    def _strip_whitespace(cls, v):
        return v.strip() if isinstance(v, str) else v


class SpeciesRingConfig:
    """Loaded from config/species_rings.yaml. Never hardcode radii in app code."""

    def __init__(self, path: Path):
        with open(path) as f:
            raw = yaml.safe_load(f)
        self.radii_km: dict[str, float] = {
            species: float(cfg["radius_km"])
            for species, cfg in raw["species_rings"].items()
        }
        self.compute_ring_species: str = raw["compute_ring_species"]

    def radius_for(self, species: Species) -> float:
        return self.radii_km[species]

    @property
    def compute_radius_km(self) -> float:
        """The outer ring radius GEE compute is scoped to (currently: camel)."""
        return self.radii_km[self.compute_ring_species]

    def species_ordered_by_radius(self) -> list[str]:
        """Smallest ring first — used when tagging a pixel/result with the
        narrowest species ring it falls within at read time."""
        return sorted(self.radii_km, key=lambda s: self.radii_km[s])


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_species_rings() -> SpeciesRingConfig:
    return SpeciesRingConfig(get_settings().species_rings_path)


class AdvisoryThresholds:
    """Loaded from config/advisory_thresholds.yaml. See that file's comments
    for the reasoning — never hardcode these values in advisory_logic.py."""

    def __init__(self, path: Path):
        with open(path) as f:
            raw = yaml.safe_load(f)
        self.vegetation = raw["vegetation"]
        self.seasonal = raw["seasonal"]
        self.water = raw["water"]


@lru_cache
def get_advisory_thresholds() -> AdvisoryThresholds:
    return AdvisoryThresholds(get_settings().advisory_thresholds_path)
