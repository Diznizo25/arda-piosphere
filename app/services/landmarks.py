"""
Landmark intake: a herder says where they are IN WORDS and the system understands.

Most herders will never send a WhatsApp location pin. They will type what they say
out loud:

    "niko karibu na Oldonyo Sabor"
    "I am at Wamba market"
    "nipo Lengwenyi"

So this module resolves a free-text mention to coordinates, and it searches TWO
name spaces, because a herder's landmarks are a mix of the two:

  1. the gazetteer (config/landmarks.geojson: towns, villages, hamlets, markets,
     rivers, peaks — built by scripts/build_landmarks.py), and
  2. OUR OWN water points (water_sources.name) — "Lengwenyi well" is a landmark to
     the herder standing next to it, even though it is not in the gazetteer.

Matching is deliberately conservative and explainable:

  * spelling tolerance (difflib) so "Oldonyo Sabor" matches "Ol Donyo Sabor",
  * token-subset matching so "niko karibu na Lengwenyi" matches "Lengwenyi well",
  * stopword removal for the words that mean "I am near" (niko/nipo/hapa/karibu
    na/near/at...), which are not part of any name,
  * kind hints ("mto"/"river", "soko"/"market", "mlima"/"peak") to break ties,
  * ambiguity is REPORTED, never guessed: two villages 40 km apart that both match
    go back as candidates for the herder to confirm. Guessing here would send a
    herd to the wrong side of the county.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from app.config import CONFIG_DIR

log = logging.getLogger(__name__)

GAZETTEER_PATH = CONFIG_DIR / "landmarks.geojson"

# Words that describe where someone is, not which place they mean.
_STOPWORDS = {
    "niko", "nipo", "hapa", "hapo", "karibu", "na", "kwa", "ya", "wa", "ni", "nami",
    "mimi", "uko", "yuko", "tuko", "sasa", "hivi", "sisi", "wewe", "leo",
    "ndani", "upande", "eneo", "mahali", "kutoka", "toka",
    "i", "am", "im", "at", "the", "a", "an", "near", "nearby", "close", "to",
    "my", "our", "we", "are", "is", "in", "of", "and", "here", "around",
    "location", "place", "area",
}
# Kind words that hint at which KIND of landmark is meant (word -> gazetteer kind).
_KIND_HINTS = {
    "mto": "river", "river": "river", "mlima": "peak", "kilima": "peak",
    "hill": "peak", "peak": "peak", "soko": "market", "market": "market",
    "kijiji": "village", "village": "village", "hamlet": "hamlet",
    "mji": "town", "town": "town", "city": "city",
}
# Rank by kind when scores tie: herders orient by settlements and markets first.
_KIND_RANK = {"city": 0, "town": 1, "market": 2, "village": 3, "hamlet": 4,
              "peak": 5, "river": 6}

MIN_SCORE = 0.74        # below this we do not associate a name at all
TIE_MARGIN = 0.06       # score gap within which matches count as a tie
SAME_PLACE_KM = 5.0     # a tie this close is the same place, not ambiguous
MAX_NEAR_KM = 75.0      # a known location this close can pick between same-named places


@dataclass
class Landmark:
    name: str
    lat: float
    lon: float
    kind: str
    source: str = "gazetteer"        # gazetteer | water_point
    water_source_id: str | None = None
    score: float = 0.0
    dist_km: float | None = None

    @property
    def label(self) -> str:
        return self.name

    def as_dict(self) -> dict:
        return {"name": self.name, "lat": self.lat, "lon": self.lon, "kind": self.kind,
                "source": self.source, "water_source_id": self.water_source_id,
                "score": round(self.score, 3), "dist_km": self.dist_km}


@dataclass
class Resolution:
    """What the herder probably meant, and how sure we are allowed to be."""

    status: str                       # matched | ambiguous | none
    best: Landmark | None = None
    candidates: list[Landmark] = field(default_factory=list)
    reason: str = ""

    @property
    def matched(self) -> bool:
        return self.status == "matched" and self.best is not None


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def normalise(text: str) -> str:
    """Lower-cased, punctuation-free, accent-free, single-spaced."""
    text = _strip_accents((text or "").lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str, keep_stopwords: bool = False) -> list[str]:
    words = [w for w in normalise(text).split() if w]
    if keep_stopwords:
        return words
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (public: callers ranking places need this)."""
    from math import asin, cos, radians, sin, sqrt

    r = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = (sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2)
    return 2 * r * asin(sqrt(a))


_haversine_km = haversine_km  # kept for the short internal spelling


@lru_cache(maxsize=1)
def load_gazetteer() -> tuple[Landmark, ...]:
    """Named places from config/landmarks.geojson. () when unavailable.

    Cached: the file is a build artefact, not runtime state, and a few hundred
    entries are scored in microseconds.
    """
    try:
        with open(GAZETTEER_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        log.warning("landmark gazetteer unavailable at %s", GAZETTEER_PATH)
        return ()

    out: list[Landmark] = []
    for feature in data.get("features") or []:
        props = feature.get("properties") or {}
        name = (props.get("name") or "").strip()
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if not name or len(coords) < 2:
            continue
        try:
            lon, lat = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            continue
        out.append(Landmark(name=name, lat=lat, lon=lon,
                            kind=(props.get("kind") or "place")))
    return tuple(out)


_WP_CACHE: dict = {"at": 0.0, "marks": []}
# Water-point names change rarely (a new pin at most), and this list is read on
# every candidate message: a 5-minute cache keeps landmark intake off the DB.
_WP_CACHE_TTL_S = 300.0


def water_point_landmarks() -> list[Landmark]:
    """Water points that have a NAME - the landmarks herders actually cite.

    Fail-open (no DB, no rows, any error => []): landmark intake must never be the
    reason a message fails. Cached for _WP_CACHE_TTL_S seconds.

    This reads every named point, which is fine at county scale; once there are
    thousands, move the fuzzy scoring into Postgres (pg_trgm) instead of growing
    this list in memory.
    """
    import time

    now = time.time()
    if _WP_CACHE["marks"] and (now - _WP_CACHE["at"]) < _WP_CACHE_TTL_S:
        return list(_WP_CACHE["marks"])
    try:
        from app.db import get_pg_connection

        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """select id, name, water_type, st_x(geom) as lon, st_y(geom) as lat
                       from water_sources
                       where name is not null and length(trim(name)) > 2"""
                )
                rows = cur.fetchall()
        marks = [Landmark(name=r["name"], lat=float(r["lat"]), lon=float(r["lon"]),
                          kind=(r["water_type"] or "water"), source="water_point",
                          water_source_id=str(r["id"]))
                 for r in rows if r["name"] and r["name"].strip()]
    except Exception:  # noqa: BLE001
        log.debug("water-point landmark lookup failed (non-fatal)", exc_info=True)
        return list(_WP_CACHE["marks"])  # stale beats nothing

    if marks:
        _WP_CACHE["marks"], _WP_CACHE["at"] = marks, now
    return marks


def all_landmarks(include_water_points: bool = True) -> list[Landmark]:
    marks = list(load_gazetteer())
    if include_water_points:
        marks.extend(water_point_landmarks())
    return marks


def score_name(message_tokens: list[str], name: str,
               kind_hints: set[str], kind: str) -> float:
    """How strongly does a free-text message refer to this name? 0.0 - 1.0.

    The ladder, best first:
      1.00  the whole message IS the name ("Lengwenyi")
      0.97  every name token appears in the message ("niko karibu na lengwenyi")
      0.85+ fuzzy: the best-matching window of message tokens vs the name, which
            tolerates spelling variation ("oldonyo sabor" vs "oldonyo sabo")
      +0.03 if the message's kind word agrees with the landmark's kind
    """
    name_tokens = tokens(name)
    if not name_tokens:
        return 0.0
    name_norm = " ".join(name_tokens)
    msg_norm = " ".join(message_tokens)
    if not msg_norm:
        return 0.0

    bonus = 0.03 if (kind_hints and kind in kind_hints) else 0.0
    if msg_norm == name_norm:
        return min(1.0, 1.0 + bonus)
    if set(name_tokens) <= set(message_tokens):
        return min(1.0, 0.97 + bonus)

    # Fuzzy: slide a window the size of the name across the message tokens.
    width = len(name_tokens)
    best = 0.0
    for start in range(max(1, len(message_tokens) - width + 1)):
        window = " ".join(message_tokens[start:start + width])
        best = max(best, difflib.SequenceMatcher(None, window, name_norm).ratio())
    # A single distinctive token can identify a multi-word name too ("Oldonyo"
    # alone for "Oldonyo Sabor"), but never above the exact/subset rungs.
    longest = max(name_tokens, key=len)
    if len(longest) >= 5:
        for tok in message_tokens:
            if len(tok) < 4:
                continue
            ratio = difflib.SequenceMatcher(None, tok, longest).ratio()
            if ratio >= 0.85:
                best = max(best, 0.80 + 0.15 * ratio)
    return min(0.96, best + bonus)



# Words owned by the OTHER services (water / pasture / herd / rain / tools). A
# message containing these is not a bare place statement even if it starts with
# "niko karibu na". Defined here rather than imported from chat.py so this module
# stays standalone (and does not import a module that imports it).
_OTHER_SERVICE_WORDS = (
    "maji", "water", "kisima", "well", "borehole", "chanzo", "source", "bwawa",
    "dam", "mto", "river",
    "malisho", "pasture", "forage", "nyasi", "grass", "vci", "kijani",
    "mifugo", "ng'ombe", "ngombe", "mbuzi", "kondoo", "ngamia", "cattle", "goat",
    "sheep", "camel", "herd", "kundi",
    "mvua", "rain", "ukame", "drought", "utabiri", "forecast", "hali ya hewa",
    "ramani", "map", "uzito", "weight", "pima", "pin", "status", "hali", "menu",
    "huduma",
)


def looks_like_place_statement(text: str) -> bool:
    """Does this read as "I am at X" rather than a question about anything else?

    Conservative on purpose: a false positive here means replying "I don't know that
    place" to a legitimate question, which is worse than falling through to the chat
    layer. So it must say where, be short, and mention nothing the other services own
    (no digits, no water/pasture/herd/rain words).
    """
    words = tokens(text, keep_stopwords=True)
    if not words or len(words) > 5:
        return False
    if any(ch.isdigit() for ch in text):
        return False
    if not ({"niko", "nipo", "hapa", "karibu", "near", "at"} & set(words)):
        return False
    normalised = normalise(text)
    return not any(word in normalised for word in _OTHER_SERVICE_WORDS)


def unknown_place_reply(text: str, lang: str = "swahili") -> str:
    """Honest answer when the herder names a place we cannot find.

    We do NOT guess and we do NOT silently show a menu: we say we don't know the
    name and ask for the one thing that always works, a WhatsApp location pin.
    """
    sw = lang != "english"
    return ("Samahani, sijui eneo hilo. Tuma eneo lako (location) kwa WhatsApp "
            "ili nikupe taarifa za mahali ulipo — maji, malisho na mvua." if sw else
            "Sorry, I do not know that place. Send your location on WhatsApp and I "
            "will tell you about water, pasture and rain where you are.")


def resolve(text: str, min_score: float = MIN_SCORE, limit: int = 5,
            include_water_points: bool = True,
            near: tuple[float, float] | None = None) -> Resolution:
    """Find the landmark a herder means by name.

    Returns matched (safe to answer directly), ambiguous (ask which one) or none.

    Duplicate names are the NORMAL case, not an edge case: measured on our own
    gazetteer, 194 names appear in more than one place far apart ("Ewaso Nyiro"
    runs 130 km, "Kamanga" is both a peak and a river 126 km away). An exact match
    therefore proves nothing about WHICH one, so:

      * if a `near` location is known (their last pin, their water point), the
        closest matching place wins - we already know roughly where they are, and
        that beats interrogating them;
      * otherwise we ask, with the kind of each place so the names are at least
        distinguishable.
    """
    message_tokens = tokens(text)
    if not message_tokens:
        return Resolution("none", reason="no_name_words")

    kind_hints = {kind for word, kind in _KIND_HINTS.items() if word in normalise(text)}

    scored: list[Landmark] = []
    for mark in all_landmarks(include_water_points=include_water_points):
        score = score_name(message_tokens, mark.name, kind_hints, mark.kind)
        if score >= min_score:
            scored.append(Landmark(name=mark.name, lat=mark.lat, lon=mark.lon,
                                   kind=mark.kind, source=mark.source,
                                   water_source_id=mark.water_source_id, score=score))
    if not scored:
        return Resolution("none", reason="no_match")

    # Best score first; on ties prefer the kind a herder orients by, then the
    # shorter (usually the settlement, not "X market centre") name.
    scored.sort(key=lambda m: (-m.score, _KIND_RANK.get(m.kind, 9), len(m.name)))

    # Competing NAMES are a scoring question, not an ambiguity question: take the
    # best. Ambiguity only arises among candidates that share the SAME name, which
    # is where neither score nor spelling can tell them apart.
    best = scored[0]
    top_name = normalise(best.name)
    same_name = [m for m in scored if normalise(m.name) == top_name][:limit]
    contenders = same_name or [best]
    if near is not None:
        for mark in contenders:
            mark.dist_km = round(_haversine_km(near[0], near[1], mark.lat, mark.lon), 1)
        contenders.sort(key=lambda m: (m.dist_km if m.dist_km is not None else 1e9,
                                       -m.score))

    distinct = _distinct_places(contenders, contenders[0])
    if len(distinct) <= 1:
        return Resolution("matched", best=contenders[0], candidates=contenders)

    # More than one distinct place shares this name.
    if near is not None:
        closest = contenders[0]
        if closest.dist_km is not None and closest.dist_km <= MAX_NEAR_KM:
            return Resolution("matched", best=closest, candidates=contenders,
                              reason="nearest_to_known_location")
        return Resolution("ambiguous", best=None, candidates=distinct[:limit],
                          reason="named_places_all_far_from_known_location")

    # No context to disambiguate with: distances would be meaningless here, so the
    # caller should ask using names + kinds only.
    for mark in contenders:
        mark.dist_km = None
    return Resolution("ambiguous", best=None, candidates=distinct[:limit],
                      reason=f"{len(distinct)}_places_share_this_name")


def _distinct_places(candidates: list[Landmark], best: Landmark) -> list[Landmark]:
    """Candidates that are NOT the same physical place as `best`.

    A village and the market inside it score nearly identically and sit within a few
    kilometres: that is one place with two names, not a genuine ambiguity.
    """
    out: list[Landmark] = []
    for mark in candidates:
        if mark is best:
            continue
        if _haversine_km(best.lat, best.lon, mark.lat, mark.lon) <= SAME_PLACE_KM:
            continue
        out.append(mark)
    return out


def describe(marks: list[Landmark], lang: str = "swahili") -> str:
    """A numbered "which one did you mean?" list for the herder to answer.

    Includes the kind and (when a reference point exists) the distance, because two
    places with the same name are otherwise indistinguishable in a list. When even
    that fails to distinguish them - four points along the same river, all called the
    same thing - the list is replaced by a request for a location pin, which is the
    only thing that can actually answer the question.
    """
    sw = lang != "english"
    kind_words = {
        "city": "mji" if sw else "city", "town": "mji" if sw else "town",
        "market": "soko" if sw else "market", "village": "kijiji" if sw else "village",
        "hamlet": "kijiji" if sw else "hamlet", "peak": "mlima" if sw else "peak",
        "river": "mto" if sw else "river", "water": "maji" if sw else "water",
        "well": "kisima" if sw else "well", "borehole": "kisima" if sw else "borehole",
        "pan": "bwawa" if sw else "pan", "dam": "bwawa" if sw else "dam",
    }

    # Same name AND same kind AND no distinguishing distance = the list is useless
    # (e.g. four points along "Ewaso Nyiro"). Ask for the location instead.
    same_label = len({(normalise(m.name), m.kind) for m in marks}) == 1
    no_distance = all(m.dist_km is None for m in marks)
    if marks and same_label and no_distance:
        return ("Jina hilo linaonekana sehemu kadhaa (k.m. mto huo huo unapita maeneo "
                "mengi), na siwezi kuwaambia ni sehemu ipi bila kujua ulipo. Tuma eneo "
                "lako (location) kwa WhatsApp." if sw else
                "That name appears in several places (the same river passes many "
                "areas), and I cannot tell which part you mean without knowing where "
                "you are. Send your location on WhatsApp.")

    lines = [("Nimeona maeneo kadhaa yenye jina hilo. Ni yupi?" if sw
              else "I found several places with that name. Which one?")]
    for i, mark in enumerate(marks, start=1):
        kind = kind_words.get(mark.kind, mark.kind)
        dist = f" - {mark.dist_km:.0f} km" if mark.dist_km is not None else ""
        lines.append(f"{i}. {mark.name} ({kind}{dist})")
    lines.append(("Jibu kwa namba, au tuma eneo lako (location) kama si mojawapo." if sw
                  else "Reply with the number, or send your location if it is none of them."))
    return "\n".join(lines)

