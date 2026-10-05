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
    "swahili": "🌿 MALISHO YA LEO\n\n"
               "Gusa kitufe hapa chini kufungua RAMANI, kisha gusa mahali "
               "mlipopanda leo.\n"
               "Kijani = malisho mazuri · kahawia = nyasi kavu.\n\n"
               "Au tuma eneo lako (📎 → Location), au andika jina la mahali.",
    "english": "🌿 GRAZING TODAY\n\n"
               "Tap the button below to open the MAP, then tap the spot where "
               "they grazed today.\n"
               "Green = good forage · brown = dry grass.\n\n"
               "Or send your location (📎 → Location), or type the place name.",
}

# Only used on the fallback path, where no button could be sent and the link has
# to travel inside the text.
GRAZE_PROMPT_MAP = {
    "swahili": "🗺 Fungua ramani: {url}",
    "english": "🗺 Open the map: {url}",
}

MAP_BUTTON = {"swahili": "🗺 Fungua ramani", "english": "🗺 Open the map"}


def prompt_text(lang: str, url: str = "") -> str:
    """The 'where did they graze' question.

    The map is the PRIMARY instruction, because a map a herder can touch asks him
    for one tap instead of a GPS gesture he may not know. The URL is appended only
    when it could not travel as a button.
    """
    key = "english" if lang == "eng" else "swahili"
    out = GRAZE_PROMPT[key]
    if url:
        out += "\n" + GRAZE_PROMPT_MAP[key].format(url=url)
    return out

MANYATTA_PROMPT = {
    "swahili": "🏠 MANYATTA YAKO\n\nKwanza niandikishe manyatta yako — mara moja tu, "
               "na nakumbuka daima.\nGusa kitufe hapa chini kutuma eneo la manyatta "
               "yako (mahali wanyama wanalala).",
    "english": "🏠 YOUR MANYATTA\n\nFirst let me register your manyatta — once only, "
               "and I remember it.\nTap the button below to send your manyatta "
               "location (where the animals sleep).",
}

LOCATION_BUTTON = {"swahili": "Tuma eneo langu 📍", "english": "Send my location 📍"}


def _lang_key(pastoralist) -> str:
    return "swa" if getattr(pastoralist, "preferred_language", "swahili") == "swahili" else "eng"


def map_url(phone: str, pastoralist, lon: float | None = None,
            lat: float | None = None, point_id: str | None = None) -> str:
    """The tap-the-map link, carrying a token instead of a phone number.

    When the covering water point is known we pass `id=`, which is what makes the
    page draw the species rings AND the satellite pasture layer: a herder choosing
    where the herd grazed needs to SEE where the grass is, not tap a blank street
    map. The tap handler then posts the chosen spot back (POST /grazing/pin).
    """
    from app.config import get_settings

    token = mint_token(phone)
    if not token:
        return ""
    base = (get_settings().app_public_base_url or "").rstrip("/")
    if not base:
        return ""
    species = getattr(pastoralist, "primary_species", None) or "cattle"
    interval = getattr(pastoralist, "water_interval", None) or "daily"
    query = (f"lat={lat or 0.35}&lon={lon or 37.58}&species={species}"
             f"&interval={interval}&lang={_lang_key(pastoralist)}&graze=1&t={token}")
    if point_id:
        query += f"&id={point_id}"
    return f"{base}/mapview/?{query}"


def map_image_url(pastoralist, lon: float | None, lat: float | None,
                  point_id: str | None) -> str | None:
    """The PNG of the same map, publicly reachable, for the message's image header.

    The picture is what tells him WHICH map the button opens, and it carries its
    own age stamp (`picha ya 05 Oct — kadirio`), so a forwarded image cannot look
    fresher than it is.
    """
    from app.config import get_settings

    base = (get_settings().app_public_base_url or "").rstrip("/")
    if not base or not point_id or lon is None or lat is None:
        return None
    species = getattr(pastoralist, "primary_species", None) or "cattle"
    interval = getattr(pastoralist, "water_interval", None) or "daily"
    confirm = getattr(pastoralist, "water_source_id", None) or ""
    return (f"{base}/map/{point_id}.png?lat={lat}&lon={lon}&species={species}"
            f"&pasture=1&lang={_lang_key(pastoralist)}&confirm={confirm}"
            f"&interval={interval}&v=9")


def send_pin_prompt(phone: str, pastoralist, text: str, *,
                    lon: float | None = None, lat: float | None = None,
                    point_id: str | None = None) -> bool:
    """Send the 'where did they graze' prompt. THE one place the UX rule lives.

    Map first (a CTA button that opens the tap-the-map page, with the map itself as
    the message header), then — only if no form of the link could be sent — the
    one-tap location button, which is more precise anyway because it is his own GPS
    at the spot. Never both: two messages asking for the same thing is how a herder
    learns to ignore us.

    Returns True when the tappable map reached him.
    """
    if point_id is None and lon is not None and lat is not None:
        species = getattr(pastoralist, "primary_species", None) or "cattle"
        interval = getattr(pastoralist, "water_interval", None) or "daily"
        point_id, _how = water_source_for_pin(lon, lat, species, interval)

    url = map_url(phone, pastoralist, lon=lon, lat=lat, point_id=point_id)
    button = MAP_BUTTON["english" if _lang_key(pastoralist) == "eng" else "swahili"]
    if url:
        try:
            if whatsapp_client.send_cta_url_button(
                    phone, text, button, url,
                    image_url=map_image_url(pastoralist, lon, lat, point_id)):
                return True
        except Exception:  # noqa: BLE001
            log.exception("map CTA button failed (non-fatal)")

    lang = _lang_key(pastoralist)
    try:
        whatsapp_client.send_location_request(
            phone, prompt_text(lang, url) if not url else f"{text}\n{url}",
            LOCATION_BUTTON["english" if lang == "eng" else "swahili"])
    except Exception:  # noqa: BLE001
        log.exception("location-request fallback failed (non-fatal)")
    return False


def prompt_for_pin(phone: str, pastoralist, *, lon: float | None = None,
                   lat: float | None = None) -> None:
    """Ask for the grazing spot, centred on where the herd starts from.

    The origin (manyatta, else water point, else his last pin) is what the map is
    drawn around, so the field he sees is the range he actually walks.
    """
    if lon is None or lat is None:
        origin = origin_for(phone, pastoralist)
        lon, lat = origin.get("lon"), origin.get("lat")
    lang = _lang_key(pastoralist)
    conversation.set_state(phone, "graze.await", {})
    send_pin_prompt(phone, pastoralist, prompt_text(lang), lon=lon, lat=lat)


def prompt_for_manyatta(phone: str, pastoralist) -> None:
    """Register the homestead once. We never ask again after this."""
    lang = _lang_key(pastoralist)
    conversation.set_state(phone, "graze.manyatta", {})
    try:
        whatsapp_client.send_location_request(
            phone, MANYATTA_PROMPT["english" if lang == "eng" else "swahili"],
            LOCATION_BUTTON["english" if lang == "eng" else "swahili"])
    except Exception:  # noqa: BLE001
        log.exception("manyatta prompt failed (non-fatal)")


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


def patch_candidates(lon: float, lat: float, species: str, interval: str,
                     hint_id: str | None = None, limit: int = 4) -> list[str]:
    """Water points to try for a tap, most plausible FIRST.

    The order is the fix for a real complaint: a herder tapped a spot on the
    satellite picture WE drew for him and was told "we have no satellite picture".
    The page knows which raster it painted (`id=`), so that one is tried first — he
    tapped pixels we showed him, so we read the same file. Only then do we guess
    from the coordinates, because the nearest registered point is not necessarily
    the one whose picture covers the tap: a brand-new point has no raster at all,
    and its rings can still be the nearest thing on the map.
    """
    from app.config import get_species_rings
    from app.services import water_reach

    out: list[str] = []
    if hint_id:
        out.append(str(hint_id))

    # the point the rest of the system would pick, exactly as before
    pid, _how = water_source_for_pin(lon, lat, species, interval)
    if pid:
        out.append(str(pid))

    # ...and neighbours, because the first guess may have no raster yet
    try:
        eff = get_species_rings().effective_radius_km(species, interval)
        for c in water_reach.find_nearest_reachable_water(
                lon, lat, species, limit=limit, effective_radius_km=eff):
            out.append(str(c.water_source_id))
    except Exception:  # noqa: BLE001
        log.debug("reach lookup failed for candidates", exc_info=True)
    try:
        for n in water_reach.list_nearby_water_sources(lon, lat, limit=limit):
            if n.get("id"):
                out.append(str(n["id"]))
    except Exception:  # noqa: BLE001
        log.debug("nearby lookup failed for candidates", exc_info=True)

    seen: set[str] = set()
    final: list[str] = []
    for candidate in out:
        if candidate not in seen:
            seen.add(candidate)
            final.append(candidate)
    return final[:limit]


def sample_candidates(candidate_ids: list[str], lon: float, lat: float, *,
                      sample_fn=None) -> tuple[object | None, str | None, str]:
    """The first candidate whose raster can actually be READ at that spot.

    Returns (sample, point_id, reason) with reason
    "ok" | "outside" | "no_raster" | "no_point" — because a herder is owed the
    difference between "we have not measured there", "the point near you is still
    being built" and "there is no water point anywhere near that spot".
    """
    reasons: list[str] = []
    for pid in candidate_ids:
        if sample_fn is not None:
            sample = sample_fn(pid, lon, lat)
            if sample is not None:
                return sample, pid, "ok"
            reasons.append("outside")
            continue
        sample, why = forage.sample_patch_detailed(pid, lon, lat)
        if sample is not None:
            return sample, pid, "ok"
        reasons.append("no_raster" if why == "no_overview" else "outside")
    if not candidate_ids:
        return None, None, "no_point"
    return None, None, ("no_raster" if all(r == "no_raster" for r in reasons) else "outside")


def analyse(phone: str, pastoralist, lat: float, lon: float, *,
            point_id_hint: str | None = None, sample_fn=None, guidance_fn=None,
            temp_max_c: float | None = None):
    """Pin -> GrazingAdvice. Returns (advice|None, reason).

    `reason` says WHY there is no reading, because the herder is owed the
    difference: "no_point" (no water point registered anywhere near that spot),
    "no_raster" (the point near him is still being built), "outside" (we measure
    forage, but not that far). None of them means "no satellite picture" — the map
    he was just looking at IS a satellite picture.
    """
    species = getattr(pastoralist, "primary_species", None) or "cattle"
    interval = getattr(pastoralist, "water_interval", None) or "daily"
    lang = _lang_key(pastoralist)

    candidates = patch_candidates(lon, lat, species, interval, hint_id=point_id_hint)
    sample, point_id, reason = sample_candidates(candidates, lon, lat, sample_fn=sample_fn)
    if sample is None:
        log.info("grazing pin unreadable: reason=%s candidates=%d", reason, len(candidates))
        return None, reason

    snapshot = snapshot_for(point_id)
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
    # The spot this result is about (the manyatta when we just registered it), so
    # the caller can centre the next prompt's map on it without a second lookup.
    lon: float | None = None
    lat: float | None = None


QUALITY_BUTTONS = [("gq:1", "1 · mazuri"), ("gq:2", "2 · kati"), ("gq:3", "3 · mabaya")]


def _is_duplicate(phone: str) -> bool:
    """Did he just report a walk? A double tap must not double the ledger."""
    _eid, mins = manyattas.recent_event_minutes(phone)
    return mins is not None and mins < DEDUPE_MINUTES


def handle_pin(phone: str, pastoralist, lat: float, lon: float,
               source: str = "pin", *, point_id_hint: str | None = None,
               sample_fn=None, guidance_fn=None,
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
        advice, reason = analyse(phone, pastoralist, lat, lon,
                                 point_id_hint=point_id_hint, sample_fn=sample_fn,
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
            ok=False, reason=reason, text=forage.no_data_message(lang, reason),
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
    # The next input is the GRAZING spot, so this is where the tap-the-map prompt
    # belongs (the caller sends it through send_pin_prompt, map first).
    return GrazingResult(ok=True, reason="registered", prompt_location=True,
                         text=f"{head}\n\n{prompt_text(lang)}", lon=lon, lat=lat)
