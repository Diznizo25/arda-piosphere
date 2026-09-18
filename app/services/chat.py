"""
Grounded conversational layer: a herder asks a real question in WhatsApp and gets
an answer, instead of a menu.

Design rule (the same one that governs app/services/ai.py): the FACTS are always
produced by deterministic code. The language model is only ever allowed to *phrase*
facts it was handed. Concretely:

    question -> which fact sections? (keyword routing, no model)
             -> gather facts from our own data (advisory / rain / water status)
                 + curated knowledge entries (config/pastoral_knowledge.yaml)
             -> ONE model call to phrase it in the herder's language
             -> HARD VALIDATION: every number in the answer must exist in the
                facts, no URLs/markdown, right language, short enough
             -> on any failure: a deterministic answer built from the same facts

Why validation instead of trust: this bot tells a man whether to walk 200 cattle
to water. An invented distance or an invented "it will rain on Tuesday" is not a
typo, it is a lost herd. Anything the validator cannot trace back to the facts is
thrown away and replaced with the plain deterministic sentence.

Cost/latency: one call per question, capped per herder per day
(DAILY_LLM_LIMIT), because reasoning models bill hidden thinking tokens (measured
at 256-400 per exchange) and a runaway loop is the only way this gets expensive.
When the cap is hit or the model is missing, herders still get the deterministic
answer — the chat degrades, it never dies.

Disease is a hard line: symptom questions NEVER reach the model (see
disease_reply) because a plausible-sounding diagnosis can kill a herd.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache

import yaml

from app.config import CONFIG_DIR
from app.services import ai

log = logging.getLogger(__name__)

KNOWLEDGE_PATH = CONFIG_DIR / "pastoral_knowledge.yaml"
MAX_ANSWER_CHARS = 550
DAILY_LLM_LIMIT = 40

# Which fact sections a question asks for. Keyword routing is deliberate: it is
# deterministic, free, testable, and it never mis-routes a factual question into a
# hallucination the way a classifier model could.
RAIN_WORDS = ("mvua", "rain", "ukame", "drought", "kausha", "dry spell", "utabiri",
              "forecast", "hali ya hewa", "weather", "kunyesha")
WATER_WORDS = ("maji", "water", "kisima", "well", "borehole", "bwawa", "chanzo",
               "dam", "river", "mwito", "laga", "kunywa")
PASTURE_WORDS = ("malisho", "pasture", "forage", "nyasi", "grass", "grazing",
                 "malisho mabichi", "vci", "green")
HERD_WORDS = ("mifugo", "ng'ombe", "ngombe", "mbuzi", "kondoo", "ngamia", "cattle",
              "goat", "sheep", "camel", "kundi", "herd")

_SW_MARKERS = ("na", "ya", "kwa", "ni", "wa", "hii", "hiyo", "yako", "wako", "maji",
               "siku", "mvua", "malisho", "kama", "sana", "kabla", "au", "hapa",
               "kutoka", "wanahitaji", "anahitaji")
_EN_MARKERS = ("the", "is", "are", "and", "you", "your", "water", "rain", "days",
               "from", "with", "this", "that", "needs", "need", "pasture")

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_in(value) -> set[str]:
    """Every numeric token inside a string/list/dict, normalised as strings.

    Used both to collect the numbers we are allowed to say and to check what the
    model actually said. Decimal commas are normalised so "0,1" and "0.1" match.
    """
    if value is None:
        return set()
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {_norm_num(str(value))}
    if isinstance(value, dict):
        out: set[str] = set()
        for k, v in value.items():
            out |= numbers_in(k)
            out |= numbers_in(v)
        return out
    if isinstance(value, (list, tuple, set)):
        out = set()
        for v in value:
            out |= numbers_in(v)
        return out
    return {_norm_num(m) for m in _NUM_RE.findall(str(value))}


def _norm_num(token: str) -> str:
    return token.replace(",", ".").rstrip(".")


def _as_float(token: str) -> float | None:
    try:
        return float(token.replace(",", "."))
    except (TypeError, ValueError):
        return None


def allowed_numbers(*sources) -> set[str]:
    """Numbers that may legally appear in an answer, with rounding tolerance.

    A fact of 1.3 may be spoken as "1 mm" and a deficit of -98.5 as "98% below
    normal", so both the value and its absolute form are permitted, and the
    validator tolerates small rounding (see _number_is_traceable).
    """
    out: set[str] = set()
    for src in sources:
        for tok in numbers_in(src):
            out.add(tok)
            v = _as_float(tok)
            if v is not None:
                out.add(_norm_num(str(abs(v))))
    return out


def _number_is_traceable(token: str, allowed: set[str]) -> bool:
    """True when a number in the answer can be traced to a fact we hold."""
    if token in allowed:
        return True
    value = _as_float(token)
    if value is None:
        return True  # not really a number
    for tok in allowed:
        fact = _as_float(tok)
        if fact is None:
            continue
        if abs(value - fact) <= 0.5:          # rounding: 1.3 -> "1"
            return True
        if abs(value - fact) <= abs(fact) * 0.02:  # percent-level rounding
            return True
    return False


# --- curated knowledge -------------------------------------------------------

@lru_cache(maxsize=1)
def load_knowledge() -> dict:
    """The curated knowledge base. Cached; a broken file yields an empty base.

    An unreadable file must never break a herder's reply — the chat layer simply
    has no guidance to offer and says so.
    """
    try:
        with open(KNOWLEDGE_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        log.exception("knowledge base unreadable at %s (non-fatal)", KNOWLEDGE_PATH)
        return {}


def knowledge_entries() -> list[dict]:
    """All curated entries except the disease block (which is handled separately)."""
    base = load_knowledge()
    out: list[dict] = []
    for section, value in base.items():
        if section == "disease_warning_signs":
            continue
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict) and entry.get("id"):
                    out.append(entry)
    return out


def match_knowledge(question: str, limit: int = 2) -> list[dict]:
    """Curated entries whose keywords appear in the question (best match first)."""
    q = (question or "").lower()
    if not q:
        return []
    scored: list[tuple[int, dict]] = []
    for entry in knowledge_entries():
        hits = sum(1 for kw in (entry.get("keywords") or []) if kw.lower() in q)
        if hits:
            scored.append((hits, entry))
    scored.sort(key=lambda pair: -pair[0])
    return [entry for _, entry in scored[:limit]]


def disease_keywords() -> tuple[str, ...]:
    block = load_knowledge().get("disease_warning_signs") or {}
    return tuple(kw.lower() for kw in (block.get("keywords") or []))


def is_disease_question(question: str) -> bool:
    """Does this question smell like a symptom/disease question?

    Deliberately broad (any hit counts): the cost of routing a non-disease
    question to the vet-safety reply is one slightly unhelpful message, while the
    cost of the opposite is a herder acting on a guessed diagnosis.
    """
    q = (question or "").lower()
    return any(kw in q for kw in disease_keywords())


def disease_reply(lang: str = "swahili") -> str:
    """The ONLY answer to a symptom question — never touches the model."""
    block = load_knowledge().get("disease_warning_signs") or {}
    text = entry_text(block, lang) or entry_text(block, "swahili")
    return " ".join((text or "").split())


# The YAML stores short language keys (sw/en); the app speaks swahili/english.
_LANG_KEY = {"swahili": "sw", "english": "en"}


def entry_text(entry: dict, lang: str) -> str:
    """Localised text from a knowledge entry, tolerant of both key styles."""
    return (entry.get(lang) or entry.get(_LANG_KEY.get(lang, "sw")) or "").strip()


# --- routing -----------------------------------------------------------------

def intent_sections(question: str) -> list[str]:
    """Which fact sections this question is about (empty list = general question)."""
    q = (question or "").lower()
    sections: list[str] = []
    if any(w in q for w in RAIN_WORDS):
        sections.append("rain")
    if any(w in q for w in WATER_WORDS):
        sections.append("water")
    if any(w in q for w in PASTURE_WORDS):
        sections.append("pasture")
    if any(w in q for w in HERD_WORDS):
        sections.append("herd")
    return sections


def mentions_water_or_pasture(question: str) -> bool:
    return bool({"rain", "water", "pasture"} & set(intent_sections(question)))


# --- validation: the guardrail ----------------------------------------------

def validate_answer(answer: str, allowed: set[str],
                    lang: str = "swahili") -> tuple[bool, str]:
    """Is this answer safe to send? Returns (ok, reason-if-not).

    Checks, in order: non-empty, short enough, no markdown/URLs, no invented
    number, right language. Every failure sends the caller to the deterministic
    fallback, so nothing here can leak a fabricated fact to a herder.
    """
    text = (answer or "").strip()
    if not text:
        return False, "empty"
    if len(text) > MAX_ANSWER_CHARS:
        return False, "too_long"
    if "http://" in text or "https://" in text or "**" in text or "```" in text:
        return False, "markdown_or_url"
    for token in numbers_in(text):
        if not _number_is_traceable(token, allowed):
            return False, f"invented_number:{token}"
    words = {w.strip(".,!?;:()'\"").lower() for w in text.split()}
    if lang == "swahili":
        if not words & set(_SW_MARKERS):
            return False, "not_swahili"
    else:
        if not words & set(_EN_MARKERS):
            return False, "not_english"
    return True, ""



# --- fact gathering (our own data only; every part fail-open) ---------------

def gather_facts(pastoralist, question: str, sections: list[str] | None = None,
                 lat: float | None = None, lon: float | None = None) -> dict:
    """Assemble the facts bundle for this question, from stored data.

    Lazy imports on purpose: this module must stay importable (and testable)
    without rasterio, GEE or a database. Each section fails independently — a COG
    read error costs the herder the pasture paragraph, not the whole answer.
    """
    sections = sections if sections is not None else intent_sections(question)
    facts: dict = {}

    if not sections or {"water", "pasture", "herd"} & set(sections):
        facts.update(_advisory_facts(pastoralist, lat, lon))

    if not sections or "rain" in sections:
        facts.update(_rain_facts(pastoralist))

    entries = match_knowledge(question)
    if entries:
        facts["guidance"] = [
            {
                "text": entry_text(e, pastoralist.preferred_language),
                "source": e.get("source"),
                "reviewed": not e.get("needs_review", False),
            }
            for e in entries
        ]
    return facts


def _herder_location(pastoralist, lat: float | None, lon: float | None):
    """Where to compute the advisory: explicit coords, else the herder's last
    shared location. Returns (lat, lon) or None."""
    if lat is not None and lon is not None:
        return lat, lon
    try:
        from app.services.pastoralists import get_last_location

        loc = get_last_location(pastoralist.phone_number)
        if loc:
            return loc[1], loc[0]
    except Exception:  # noqa: BLE001
        log.debug("last location unavailable", exc_info=True)
    return None


def _advisory_facts(pastoralist, lat: float | None, lon: float | None) -> dict:
    """Water + pasture + reach facts, from the same pipeline the advisory uses."""
    coords = _herder_location(pastoralist, lat, lon)
    if not coords:
        return {}
    plat, plon = coords
    try:
        from app.models.schemas import AdvisoryRequest
        from app.services.advisory_service import get_advisory

        res = get_advisory(AdvisoryRequest(
            lat=plat, lon=plon,
            species=pastoralist.primary_species or "cattle",
            language=pastoralist.preferred_language,
            water_interval=getattr(pastoralist, "water_interval", None) or "daily",
        ))
    except Exception:  # noqa: BLE001
        log.exception("chat: advisory facts unavailable (non-fatal)")
        return {}

    if not res.found:
        return {"water": {"found": False},
                "herd": {"species": pastoralist.primary_species or "cattle"}}

    return {
        "water": {
            "found": True,
            "distance_km": res.distance_km,
            "reliability": res.water_reliability,
            "grazing_zone": res.grazing_zone,
        },
        "pasture": {
            "condition": res.forage_condition,
            "seasonally_normal": res.seasonally_normal,
            "vci": (res.raw_indices or {}).get("VCI"),
        },
        "reach": {"effective_radius_km": res.effective_radius_km},
        "herd": {"species": pastoralist.primary_species or "cattle"},
    }


def _rain_facts(pastoralist) -> dict:
    """Rain outlook from the stored series/forecast (never a live weather call)."""
    ws_id = getattr(pastoralist, "water_source_id", None)
    if not ws_id:
        return {}
    try:
        from app.services import environment
        from app.services.forecast import outlook_severity

        o = environment.outlook(ws_id, window_days=30)
    except Exception:  # noqa: BLE001
        log.exception("chat: rain facts unavailable (non-fatal)")
        return {}
    if o is None:
        return {}
    return {"rain": {
        "dry_spell_days": o.dry_spell_days,
        "observed_30d_mm": o.rain_30d_mm,
        "normal_30d_mm": o.normal_30d_mm,
        "deficit_pct": o.deficit_pct,
        "season": outlook_severity(o),
        "forecast_days": o.horizon_days,
        "forecast_total_mm": o.forecast_total_mm,
        "onset_date": o.onset_date.isoformat() if o.onset_date else None,
        "confidence": o.confidence if o.has_forecast else None,
    }}



# --- phrasing: one model call, then hard validation -------------------------

SW_SYSTEM = (
    "Wewe ni Arda Link, msaidizi wa WhatsApp kwa wachungaji wa Isiolo, Kenya. "
    "Jibu kwa Kiswahili rahisi. SHERIA KALI: (1) tumia tu FACTS ulizopewa - "
    "usibuni namba, umbali, tarehe, bei, dawa au jina la eneo lolote; (2) kama "
    "FACTS hazitoshi, sema hivyo na mwambie achague huduma au atume eneo lake; "
    "(3) usitoe utambuzi wa ugonjwa hata kidogo; (4) weka alama '(makadirio)' "
    "kwa mambo ya utabiri; (5) mstari 3 mafupi pekee, bila markdown, bila URL."
)
EN_SYSTEM = (
    "You are Arda Link, a WhatsApp assistant for pastoralists in Isiolo, Kenya. "
    "Reply in simple English. HARD RULES: (1) use ONLY the FACTS given - never "
    "invent a number, distance, date, price, drug or place name; (2) if FACTS are "
    "insufficient, say so and offer the services menu or ask for their location; "
    "(3) never give a disease diagnosis; (4) mark forecast statements as "
    "'(estimate)'; (5) at most 3 short lines, no markdown, no URLs."
)


def system_prompt(lang: str) -> str:
    return SW_SYSTEM if lang == "swahili" else EN_SYSTEM


def deterministic_answer(facts: dict, lang: str = "swahili") -> str:
    """The fail-open answer: built only from facts we hold, no model involved.

    Fact-driven on purpose: it speaks whatever the bundle contains (the routing in
    gather_facts already decided what is relevant), so there is no second, hidden
    filter that could silently drop something the herder needs.

    This is what a herder gets when the model is unavailable, over the daily cap,
    or produced something the validator rejected. It must therefore stand on its
    own — and it must never say more than the facts support.
    """
    sw = lang == "swahili"
    lines: list[str] = []

    rain = facts.get("rain") or {}
    if rain:
        bits: list[str] = []
        if rain.get("dry_spell_days") is not None:
            bits.append(f"siku {rain['dry_spell_days']} bila mvua ya maana" if sw
                        else f"{rain['dry_spell_days']} days without real rain")
        if rain.get("normal_30d_mm") is not None:
            bits.append(
                (f"mvua ya siku 30 ni {rain.get('observed_30d_mm')} mm dhidi ya "
                 f"kawaida {float(rain['normal_30d_mm']):.1f} mm") if sw else
                (f"30-day rain {rain.get('observed_30d_mm')} mm vs a normal of "
                 f"{float(rain['normal_30d_mm']):.1f} mm"))
        if rain.get("forecast_days"):
            bits.append(
                (f"utabiri wa siku {rain['forecast_days']}: "
                 f"{rain.get('forecast_total_mm')} mm (makadirio)") if sw else
                (f"{rain['forecast_days']}-day forecast: "
                 f"{rain.get('forecast_total_mm')} mm (estimate)"))
        if bits:
            lines.append(("Mvua: " if sw else "Rain: ") + "; ".join(bits) + ".")

    water = facts.get("water") or {}
    if water and water.get("found") is False:
        lines.append("Kwa eneo lako hatuna maji yanayofikika kwa sasa — angalia "
                     "maeneo mengine." if sw else
                     "Near your location we currently have no reachable water — "
                     "consider other areas.")
    elif water.get("distance_km") is not None:
        line = (f"Maji ya karibu yapo umbali wa {float(water['distance_km']):.1f} km." if sw
                else f"Nearest water is {float(water['distance_km']):.1f} km away.")
        if water.get("reliability"):
            line += f" ({water['reliability']})"
        lines.append(line)

    pasture = facts.get("pasture") or {}
    if pasture.get("condition"):
        lines.append((f"Malisho karibu na maji: {pasture['condition']}." if sw
                      else f"Pasture near that water: {pasture['condition']}."))

    for entry in (facts.get("guidance") or []):
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        marker = "" if entry.get("reviewed") else (
            " (mwongozo wa jumla — hakikiwa na mtaalam: bado)" if sw
            else " (general guidance — not yet reviewed by an expert)")
        lines.append(f"{text}{marker}")
        break  # one curated answer is enough in a chat reply

    if not lines:
        return ("Sijaelewa vizuri. Tuma eneo lako (location) au chagua huduma kwa "
                "'menu'." if sw else
                "I did not quite understand. Send your location, or send 'menu'.")
    return "\n".join(lines[:4])



def _log_chat(pastoralist, question: str, answer: str, used_llm: bool,
              reason: str = "") -> None:
    """Best-effort activity-log entry (drives the dashboard AND our cost ledger)."""
    try:
        from app.services import query_log

        query_log.log_query(
            kind="other",
            phone=getattr(pastoralist, "phone_number", None),
            species=getattr(pastoralist, "primary_species", None),
            result="ok",
            detail={"pipeline": "chat", "llm": used_llm, "reason": reason,
                    "q": (question or "")[:120], "chars": len(answer or "")},
        )
    except Exception:  # noqa: BLE001
        log.debug("chat log failed (non-fatal)", exc_info=True)


def llm_calls_today(phone: str) -> int:
    """How many grounded chat calls this herder has had today (fail-open: 0)."""
    try:
        from app.db import get_pg_connection

        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """select count(*) as n from query_log
                       where phone = %(phone)s
                         and detail->>'pipeline' = 'chat'
                         and detail->>'llm' = 'true'
                         and created_at >= date_trunc('day', now())""",
                    {"phone": phone},
                )
                row = cur.fetchone()
        return int((row or {}).get("n") or 0)
    except Exception:  # noqa: BLE001
        return 0


def answer(pastoralist, question: str, lang: str | None = None, *,
           gather=None, llm=None, counter=None) -> str | None:
    """Answer one herder question, or return None so the caller shows the menu.

    `gather`, `llm` and `counter` are injectable so the whole path is testable
    without a database, a model or a network — see scripts/test_chat_layer.py.
    """
    lang = lang or getattr(pastoralist, "preferred_language", "swahili")
    question = (question or "").strip()
    if not question:
        return None

    # 1) Disease questions never reach the model, and are never answered with a
    #    guess. This is the one place we deliberately answer less than asked.
    if is_disease_question(question):
        reply = disease_reply(lang)
        _log_chat(pastoralist, question, reply, used_llm=False,
                  reason="disease_guardrail")
        return reply

    sections = intent_sections(question)
    collect = gather or gather_facts
    try:
        facts = collect(pastoralist, question, sections) or {}
    except Exception:  # noqa: BLE001
        log.exception("chat: fact gathering failed")
        facts = {}

    if not facts:
        # Nothing to ground on: let the caller show the services menu.
        return None

    fallback = deterministic_answer(facts, lang)
    allowed = allowed_numbers(facts, question)

    # 2) One model call to phrase it, budget permitting. Reasoning models bill
    #    hidden thinking tokens, so the cap is about keeping a loop bounded, not
    #    about the per-answer price (which is a fraction of a cent).
    call_llm = llm or ai.grounded_answer
    phone = getattr(pastoralist, "phone_number", "") or ""
    if (counter or llm_calls_today)(phone) < DAILY_LLM_LIMIT:
        try:
            candidate = call_llm(
                system_prompt(lang),
                json.dumps(facts, ensure_ascii=False, default=str),
                question,
            )
        except Exception:  # noqa: BLE001
            log.exception("chat: model call failed")
            candidate = None
        if candidate:
            ok, why = validate_answer(candidate, allowed, lang)
            if ok:
                _log_chat(pastoralist, question, candidate, used_llm=True)
                return candidate
            log.warning("chat: rejected model answer (%s)", why)
            _log_chat(pastoralist, question, fallback, used_llm=False,
                      reason=f"rejected:{why}")
            return fallback

    _log_chat(pastoralist, question, fallback, used_llm=False, reason="deterministic")
    return fallback

