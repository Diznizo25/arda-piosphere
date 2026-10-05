"""
MALISHO YA LEO — the grazing ledger.

What this module does, in one line: it takes where the herd actually grazed and
answers the two questions a herder cannot answer from where he stands — *what was
that grass actually worth*, and *what closes the gap so the animals keep flesh*.

The two axes are deliberate and must never be collapsed into one number:

  QUANTITY  — is there enough to fill them?  Driven by standing biomass (SATVI,
              the same index this system already trusts for standing dry forage),
              filtered through the intake-rate plateau: above ~1000 kg/ha the
              animal's appetite is the limit, below ~250 kg/ha it cannot harvest
              enough however long it grazes.
  QUALITY   — does the fill carry energy?  Cured dry grass fills the rumen and
              delivers little. "Wanashiba lakini wanapungua" (they are full and
              still losing) is the exact thing this feature exists to say out
              loud, because it is the state a herder cannot see.

Everything the herder sees is a BAND with the satellite date and the word
"kadirio". Nothing here ever claims a body weight, a weight change, a diagnosis,
a drug or a dose — there is no code path that can produce one, and
scripts/test_forage.py asserts that mechanically.

Pure module by design: no DB, no network, no WhatsApp. The COG reader is injected
(`sample_fn`) so the arithmetic can be tested against seasons we are not in.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.config import CONFIG_DIR
from app.services.advisory_logic import ForageCondition, classify_forage_condition

log = logging.getLogger(__name__)

PARAMS_PATH = CONFIG_DIR / "forage_energy.yaml"

# Words that must never reach a herder from this feature. The grazing ledger
# advises on forage and energy, never on animal health: a mineral lick is feed,
# an injection is a vet's call. Asserted over every rendered sample in the tests.
FORBIDDEN_WORDS = (
    "dawa", "sindano", "chanjo", "antibiotic", "dewormer", "deworming",
    "dose", "dosage", "mg/kg", "ugonjwa", "drug", "injection", "vaccine",
    "ivermectin", "oxytetracycline",
)

DIGIT_QUALITY = {"1": "good", "2": "fair", "3": "poor"}


# ---------------------------------------------------------------------------
# parameters
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4)
def load_params(path: str | None = None) -> dict:
    """The whole parameter file. Cached; call `load_params.cache_clear()` in tests."""
    with open(path or PARAMS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# geometry — kept local so this module stays importable without PIL/numpy
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def walk_km(from_lat: float, from_lon: float, to_lat: float, to_lon: float,
            round_trip: bool = True) -> float:
    """Straight-line distance from the manyatta to the patch (x2 when round trip).

    Straight-line is the honest measure we can compute: animals do not walk
    straight, so this UNDER-states the real distance — the safe direction for an
    estimate that decides whether an animal is in deficit.
    """
    d = haversine_km(from_lat, from_lon, to_lat, to_lon)
    return d * 2.0 if round_trip else d


def offset_point(lat: float, lon: float, bearing_deg: float, km: float) -> tuple[float, float]:
    """Point `km` away from (lat, lon) along a compass bearing -> (lat, lon)."""
    br = math.radians(bearing_deg)
    dlat = (km * math.cos(br)) / 111.32
    dlon = (km * math.sin(br)) / (111.32 * max(0.2, math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


# ---------------------------------------------------------------------------
# the satellite side: a patch, not a pixel
# ---------------------------------------------------------------------------

@dataclass
class PatchSample:
    """Mean indices over a neighbourhood around the pin.

    A single 10-20 m pixel is noise (a bush, a bare patch, a shadow), so we always
    average a neighbourhood. `radius_m` and `pixel_m` travel with the reading
    because the memory-safe overview read is coarser than the native 10 m product,
    and the answer must be able to say which one produced the number.
    """
    ndvi: float
    satvi: float
    bsi: float
    ndmi: float | None = None
    vci: float | None = None
    n_pixels: int = 0
    radius_m: float = 250.0
    pixel_m: float | None = None


@dataclass
class ForageQuality:
    condition: str                     # a ForageCondition value
    label_swa: str
    label_eng: str
    me_mj_per_kg_dm: float
    protein: str                       # adequate | borderline | low
    biomass_lo_kg_ha: int
    biomass_hi_kg_ha: int
    utilisable_lo_kg_ha: int
    utilisable_hi_kg_ha: int
    curing: str | None                 # still_curing | fully_cured | None
    snapshot_as_of: str | None
    has_data: bool = True

    def label_for(self, lang: str) -> str:
        return self.label_eng if lang == "eng" else self.label_swa

    @property
    def biomass_mid_kg_ha(self) -> float:
        return (self.biomass_lo_kg_ha + self.biomass_hi_kg_ha) / 2.0

    @property
    def utilisable_mid_kg_ha(self) -> float:
        return (self.utilisable_lo_kg_ha + self.utilisable_hi_kg_ha) / 2.0

    @property
    def is_green(self) -> bool:
        return self.condition == ForageCondition.GREEN_GROWING.value


def quality_from_bands(ndvi: float, satvi: float, bsi: float, ndmi: float | None,
                       *, snapshot_as_of: str | None = None) -> ForageQuality:
    """Classify a patch (reusing the ONE forage rule in advisory_logic) and turn
    SATVI into a biomass BAND.

    Reusing classify_forage_condition is not a shortcut — it is the guarantee that
    the grazing ledger can never disagree with the advisory about what a patch of
    ground is, which is the failure a herder would notice immediately.
    """
    p = load_params()
    assessment = classify_forage_condition(
        {"NDVI": ndvi, "SATVI": satvi, "BSI": bsi,
         "NDMI": ndmi if ndmi is not None else float("nan")}
    )
    condition = assessment.condition.value if hasattr(assessment.condition, "value") \
        else str(assessment.condition)
    curing = assessment.curing_stage_note

    key = condition
    if condition == ForageCondition.DRY_FORAGE_AVAILABLE.value:
        # Grass cut by a dry spell is the dangerous middle: it still looks edible
        # and has already lost energy. Its own grade, its own wording.
        key = "dry_curing" if curing == "still_curing" else "dry_cured"
    q = p["quality"].get(key) or p["quality"]["uncertain"]

    b = p["biomass"]
    span = max(1e-6, float(b["satvi_max"]) - float(b["satvi_min"]))
    frac = _clamp((satvi - float(b["satvi_min"])) / span, 0.0, 1.0)
    mid = float(b["kg_ha_min"]) + frac * (float(b["kg_ha_max"]) - float(b["kg_ha_min"]))
    unc = _clamp(float(b["uncertainty_pct"]) / 100.0, 0.0, 0.6)
    lo, hi = mid * (1 - unc), mid * (1 + unc)

    util = p["utilisation"]
    if condition == ForageCondition.GREEN_GROWING.value:
        ufrac = float(util["green"])
    elif condition == ForageCondition.BARE_DEGRADED.value:
        ufrac = float(util["bare"])
    elif condition == ForageCondition.UNCERTAIN.value:
        ufrac = 0.2
    else:
        ufrac = float(util["dry"])

    return ForageQuality(
        condition=condition,
        label_swa=str(q["label_swa"]),
        label_eng=str(q["label_eng"]),
        me_mj_per_kg_dm=float(q["me_mj_per_kg_dm"]),
        protein=str(q["protein"]),
        biomass_lo_kg_ha=int(round(lo)),
        biomass_hi_kg_ha=int(round(hi)),
        utilisable_lo_kg_ha=int(round(lo * ufrac)),
        utilisable_hi_kg_ha=int(round(hi * ufrac)),
        curing=curing,
        snapshot_as_of=snapshot_as_of,
    )


def sample_patch(water_source_id: str, lon: float, lat: float,
                 radius_m: float = 250.0, max_dim: int = 512) -> PatchSample | None:
    """Mean indices over a neighbourhood around (lon, lat) from the point's COG.

    Returns None (never a guess) when there is no COG for that water point, or the
    pin falls outside the picture — the caller then says "we have no picture of
    that area yet" rather than borrowing a number from somewhere else.

    The memory-safe overview is read (cogs/<id>/overview.tif) because the full COG
    is ~500 MB and this runs on the request path. That costs spatial detail, so the
    reading carries `pixel_m` and the docs/tests state it exactly instead of
    implying 10 m precision we did not read.
    """
    try:
        import numpy as np

        from app.services.raster_read import read_overview_array

        # 1 NDVI, 3 SATVI, 4 BSI, 5 NDMI, 7 VCI (rasterio 1-based indexes)
        res = read_overview_array(water_source_id, bands=[1, 3, 4, 5, 7], max_dim=max_dim)
        if res is None:
            return None
        arr, transform = res
        if arr.shape[0] < 4:
            return None
        h, w = arr.shape[1], arr.shape[2]
        c0, f0 = transform.c, transform.f
        a_, e_ = transform.a, transform.e
        col = (lon - c0) / a_
        row = (lat - f0) / e_
        if not (0 <= col <= w - 1 and 0 <= row <= h - 1):
            return None
        # The transform is in DEGREES, so converting the neighbourhood radius to
        # pixels needs metres-per-degree — mixing the two units silently made the
        # "patch" the WHOLE ring (77k pixels over 25 km) instead of a 250 m spot.
        m_per_deg_lat = 110_570.0
        m_per_deg_lon = 111_320.0 * max(0.2, math.cos(math.radians(lat)))
        pr = max(1, int(round(radius_m / (abs(a_) * m_per_deg_lon))))
        c1, c2 = max(0, int(col) - pr), min(w, int(col) + pr + 1)
        r1, r2 = max(0, int(row) - pr), min(h, int(row) + pr + 1)
        win = np.asarray(arr[:, r1:r2, c1:c2], dtype="float64")
        if win.size == 0:
            return None
        rr = np.arange(r1, r2)[:, None]
        cc = np.arange(c1, c2)[None, :]
        north_m = np.abs(rr - row) * abs(e_) * m_per_deg_lat
        east_m = np.abs(cc - col) * abs(a_) * m_per_deg_lon
        dist_m = np.hypot(east_m, north_m)
        disc = dist_m <= radius_m
        if not disc.any():
            disc = dist_m <= (dist_m.min() + 1.0)   # a 1-pixel fallback, never the ring

        def _mean(band: int) -> float:
            v = win[band][disc]
            v = v[np.isfinite(v)]
            return float(v.mean()) if v.size else float("nan")

        ndvi, satvi, bsi = _mean(0), _mean(1), _mean(2)
        ndmi, vci = _mean(3), _mean(4)
        n = int(np.isfinite(win[1][disc]).sum())
        if n == 0 or not math.isfinite(satvi) or not math.isfinite(ndvi):
            return None
        return PatchSample(
            ndvi=ndvi, satvi=satvi, bsi=bsi,
            ndmi=ndmi if math.isfinite(ndmi) else None,
            vci=vci if math.isfinite(vci) else None,
            n_pixels=n, radius_m=radius_m, pixel_m=abs(a_) * m_per_deg_lon,
        )
    except Exception:  # noqa: BLE001 — fail open, never break the answer
        log.exception("patch sampling failed (non-fatal)")
        return None


# ---------------------------------------------------------------------------
# the animal's side: the energy ledger
# ---------------------------------------------------------------------------

@dataclass
class EnergyLedger:
    species: str
    head_count: int
    body_kg: float
    walk_km: float
    walk_hours: float
    maintenance_mj: float
    activity_mj: float
    locomotion_mj: float
    heat_mj: float
    heat_pct: float
    required_mj: float
    intake_kg_dm: float
    intake_mj: float
    harvest: float
    balance_mj: float
    balance_lo_mj: float
    balance_hi_mj: float
    verdict: str                       # surplus | holding | deficit

    @property
    def gap_mj(self) -> float:
        """The size of the hole we have to close, per head per day (0 when fed)."""
        return max(0.0, -self.balance_mj)


def herd_spec(species: str) -> dict:
    p = load_params()
    return p["species"].get(species) or p["species"]["cattle"]


def heat_pct(temp_max_c: float | None) -> float:
    """Extra maintenance from heat, as a fraction. Above the threshold the animal
    spends energy on panting AND grazes fewer hours — the cost only shows up here,
    the lost intake shows up as a lower harvest."""
    if temp_max_c is None:
        return 0.0
    e = load_params()["energy"]["heat"]
    over = float(temp_max_c) - float(e["threshold_c"])
    if over <= 0:
        return 0.0
    pct = min(float(e["max_pct"]), (over / 5.0) * float(e["pct_per_5c"]))
    return pct / 100.0


def harvest_factor(quality: ForageQuality) -> float:
    """How much of the animal's appetite the sward lets it satisfy, 0.15 - 1.0.

    This is the intake-rate plateau: the relationship every grazier knows without
    naming. Below ~250 kg/ha utilisable there is no way to fill up, however long
    they graze — and that is a DIFFERENT problem from grass that fills and does not
    feed, which the quality axis carries.
    """
    u = load_params()["utilisation"]
    avail = quality.utilisable_mid_kg_ha
    floor = float(u["min_kg_ha_to_graze"])
    starve = float(u["plateau_kg_ha_starve"])
    full = float(u["plateau_kg_ha_full"])
    if avail < floor:
        return 0.15                      # nothing there: nibbling residue and browse
    if avail < starve:
        return 0.15 + 0.35 * (avail - floor) / max(1.0, starve - floor)
    return _clamp(0.5 + 0.5 * (avail - starve) / max(1.0, full - starve), 0.5, 1.0)


def ledger(species: str, head_count: int, walk_distance_km: float,
           quality: ForageQuality, temp_max_c: float | None = None) -> EnergyLedger:
    """The whole ledger for ONE representative adult animal, plus what the herd
    needs. Per-head is what makes the numbers arguable; per-herd is what makes the
    cost real."""
    p = load_params()
    e = p["energy"]
    spec = herd_spec(species)
    body = float(spec["body_kg"])

    maintenance = float(e["maintenance_mj_per_kg075"]) * (body ** 0.75)
    activity = maintenance * float(e["activity_frac"])
    terrain = float(e.get("terrain_factor", 1.0))
    locomotion = (float(e["locomotion_j_per_kg_m"]) * body
                  * max(0.0, walk_distance_km) * 1000.0 * terrain) / 1_000_000.0
    hp = heat_pct(temp_max_c)
    heat = maintenance * hp
    required = (maintenance + activity + heat + locomotion)

    h = harvest_factor(quality)
    dmi_frac = float(spec["dmi_frac_green"] if quality.is_green else spec["dmi_frac_dry"])
    intake_kg = body * dmi_frac * h
    intake = intake_kg * quality.me_mj_per_kg_dm

    # The band comes from the biomass uncertainty, and it is applied to INTAKE
    # only — we are far more confident about the animal than about the grass.
    #
    # It also COLLAPSES as the sward gets richer, and that is physics rather than
    # convenience: above the intake plateau the animal's appetite is the limit, not
    # the biomass, so a 45% error in standing crop stops mattering. Without this,
    # +/-45% of a big intake swamps a real surplus and no herder on a green flush
    # could ever be told he is gaining — the ledger would push supplements at the
    # exact moment he needs none.
    unc = _clamp(float(p["biomass"]["uncertainty_pct"]) / 100.0, 0.0, 0.6)
    u = p["utilisation"]
    starve = float(u["plateau_kg_ha_starve"])
    full = float(u["plateau_kg_ha_full"])
    confidence = _clamp((quality.utilisable_mid_kg_ha - starve) / max(1.0, full - starve),
                        0.0, 1.0)
    unc_eff = unc * (1.0 - 0.8 * confidence)
    intake_lo, intake_hi = intake * (1 - unc_eff), intake * (1 + unc_eff)
    balance = intake - required
    band = required * float(e["balance_band_pct"]) / 100.0
    lo, hi = (intake_lo - required) - band, (intake_hi - required) + band

    # A verdict only when the whole band agrees — otherwise "holding". This is the
    # rule that keeps us from telling a herder to buy feed off a noisy reading.
    if hi < 0:
        verdict = "deficit"
    elif lo > 0:
        verdict = "surplus"
    else:
        verdict = "holding"

    hours = walk_distance_km / max(0.5, float(spec["km_per_hour"]))
    return EnergyLedger(
        species=species, head_count=int(head_count), body_kg=body,
        walk_km=round(float(walk_distance_km), 1), walk_hours=round(hours, 1),
        maintenance_mj=round(maintenance, 1), activity_mj=round(activity, 1),
        locomotion_mj=round(locomotion, 1), heat_mj=round(heat, 1),
        heat_pct=round(hp * 100.0, 1), required_mj=round(required, 1),
        intake_kg_dm=round(intake_kg, 1), intake_mj=round(intake, 1),
        harvest=round(h, 2), balance_mj=round(balance, 1),
        balance_lo_mj=round(lo, 1), balance_hi_mj=round(hi, 1), verdict=verdict,
    )


# ---------------------------------------------------------------------------
# offsets: the answer to "so what do I do"
# ---------------------------------------------------------------------------

@dataclass
class Option:
    code: str                      # move | water | mineral | feed | sell
    title_swa: str
    title_eng: str
    detail_swa: str = ""
    detail_eng: str = ""
    saves_mj: float = 0.0          # per head per day, only when we can compute it
    kg_per_head: float | None = None
    cost_per_head_ksh: int | None = None
    herd_cost_ksh: int | None = None
    free: bool = False
    recommended: bool = False

    def title(self, lang: str) -> str:
        return self.title_eng if lang == "eng" else self.title_swa

    def detail(self, lang: str) -> str:
        return self.detail_eng if lang == "eng" else self.detail_swa


@dataclass
class GrazingAdvice:
    quality: ForageQuality
    ledger: EnergyLedger
    options: list[Option] = field(default_factory=list)
    walk_km: float = 0.0
    from_manyatta: bool = False
    snapshot_as_of: str | None = None
    has_data: bool = True
    lang: str = "swa"

    @property
    def recommended(self) -> Option | None:
        for o in self.options:
            if o.recommended:
                return o
        return self.options[0] if self.options else None


def quantity_word(utilisable_mid: float, lang: str = "swa") -> str:
    """Quantity as a word a herder uses, from the utilisable band mid-point."""
    if utilisable_mid < 200:
        return "kidogo sana" if lang != "eng" else "very little"
    if utilisable_mid < 500:
        return "kidogo" if lang != "eng" else "little"
    if utilisable_mid < 1000:
        return "kati" if lang != "eng" else "moderate"
    return "nyingi" if lang != "eng" else "plenty"


def _herd_cost(cost_per_head: float, heads: int) -> int | None:
    """Cost for the whole herd, or None when we do not know the herd size.

    None (and no line in the message) beats a total computed from a guessed count:
    a herder who catches us inventing his herd stops believing the rest.
    """
    if heads is None or heads <= 0:
        return None
    return int(round(cost_per_head * heads / 10.0) * 10)


def build_options(quality: ForageQuality, led: EnergyLedger, *, species: str,
                  better: dict | None = None) -> list[Option]:
    """Free first, then cheap, then costed, and finally the honest exit.

    The order is the product decision: a herder told to buy feed before he is told
    to walk somewhere better learns that our advice costs him money. So anything
    free is computed, quantified and offered first — and only a deficit that
    survives the whole uncertainty band reaches the costed options at all.
    """
    p = load_params()
    off = p["offset"]
    out: list[Option] = []
    if led.verdict != "deficit":
        return out

    gap = led.gap_mj

    # 1) move — the cheapest and usually the largest single lever
    if off.get("move_patch") and better and better.get("quality") is not None:
        q2: ForageQuality = better["quality"]
        new_walk = float(better.get("walk_km") or 0.0)
        saved_km = max(0.0, led.walk_km - new_walk)
        e = p["energy"]
        loc_saved = (float(e["locomotion_j_per_kg_m"]) * led.body_kg
                     * saved_km * 1000.0) / 1_000_000.0
        h2 = harvest_factor(q2)
        spec = herd_spec(species)
        dmi_frac2 = float(spec["dmi_frac_green"] if q2.is_green else spec["dmi_frac_dry"])
        intake2 = led.body_kg * dmi_frac2 * h2 * q2.me_mj_per_kg_dm
        intake_gain = max(0.0, intake2 - led.intake_mj)
        saves = loc_saved + intake_gain
        if saves >= max(1.0, 0.2 * gap):
            where = better.get("direction") or ""
            out.append(Option(
                code="move", free=True, saves_mj=round(saves, 1),
                title_swa=f"Hamia km {new_walk:.0f} {where} — {q2.label_swa}".strip(),
                title_eng=f"Move {new_walk:.0f} km {where} — {q2.label_eng}".strip(),
                # The breakdown, not just the total: a herder who is told "38 MJ
                # better" with no reason has to take it on faith. Naming the two
                # parts (better grass, shorter walk) lets him argue with us — and
                # shows that most of the gain is usually the GRASS, not the steps.
                detail_swa=(f"Bila gharama — nyasi bora +{intake_gain:.0f} MJ, "
                            f"kutembea kufupi +{loc_saved:.0f} MJ kwa mnyama kwa siku"),
                detail_eng=(f"Costs nothing — better grass +{intake_gain:.0f} MJ, "
                            f"shorter walk +{loc_saved:.0f} MJ per animal per day"),
            ))

    # 2) water — free, and the one nobody thinks about in the heat
    if off.get("water_earlier") and led.heat_pct > 0:
        out.append(Option(
            code="water", free=True,
            title_swa="Mnyweshe mapema (asubuhi)",
            title_eng="Water them earlier (morning)",
            detail_swa="Joto kali linapunguza kula — si kipimo, ni msaada",
            detail_eng="Heat cuts grazing time — not a measured figure, a support",
        ))

    # 3) mineral lick — feed, never a drug, and cheap
    m = off["mineral"]
    if quality.protein in ("low", "borderline"):
        g = float(m["g_per_head_day"])
        cost = (g / 1000.0) * float(m["price_ksh_per_kg"])
        out.append(Option(
            code="mineral", kg_per_head=round(g / 1000.0, 3),
            cost_per_head_ksh=int(round(cost)),
            herd_cost_ksh=_herd_cost(cost, led.head_count),
            title_swa=f"{m['name_swa']} ({g:.0f} g/siku)",
            title_eng=f"{m['name_eng']} ({g:.0f} g/day)",
            # No clinical vocabulary ANYWHERE, not even a negation: "(madini, si
            # dawa)" tripped the guard, and the guard is right — a snippet of this
            # message can be forwarded out of context, so the word never ships.
            detail_swa="Husaidia mmeng'enyo wa nyasi kavu (hii ni chakula)",
            detail_eng="Helps digestion of dry roughage (this is feed)",
        ))

    # 4) energy supplement — only now, and always with its cost
    f = off["energy_feed"]
    me_feed = float(f["me_mj_per_kg_dm"])
    kg = min(gap / me_feed, float(f["max_kg_per_head_day"]))
    kg = math.ceil(kg * 10) / 10.0
    cost = kg * float(f["price_ksh_per_kg"])
    shortfall = gap - kg * me_feed
    note_swa = " (haitoshi peke yake — hamia pia)" if shortfall > 0.3 else ""
    note_eng = " (not enough alone — move as well)" if shortfall > 0.3 else ""
    out.append(Option(
        code="feed", kg_per_head=round(kg, 1),
        cost_per_head_ksh=int(round(cost)),
        herd_cost_ksh=_herd_cost(cost, led.head_count),
        title_swa=f"{f['name_swa']}: {kg:.1f} kg/siku{note_swa}",
        title_eng=f"{f['name_eng']}: {kg:.1f} kg/day{note_eng}",
        detail_swa=f"Bei ya kadirio KSh {int(round(float(f['price_ksh_per_kg'])))}/kg — "
                   f"hakikisha bei ya soko lako",
        detail_eng=f"Estimated KSh {int(round(float(f['price_ksh_per_kg'])))}/kg — "
                   f"confirm your market price",
    ))

    # 5) the honest exit: sell rather than feed a losing animal
    if off.get("sell_finished") and gap >= float(off["sell_deficit_mj"]):
        out.append(Option(
            code="sell",
            title_swa="Uza waliyoiva sasa",
            title_eng="Sell the finished ones now",
            detail_swa="Upungufu ni mkubwa — kuuza kabla ya kupoteza uzito ni faida",
            detail_eng="The gap is large — selling before they lose weight pays",
        ))

    if out:
        out[0].recommended = True
    return out


def advise(quality: ForageQuality, *, species: str, head_count: int,
           walk_distance_km: float, temp_max_c: float | None = None,
           from_manyatta: bool = False, better: dict | None = None,
           lang: str = "swa") -> GrazingAdvice:
    """One call: the satellite reading in, the whole answer out."""
    led = ledger(species, head_count, walk_distance_km, quality, temp_max_c)
    options = build_options(quality, led, species=species, better=better)
    return GrazingAdvice(
        quality=quality, ledger=led, options=options,
        walk_km=round(float(walk_distance_km), 1), from_manyatta=from_manyatta,
        snapshot_as_of=quality.snapshot_as_of, has_data=quality.has_data, lang=lang,
    )


# ---------------------------------------------------------------------------
# wording — the only place a herder-facing string is produced
# ---------------------------------------------------------------------------

HEADER = {"swa": "🌿 MALISHO YA LEO", "eng": "🌿 GRAZING TODAY"}

# The herder's own words for his animals. "(18 cattle)" in a Kiswahili message is
# the kind of small wrongness that makes a herder trust the rest of it less.
SPECIES_LABEL = {
    "swa": {"cattle": "ng'ombe", "shoat": "mbuzi/kondoo", "camel": "ngamia"},
    "eng": {"cattle": "cattle", "shoat": "goats/sheep", "camel": "camels"},
}


def species_label(species: str, lang: str = "swa") -> str:
    key = "eng" if lang == "eng" else "swa"
    return SPECIES_LABEL[key].get(species, species)
VERDICT_LINE = {
    "deficit": {
        "swa": "❗ Wanashiba lakini wanapungua.",
        "eng": "❗ They are filling up but losing condition.",
    },
    "bare_deficit": {
        "swa": "❗ Hakuna cha kutosha kula — wanapungua haraka.",
        "eng": "❗ Not enough to eat — they are losing fast.",
    },
    "surplus": {
        "swa": "✅ Wanakusanya zaidi ya wanachotumia — wanaongeza.",
        "eng": "✅ They are eating more than they spend — gaining.",
    },
    "holding": {
        "swa": "⚖️ Wanashikilia (kadirio) — usiongeze chakula sasa.",
        "eng": "⚖️ Holding (estimate) — no need to add feed now.",
    },
}


def assert_clean(text: str) -> None:
    """No drug, no dose, no diagnosis, ever. Called on every rendered sample."""
    low = (text or "").lower()
    for word in FORBIDDEN_WORDS:
        assert word not in low, f"forbidden word {word!r} in grazing message"


def quality_question(lang: str = "swa") -> str:
    return ("Malisho hapo yalikuwaje?\n1 = mazuri · 2 = kati · 3 = mabaya"
            if lang != "eng" else
            "How was the grazing there?\n1 = good · 2 = fair · 3 = poor")


def quality_for_digit(text: str) -> str | None:
    """One-tap answer -> ground-truth bucket. Anything else is not an answer."""
    t = (text or "").strip()
    if t in DIGIT_QUALITY:
        return DIGIT_QUALITY[t]
    low = t.lower()
    if low in ("nzuri", "mazuri", "good"):
        return "good"
    if low in ("kati", "fair", "sawa"):
        return "fair"
    if low in ("mbaya", "mabaya", "poor", "bad"):
        return "poor"
    return None


def thanks_for_quality(quality: str, lang: str = "swa") -> str:
    if lang == "eng":
        return "Thank you — we use this to check the satellite picture for your area."
    return "Asante — tunatumia jibu lako kukagua picha ya satellite ya eneo lako."


def no_data_message(lang: str = "swa") -> str:
    p = load_params()
    return str(p["no_data"]["eng" if lang == "eng" else "swa"])


def _fmt_mj(v: float) -> str:
    return f"{v:.0f}"


UNKNOWN_WALK = {
    "swa": "📍 Umbali: haujulikani bado (tuma eneo la manyatta yako)",
    "eng": "📍 Distance: not known yet (send your manyatta location)",
}


def _walk_line(advice: GrazingAdvice, lang: str) -> str:
    if advice.walk_km <= 0:
        # No origin means no distance. Inventing a plausible walk would quietly
        # change every locomotion figure in the answer, so we say we do not know.
        return ""
    if lang == "eng":
        origin = "from your manyatta" if advice.from_manyatta else "from your water point"
        return f"📍 You grazed ~{advice.walk_km:.1f} km {origin}"
    origin = "kutoka manyatta yako" if advice.from_manyatta else "kutoka chanzo chako cha maji"
    return f"📍 Mlipopanda ~km {advice.walk_km:.1f} {origin}"


def _snapshot_line(advice: GrazingAdvice, lang: str) -> str:
    q = advice.quality
    if not advice.snapshot_as_of:
        # A reading without its date is the one lie this system will not tell: say
        # the date is unknown rather than implying "today".
        stamp = "tarehe haijulikani" if lang != "eng" else "date unknown"
    else:
        stamp = advice.snapshot_as_of
    return (f"🛰 Picha ya {stamp}: {q.label_for(lang)}" if lang != "eng"
            else f"🛰 Picture of {stamp}: {q.label_for(lang)}")


def message(advice: GrazingAdvice, lang: str = "swa") -> str:
    """The full answer: quality, the per-head ledger, and what to do about it.

    Built from short lines on purpose: this is read on a phone in sunlight and
    sometimes heard as a voice note, so there is no paragraph anywhere in it.
    """
    led = advice.ledger
    p = load_params()
    lang_key = "eng" if lang == "eng" else "swa"
    lines = [HEADER[lang_key], "", _walk_line(advice, lang) or UNKNOWN_WALK[lang_key],
             _snapshot_line(advice, lang)]

    if led.head_count > 0:
        title = (f"KWA KILA MNYAMA ({led.head_count} "
                 f"{species_label(led.species, lang)})" if lang != "eng"
                 else f"PER ANIMAL ({led.head_count} {species_label(led.species, lang)})")
    else:
        # No herd size on file: keep the figures PER HEAD and say so, because a
        # guessed count would silently scale every cost we quote him.
        title = ("KWA KILA MNYAMA" if lang != "eng" else "PER ANIMAL")
    lines += ["", title]

    if lang == "eng":
        lines.append(f"Spends {_fmt_mj(led.required_mj)} MJ "
                     f"(walking {_fmt_mj(led.locomotion_mj)}, heat {_fmt_mj(led.heat_mj)})")
        lines.append(f"Gets {_fmt_mj(led.intake_mj)} MJ "
                     f"({led.intake_kg_dm:.1f} kg dry matter eaten)")
    else:
        lines.append(f"Anatumia MJ {_fmt_mj(led.required_mj)} "
                     f"(kutembea {_fmt_mj(led.locomotion_mj)}, joto {_fmt_mj(led.heat_mj)})")
        lines.append(f"Anakusanya MJ {_fmt_mj(led.intake_mj)} "
                     f"(nyasi {led.intake_kg_dm:.1f} kg kavu)")

    if led.verdict == "deficit":
        key = "bare_deficit" if led.harvest <= 0.2 else "deficit"
        lines.append(f"Upungufu MJ {_fmt_mj(led.gap_mj)}" if lang != "eng"
                     else f"Gap {_fmt_mj(led.gap_mj)} MJ")
        lines.append(VERDICT_LINE[key][lang_key])
    else:
        lines.append(VERDICT_LINE[led.verdict][lang_key])

    if advice.options:
        lines += ["", "FANYA HILI:" if lang != "eng" else "DO THIS:"]
        for i, o in enumerate(advice.options, start=1):
            mark = "✔ " if o.recommended else ""
            cost = ""
            if o.cost_per_head_ksh is not None:
                cost = (f" — ~KSh {o.cost_per_head_ksh}/animal/day" if lang == "eng"
                        else f" — ~KSh {o.cost_per_head_ksh}/mnyama/siku")
            if o.herd_cost_ksh:
                cost += (f" (herd ~KSh {o.herd_cost_ksh}/day)" if lang == "eng"
                         else f" (kundi ~KSh {o.herd_cost_ksh}/siku)")
            lines.append(f"{mark}{i}. {o.title(lang)}{cost}")
            if o.recommended and o.detail(lang):
                lines.append(f"   {o.detail(lang)}")

    q = advice.quality
    qty = quantity_word(q.utilisable_mid_kg_ha, lang)
    lines += ["", (
        # The word and the number MUST describe the same quantity: the word comes
        # from the utilisable band, so the number printed beside it does too. A
        # "little to eat" next to a standing-crop figure that looks like plenty is
        # exactly the contradiction that costs us a herder's trust.
        f"Malisho hapa: {qty} ya kula (kadirio {q.utilisable_lo_kg_ha}-"
        f"{q.utilisable_hi_kg_ha} kg kwa hekta)"
        if lang != "eng" else
        f"Forage here: {qty} to eat (estimate {q.utilisable_lo_kg_ha}-"
        f"{q.utilisable_hi_kg_ha} kg/ha)")]
    if q.utilisable_mid_kg_ha < float(p["utilisation"]["plateau_kg_ha_starve"]):
        lines.append("Nyasi ni chache sana kujaza tumbo — hata wakitembea siku nzima."
                     if lang != "eng" else
                     "Too little grass to fill up — whatever the hours.")
    lines.append(str(p["disclaimer"][lang_key]))
    out = "\n".join(lines)
    assert_clean(out)
    return out


def spoken_summary(advice: GrazingAdvice, lang: str = "swa") -> str:
    """Short, number-light version for a voice reply.

    Numbers that survive being read aloud are the ones a herder acts on; a table of
    mega-joules read by a synthetic voice is not information, it is noise.
    """
    led = advice.ledger
    q = advice.quality
    lead = advice.recommended
    if lang == "eng":
        base = (f"Grazing today: {q.label_eng}, about {advice.walk_km:.0f} kilometres "
                f"walked. Your {led.head_count} {species_label(led.species, 'eng')} are "
                f"using more energy than they are getting, so they are losing "
                f"condition. ")
        if lead:
            base += f"Best first step, free: {lead.title_eng}. "
        base += "This is an estimate from satellite, not a measurement."
        assert_clean(base)
        return base
    base = (f"Malisho ya leo: {q.label_swa}, umbali wa kilometa {advice.walk_km:.0f}. "
            f"{led.head_count} {species_label(led.species, 'swa')} wanatumia nishati "
            f"nyingi kuliko wanavyopata, hivyo wanapungua. ")
    if lead:
        base += f"Anza na hili bila gharama: {lead.title_swa}. "
    base += "Haya ni makadirio kutoka satellite, si vipimo vya mnyama wako."
    assert_clean(base)
    return base

