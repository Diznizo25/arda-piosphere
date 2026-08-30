"""Heart-girth (tape) weight estimation + herd sampling.

The pastoralist measures an animal's heart girth with a tailor's tape (cm),
and we estimate live weight from published species equations (config/
weight_formulas.yaml). For a herd, the herder samples a few animals and we
extrapolate the mean to the whole herd with a confidence range.

Everything here is deterministic + fail-safe: out-of-range measurements are
rejected with a clear message rather than extrapolated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import yaml

from app.config import BASE_DIR
from app.db import get_pg_connection

# Species *types* we can estimate. "shoat" splits into goat/sheep because the
# heart-girth equations differ meaningfully.
ANIMAL_TYPES = ("cattle", "goat", "sheep", "camel")
AGE_CLASSES = ("young", "adult")

WEIGHT_FORMULAS_PATH = BASE_DIR / "config" / "weight_formulas.yaml"

# Generic medication dosing note (see config file for the localized copy).
MEDICATION_NOTE_KEY = "medication_note"
MEASUREMENT_GUIDE_KEY = "measurement_guide"


@dataclass
class WeightEstimate:
    species_type: str
    heart_girth_cm: float
    age_class: str
    weight_kg: float
    valid_cm: tuple[float, float]


@dataclass
class HerdEstimate:
    species_type: str
    herd_count: int
    sample_size: int
    sample_mean_kg: float
    sample_sd_kg: float | None
    estimated_total_kg: float
    low_estimate_kg: float
    high_estimate_kg: float


@lru_cache
def _formulas() -> dict:
    with open(WEIGHT_FORMULAS_PATH) as f:
        return yaml.safe_load(f)


@lru_cache
def _copy(key: str, language: str) -> str:
    raw = _formulas().get(key, {})
    return raw.get(language, raw.get("swahili", ""))


def measurement_guide(language: str = "swahili") -> str:
    return _copy(MEASUREMENT_GUIDE_KEY, language)


def medication_note(language: str = "swahili") -> str:
    return _copy(MEDICATION_NOTE_KEY, language)


def validate_girth(species_type: str, girth_cm: float) -> tuple[bool, str | None]:
    """Return (ok, error_message). Rejects obviously mistyped measurements."""
    formula = _formulas()["heart_girth_formulas"].get(species_type)
    if formula is None:
        return False, f"unknown animal type {species_type}"
    lo, hi = formula["valid_cm"]
    if not (lo <= girth_cm <= hi):
        return False, f"{girth_cm:.0f} cm is outside the expected range for {species_type} ({lo:.0f}-{hi:.0f} cm)."
    return True, None


def estimate_weight(species_type: str, girth_cm: float, age_class: str = "adult") -> WeightEstimate:
    """W(kg) = a * G(cm)^b, then apply the age-class adjustment."""
    formula = _formulas()["heart_girth_formulas"][species_type]
    base = formula["a"] * (float(girth_cm) ** formula["b"])
    adjust = formula["age_adjust"].get(age_class, 1.0)
    weight = base * adjust
    return WeightEstimate(
        species_type=species_type,
        heart_girth_cm=float(girth_cm),
        age_class=age_class,
        weight_kg=round(weight, 1),
        valid_cm=tuple(formula["valid_cm"]),
    )

def estimate_herd(
    species_type: str,
    herd_count: int,
    girth_samples_cm: list[float],
    age_class: str = "adult",
) -> HerdEstimate:
    """Estimate total herd weight from a girth sample.

    Mean per-animal weight is the mean of the sampled animals' estimates. The
    confidence range on the TOTAL uses the t-distribution 95% CI on the mean
    (with a generous ±20% when only one animal was sampled), scaled by herd
    size. Honest about small samples without pretending.
    """
    if herd_count < 1:
        raise ValueError("herd_count must be >= 1")
    if not girth_samples_cm:
        raise ValueError("need at least one sampled girth")
    weights = [estimate_weight(species_type, g, age_class).weight_kg for g in girth_samples_cm]
    n = len(weights)
    mean = sum(weights) / n
    if n > 1:
        var = sum((w - mean) ** 2 for w in weights) / (n - 1)
        sd = math.sqrt(var)
        se = sd / math.sqrt(n)
        t = _t_95(n - 1)
        margin = t * se
    else:
        sd = None
        margin = mean * 0.20  # ±20% for a single sample
    total = mean * herd_count
    return HerdEstimate(
        species_type=species_type,
        herd_count=herd_count,
        sample_size=n,
        sample_mean_kg=round(mean, 1),
        sample_sd_kg=round(sd, 1) if sd is not None else None,
        estimated_total_kg=round(total, 0),
        low_estimate_kg=round(max(0.0, (mean - margin) * herd_count), 0),
        high_estimate_kg=round((mean + margin) * herd_count, 0),
    )


def record_weight(
    pastoralist_id: str,
    species_type: str,
    girth_cm: float,
    weight_kg: float,
    age_class: str | None = None,
    sex: str | None = None,
) -> None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into weight_records
                  (pastoralist_id, species, age_class, sex, heart_girth_cm,
                   estimated_weight_kg, method)
                values (%s, %s, %s, %s, %s, %s, 'heart_girth')
                """,
                (pastoralist_id, species_type, age_class, sex, girth_cm, weight_kg),
            )
        conn.commit()


def record_herd_estimate(
    pastoralist_id: str,
    species_type: str,
    herd_count: int,
    sample_size: int,
    sample_mean_kg: float,
    estimated_total_kg: float,
    low_kg: float,
    high_kg: float,
) -> None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into herd_estimates
                  (pastoralist_id, species, herd_count, sample_size,
                   sample_mean_kg, estimated_total_kg, low_estimate_kg, high_estimate_kg)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    pastoralist_id, species_type, herd_count, sample_size,
                    sample_mean_kg, estimated_total_kg, low_kg, high_kg,
                ),
            )
        conn.commit()


def recent_weight(pastoralist_id: str, limit: int = 3) -> list[dict]:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select species, age_class, sex, heart_girth_cm, estimated_weight_kg,
                       measured_at
                from weight_records
                where pastoralist_id = %s
                order by measured_at desc
                limit %s
                """,
                (pastoralist_id, limit),
            )
            rows = cur.fetchall()
    return [
        {
            "species": r["species"],
            "age_class": r["age_class"],
            "sex": r["sex"],
            "heart_girth_cm": float(r["heart_girth_cm"]),
            "estimated_weight_kg": float(r["estimated_weight_kg"]),
            "measured_at": r["measured_at"],
        }
        for r in rows
    ]


# -- small statistics helpers -------------------------------------------------

_T_TABLE: dict[int, float] = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    25: 2.060, 30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980,
}


def _t_95(df: int) -> float:
    """Two-tailed 95% t-critical value (approx table; fine for guidance)."""
    if df in _T_TABLE:
        return _T_TABLE[df]
    if df < 1:
        return _T_TABLE[1]
    # fall back to the next-smallest tabulated df
    best = _T_TABLE[1]
    for k in sorted(_T_TABLE):
        if k <= df:
            best = _T_TABLE[k]
    return best

