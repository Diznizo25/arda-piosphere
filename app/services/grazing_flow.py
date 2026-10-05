"""
MALISHO YA LEO — the flow, shared by WhatsApp and the map-tap endpoint.

One function does the whole job (`handle_pin`): pin in, answer out, event stored.
Both entry points call it, so a walk reported by tapping on the map behaves
*identically* to one reported by sharing a WhatsApp location — the same rule every
other service in this codebase follows, because a service that behaves differently
depending on how a herder reached it is a service with two sets of bugs.

Order of work, and why:

  1. resolve the ORIGIN of the walk: the manyatta if we have it, otherwise the
     water point he confirmed earlier — and the answer says which one it used.
  2. find the water point whose satellite picture covers the pin (the COG is built
     per point).
  3. sample the patch, classify it, run the energy ledger.
  4. look for a better patch we can name, because "move 4 km and gain" is the only
     advice here that costs nothing.
  5. store the event, ask the ONE question that calibrates the classes.

Everything is fail-open and the answer goes out even when the bookkeeping fails.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field

from app.db import get_pg_connection
from app.services import conversation, forage, manyattas, whatsapp_client

log = logging.getLogger(__name__)

TEMP_SQL = """
select temperature_max_c from environment_daily
where water_source_id = %(id)s and temperature_max_c is not null
order by observed_on desc limit 1
"""

MINT_TOKEN_SQL = """
insert into graze_tokens (token, phone)
values (%(token)s, %(phone)s)
returning token
"""

TOKEN_SQL = """
select phone from graze_tokens
where token = %(token)s and expires_at > now()
"""


# ---------------------------------------------------------------------------
# tokens: who tapped the map
# ---------------------------------------------------------------------------

def mint_token(phone: str) -> str | None:
    """A short, opaque, 48-hour pointer to this herder (never his phone number)."""
    token = secrets.token_urlsafe(8)
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(MINT_TOKEN_SQL, {"token": token, "phone": phone})
            conn.commit()
        return token
    except Exception:  # noqa: BLE001
        log.exception("graze token mint failed (non-fatal)")
        return None


def phone_for_token(token: str) -> str | None:
    if not token:
        return None
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(TOKEN_SQL, {"token": token})
                row = cur.fetchone()
        return str(row["phone"]) if row else None
    except Exception:  # noqa: BLE001
        log.debug("graze token lookup failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# prompts — the "super easy" part
# ---------------------------------------------------------------------------

GRAZE_PROMPT = {
    "swahili": "🌿 MALISHO YA LEO\n\nNiambie mlipopanda leo — kwa njia moja:\n"
               "• Gusa kitufe hapa chini kutuma eneo (rahisi zaidi)\n"
               "• Au andika jina la mahali (k.m. 'niko Kipsing')",
    "english": "🌿 GRAZING TODAY\n\nTell me where you grazed today — one way:\n"
               "• Tap the button below to send your location (easiest)\n"
               "• Or type the place name (e.g. 'I am at Kipsing')",
}

GRAZE_PROMPT_MAP = {
    "swahili": "• Au gusa mahali kwenye ramani: {url}",
    "english": "• Or tap the spot on the map: {url}",
}


def prompt_text(lang: str, url: str = "") -> str:
    """The pin request. The map bullet is dropped (not left dangling) when we have
    no public URL — a broken link in a herder's hands costs more trust than a
    missing option."""
    key = "english" if lang == "eng" else "swahili"
    out = GRAZE_PROMPT[key]
    if url:
        out += "\n" + GRAZE_PROMPT_MAP[key].format(url=url)
    return out

MANYATTA_PROMPT = {
    "swahili": "Kwanza niandikishe manyatta yako — mara moja tu, na nakumbuka daima.\n"
               "Gusa kitufe hapa chini kutuma eneo la manyatta yako.{hint}",
    "english": "First let me register your manyatta — once only, and I remember it.\n"
               "Tap the button below to send your manyatta location.{hint}",
}

LOCATION_BUTTON = {"swahili": "Tuma eneo langu 📍", "english": "Send my location 📍"}


def _lang_key(pastoralist) -> str:
    return "swa" if getattr(pastoralist, "preferred_language", "swahili") == "swahili" else "eng"


def map_url(phone: str, pastoralist, lon: float | None = None,
            lat: float | None = None) -> str:
    """The tap-the-map link, carrying a token instead of a phone number."""
    from app.config import get_settings

    token = mint_token(phone)
    if not token:
        return ""
    base = (get_settings().app_public_base_url or "").rstrip("/")
    if not base:
        return ""
    species = getattr(pastoralist, "primary_species", None) or "cattle"
    interval = getattr(pastoralist, "water_interval", None) or "daily"
    return (f"{base}/mapview/?lat={lat or 0.35}&lon={lon or 37.58}&species={species}"
            f"&interval={interval}&lang={_lang_key(pastoralist)}&graze=1&t={token}")


def prompt_for_pin(phone: str, pastoralist, *, lon: float | None = None,
                   lat: float | None = None) -> None:
    """Ask for the pin — with a one-tap location button as the primary path.

    Three ways in, because three kinds of herder will use this: the button (one
    tap, works with any phone), a place name in words (works with no data bundle
    to speak of), and the map (works when he is somewhere he knows by sight).
    """
    lang = _lang_key(pastoralist)
    url = map_url(phone, pastoralist, lon=lon, lat=lat)
    conversation.set_state(phone, "graze.await", {})
    whatsapp_client.send_location_request(phone, prompt_text(lang, url), LOCATION_BUTTON[lang])


def prompt_for_manyatta(phone: str, pastoralist) -> None:
    """Register the homestead once. We never ask again after this."""
    lang = _lang_key(pastoralist)
    conversation.set_state(phone, "graze.manyatta", {})
    whatsapp_client.send_location_request(
        phone, MANYATTA_PROMPT["english" if lang == "eng" else "swahili"].format(hint=""),
        LOCATION_BUTTON[lang])


# ---------------------------------------------------------------------------
# facts the flow needs
# ---------------------------------------------------------------------------

def origin_for(phone: str, pastoralist) -> dict:
    """Where the walk starts: the manyatta, else the water point, else his last pin.

    `from_manyatta` is returned with it so the message can say which origin the
    distance was measured from — an unlabelled distance is a wrong distance.
    """
    m = manyattas.manyatta_for_phone(phone)
    if m:
        return {"lat": float(m["lat"]), "lon": float(m["lon"]), "from_manyatta": True,
                "manyatta_id": str(m["id"]), "name": m.get("name")}
    try:
        from app.services.pastoralists import get_water_source

        ws = get_water_source(phone) or {}
        if ws.get("lat") is not None:
            return {"lat": float(ws["lat"]), "lon": float(ws["lon"]),
                    "from_manyatta": False, "manyatta_id": None,
                    "name": ws.get("name") or ws.get("point_name")}
    except Exception:  # noqa: BLE001
        log.debug("water point origin unavailable", exc_info=True)
    return {"lat": None, "lon": None, "from_manyatta": False,
            "manyatta_id": None, "name": None}


def head_count_for(pastoralist, species: str) -> int:
    """Herd size from what he has already told us, else 0 = "we do not know".

    A guessed herd size would silently scale every cost in the answer, so an
    unknown count is passed through as unknown and the per-head figures stand on
    their own.
    """
    comp = getattr(pastoralist, "herd_composition", None) or {}
    try:
        if isinstance(comp, dict):
            for key, val in comp.items():
                if str(key).lower().startswith(species[:4]) and val:
                    return int(val)
            total = sum(int(v) for v in comp.values() if str(v).isdigit())
            return total
    except Exception:  # noqa: BLE001
        log.debug("herd composition unreadable", exc_info=True)
    return 0


def water_source_for_pin(lon: float, lat: float, species: str,
                         interval: str) -> tuple[str | None, str]:
    """The point whose satellite picture covers this pin, and how it was chosen."""
    from app.config import get_species_rings
    from app.services import water_reach

    eff = get_species_rings().effective_radius_km(species, interval)
    try:
        found = water_reach.find_nearest_reachable_water(
            lon, lat, species, limit=1, effective_radius_km=eff)
        if found:
            return found[0].water_source_id, "reachable"
    except Exception:  # noqa: BLE001
        log.debug("reach lookup failed for grazing pin", exc_info=True)
    try:
        nearest = water_reach.list_nearby_water_sources(lon, lat, limit=1)
        if nearest:
            return nearest[0].get("id"), "nearest"
    except Exception:  # noqa: BLE001
        log.debug("nearest lookup failed for grazing pin", exc_info=True)
    return None, "none"


def temp_max_for(point_id: str | None) -> float | None:
    if not point_id:
        return None
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(TEMP_SQL, {"id": point_id})
                row = cur.fetchone()
        return float(row["temperature_max_c"]) if row else None
    except Exception:  # noqa: BLE001
        return None


def snapshot_for(point_id: str | None) -> str | None:
    """The satellite date, formatted the same way the map stamps it."""
    if not point_id:
        return None
    try:
        from app.services.map_renderer import _snapshot_as_of

        as_of = _snapshot_as_of(point_id)
        if not as_of:
            return None
        from datetime import date, datetime

        d = as_of if isinstance(as_of, date) else datetime.fromisoformat(str(as_of)[:10]).date()
        return d.strftime("%d %b")
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# the answer
# ---------------------------------------------------------------------------

def better_patch(point_id: str | None, pin_lon: float, pin_lat: float,
                 origin: dict, *, snapshot_as_of: str | None = None,
                 sample_fn=None, guidance_fn=None) -> dict | None:
    """The nearest usable patch we can NAME, with its own reading and its own walk.

    "Move 4 km north-east and gain 8 MJ" is the only advice in this whole feature
    that costs a herder nothing, so it gets the effort: we locate the patch, read
    the grass there, and measure the walk to it from the same origin as today's.
    If the patch is where he already is, there is nothing to say and we say nothing.
    """
    if not point_id:
        return None
    try:
        from app.services import map_renderer

        guid = (guidance_fn or map_renderer.pasture_guidance)(point_id, pin_lon, pin_lat)
    except Exception:  # noqa: BLE001
        log.debug("better-patch guidance failed", exc_info=True)
        return None
    if not guid:
        return None
    bearing, km = float(guid[0]), float(guid[1])
    if km < 1.0:
        return None                     # he is standing in it
    nlat, nlon = forage.offset_point(pin_lat, pin_lon, bearing, km)
    sample = (sample_fn or forage.sample_patch)(point_id, nlon, nlat)
    if sample is None:
        return None
    q = forage.quality_from_bands(sample.ndvi, sample.satvi, sample.bsi, sample.ndmi,
                                 snapshot_as_of=snapshot_as_of)
    if origin.get("lat") is not None:
        walk = forage.walk_km(origin["lat"], origin["lon"], nlat, nlon)
    else:
        walk = km * 2.0
    try:
        from app.services.map_renderer import compass_swa

        direction = compass_swa(bearing)
    except Exception:  # noqa: BLE001
        direction = None
    return {"lat": nlat, "lon": nlon, "bearing": bearing, "km": km,
            "walk_km": round(walk, 1), "quality": q, "direction": direction}


def analyse(phone: str, pastoralist, lat: float, lon: float, *,
            sample_fn=None, guidance_fn=None,
            temp_max_c: float | None = None):
    """Pin -> GrazingAdvice. Returns (advice|None, reason).

    `reason` says WHY there is no advice ("no_point", "no_picture"), because "we
    have nothing for that spot" and "something broke" deserve different words.
    """
    species = getattr(pastoralist, "primary_species", None) or "cattle"
    interval = getattr(pastoralist, "water_interval", None) or "daily"
    lang = _lang_key(pastoralist)

    point_id, _how = water_source_for_pin(lon, lat, species, interval)
    if not point_id:
        return None, "no_point"
    snapshot = snapshot_for(point_id)
    sampler = sample_fn or forage.sample_patch
    sample = sampler(point_id, lon, lat)
    if sample is None:
        return None, "no_picture"

    quality = forage.quality_from_bands(
        sample.ndvi, sample.satvi, sample.bsi, sample.ndmi, snapshot_as_of=snapshot)

    origin = origin_for(phone, pastoralist)
    walk = (forage.walk_km(origin["lat"], origin["lon"], lat, lon)
            if origin.get("lat") is not None else 0.0)
    better = better_patch(point_id, lon, lat, origin, snapshot_as_of=snapshot,
                          sample_fn=sample_fn, guidance_fn=guidance_fn)
    temp = temp_max_c if temp_max_c is not None else temp_max_for(point_id)
    heads = head_count_for(pastoralist, species)

    advice = forage.advise(
        quality, species=species, head_count=heads, walk_distance_km=walk,
        temp_max_c=temp, from_manyatta=bool(origin.get("from_manyatta")),
        better=better, lang=lang)
    # Kept for the caller's bookkeeping (stored with the event) — not shown.
    advice.point_id = point_id          # type: ignore[attr-defined]
    advice.origin = origin              # type: ignore[attr-defined]
    return advice, "ok"


# ---------------------------------------------------------------------------
# the flow itself
# ---------------------------------------------------------------------------

DEDUPE_MINUTES = 20


@dataclass
class GrazingResult:
    """Everything the caller needs to reply — the flow does not send anything."""
    ok: bool
    reason: str = "ok"
    advice: object | None = None
    text: str = ""
    spoken: str = ""
    question: str = ""
    buttons: list = field(default_factory=list)
    recorded: bool = False
    duplicate: bool = False
    ask_manyatta: bool = False
    prompt_location: bool = False
    point_id: str | None = None


QUALITY_BUTTONS = [("gq:1", "1 · mazuri"), ("gq:2", "2 · kati"), ("gq:3", "3 · mabaya")]


def _is_duplicate(phone: str) -> bool:
    """Did he just report a walk? A double tap must not double the ledger."""
    _eid, mins = manyattas.recent_event_minutes(phone)
    return mins is not None and mins < DEDUPE_MINUTES


def handle_pin(phone: str, pastoralist, lat: float, lon: float,
               source: str = "pin", *, sample_fn=None, guidance_fn=None,
               temp_max_c: float | None = None) -> GrazingResult:
    """A grazing pin in, the answer out, the walk stored.

    The answer goes out even when the satellite has nothing (honest words instead
    of a borrowed number) and even when the manyatta is unknown (per-head figures,
    with the origin it used named in the message) — because the herder asked a
    question, and "we do not know yet" is a valid answer.
    """
    lang = _lang_key(pastoralist)
    species = getattr(pastoralist, "primary_species", None) or "cattle"
    try:
        advice, reason = analyse(phone, pastoralist, lat, lon, sample_fn=sample_fn,
                                 guidance_fn=guidance_fn, temp_max_c=temp_max_c)
    except Exception:  # noqa: BLE001
        log.exception("grazing analysis failed")
        return GrazingResult(ok=False, reason="error",
                             text=forage.no_data_message(lang))

    existing = manyattas.manyatta_for_phone(phone)
    origin = getattr(advice, "origin", {}) if advice is not None else {}
    ask_manyatta = existing is None

    if advice is None:
        # The pin is real even when the picture is not: store the walk, then say
        # honestly that we have no satellite reading for that spot yet.
        if not _is_duplicate(phone):
            manyattas.record_grazing_event(
                phone, lat=lat, lon=lon, source=source, species=species,
                head_count=None, advice=None,
                manyatta_id=(existing or {}).get("id"),
                pastoralist_id=getattr(pastoralist, "id", None), lang=lang)
        return GrazingResult(
            ok=False, reason=reason, text=forage.no_data_message(lang),
            ask_manyatta=ask_manyatta, point_id=None)

    duplicate = _is_duplicate(phone)
    if not duplicate:
        recorded = manyattas.record_grazing_event(
            phone, lat=lat, lon=lon, source=source, species=species,
            head_count=getattr(advice.ledger, "head_count", None) or None,
            advice=advice, manyatta_id=(existing or {}).get("id"),
            water_source_id=getattr(advice, "point_id", None),
            pastoralist_id=getattr(pastoralist, "id", None), lang=lang)
    else:
        recorded = False

    # The one question that grades our own satellite reading against his eyes.
    conversation.set_state(phone, "graze.quality",
                           {"water_source_id": getattr(advice, "point_id", None),
                            "lon": lon, "lat": lat})
    return GrazingResult(
        ok=True, advice=advice, text=forage.message(advice, lang),
        spoken=forage.spoken_summary(advice, lang),
        question=forage.quality_question(lang), buttons=list(QUALITY_BUTTONS),
        recorded=recorded, duplicate=duplicate, ask_manyatta=ask_manyatta,
        point_id=getattr(advice, "point_id", None))


def handle_manyatta(phone: str, pastoralist, lat: float, lon: float,
                    source: str = "pin") -> GrazingResult:
    """Register the homestead (once), then immediately ask for today's walk.

    One message, not two: register, confirm, and hand him the location button in
    the same breath. The state moves straight to `graze.await`, because the next
    pin he sends is the GRAZING spot, not another manyatta.
    """
    lang = _lang_key(pastoralist)
    m = manyattas.register_manyatta(phone, lat, lon, source=source)
    if not m:
        return GrazingResult(ok=False, reason="register_failed",
                             text=forage.no_data_message(lang))
    url = map_url(phone, pastoralist, lon=lon, lat=lat)
    conversation.set_state(phone, "graze.await", {})
    if lang == "eng":
        head = "✅ Your manyatta is registered. I will not ask again."
    else:
        head = "✅ Manyatta yako imeandikishwa. Sitakuuliza tena."
    return GrazingResult(ok=True, reason="registered", prompt_location=True,
                         text=f"{head}\n\n{prompt_text(lang, url)}")
