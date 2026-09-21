"""Thin, fail-open AI layer (Azure OpenAI GPT-5-mini).

Architecture principle: the advisory *facts* are always produced by the
deterministic pipeline (advisory_service -> raster_read -> advisory_logic).
The LLM is only ever allowed to:

  (a) classify free-text ground-truth reports into the existing report_type
      set (catching phrasing the keyword list misses), and
  (b) rephrase an already-computed advisory message.

If the key is missing, the call times out, or the model returns garbage,
every function falls back to the deterministic path, so WhatsApp never breaks.
"""
from __future__ import annotations

import logging
import re

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

TIMEOUT_S = 20.0
# GPT-5-mini is a reasoning model: it spends tokens on a reasoning pass before
# the answer. "low" effort keeps WhatsApp latency sane; the budget below leaves
# plenty of room for both the reasoning pass and the actual answer.
MAX_TOKENS = 1200

# The four real report types from ground_truth.py, plus "none" for noise.
REPORT_TYPES = ("water_dry", "water_available", "pasture_good", "pasture_poor", "none")
REAL_TYPES = REPORT_TYPES[:-1]


def _chat(system: str, user: str) -> str | None:
    """One chat-completions round trip against the Azure project endpoint.

    Returns the trimmed content, or None on any failure (missing config,
    non-200, timeout, network error, empty reply). Never raises.
    """
    settings = get_settings()
    if not settings.azure_openai_api_key or not settings.azure_openai_endpoint:
        return None
    try:
        with httpx.Client(
            base_url=settings.azure_openai_endpoint.rstrip("/"),
            headers={
                "api-key": settings.azure_openai_api_key,
                "Content-Type": "application/json",
            },
            timeout=TIMEOUT_S,
        ) as client:
            resp = client.post(
                "/openai/v1/chat/completions",
                json={
                    "model": settings.azure_openai_model or "gpt-5-mini",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_completion_tokens": MAX_TOKENS,
                    "reasoning_effort": "low",
                },
            )
        if resp.status_code != 200:
            log.warning("Azure OpenAI returned %s: %.160s", resp.status_code, resp.text)
            return None
        content = resp.json()["choices"][0]["message"].get("content") or ""
        return content.strip() or None
    except Exception:  # noqa: BLE001
        log.exception("Azure OpenAI call failed - falling back to deterministic path")
        return None


def classify_report(text: str) -> str | None:
    """Classify a herder's free-text reply into a report_type.

    The LLM catches phrasing the keyword list misses (e.g. "maji yalikauka
    jana" / "the well dried up last week"). The keyword parser remains the
    fallback whenever the LLM is unavailable or returns something outside the
    report_type set.
    """
    from app.services.ground_truth import parse_ground_truth_intent

    keyword_hit = parse_ground_truth_intent(text.lower())
    system = (
        "You classify WhatsApp messages from Kenyan pastoralists into exactly one "
        "category. Reply with ONLY the single category word, nothing else.\n"
        "Categories:\n"
        "- water_dry: a water point/well/pond is dry, empty, or gone\n"
        "- water_available: a water point has water\n"
        "- pasture_good: pasture/grass is good, plenty, or growing\n"
        "- pasture_poor: pasture/grass is bad, eaten, or missing\n"
        "- none: anything else (questions, greetings, noise)\n"
        "Messages may be in Swahili, English, or mixed SMS slang."
    )
    out = _chat(system, text)
    if out:
        match = re.search(r"\b(" + "|".join(REPORT_TYPES) + r")\b", out.lower())
        if match and match.group(1) in REAL_TYPES:
            return match.group(1)
    return keyword_hit


def grounded_answer(system: str, facts_json: str, question: str,
                    context: str = "") -> str | None:
    """One grounded chat turn: phrase FACTS in answer to a herder's question.

    This is the ONLY place a herder's free-text question reaches a model, and the
    model is not trusted with facts: the caller validates every number in the
    reply against the facts bundle (app/services/chat.py) and falls back to
    deterministic text if anything is invented, mistranslated or too long.

    `context` is the recap of earlier turns, explicitly labelled as NOT a source of
    facts — so a follow-up question ("and where should I go?") is understood without
    letting the recap introduce numbers the bundle does not contain.

    Returns None on any failure so the caller can fall back. Note the token budget:
    this is a reasoning model, and a small max_completion_tokens gets entirely
    consumed by the hidden reasoning pass, producing an EMPTY reply — measured at
    256-400 reasoning tokens per short exchange.
    """
    parts = []
    if context:
        parts.append(context)
    parts.append(f"FACTS={facts_json}")
    parts.append(f"SWALI LA MCHUNGAJI / HERDER'S QUESTION: {question}")
    return _chat(system, "\n\n".join(parts))


def _nums(text: str) -> set[str]:
    """Numeric tokens in a message, comma-decimals normalised (4,3 == 4.3)."""
    return {m.replace(",", ".") for m in re.findall(r"\d+(?:[.,]\d+)?", text or "")}


def rephrase_advisory_ok(base: str, out: str, distance_km: float | None) -> tuple[bool, str]:
    """May this rephrasing be sent instead of the original? (ok, reason-if-not)

    The model is allowed to improve the PHRASING of an advisory, nothing else — and
    a live inspection showed it happily destroying the structure instead: five clean
    lines came back as one 309-character run with six colons. So the guard is
    structural, not just factual:

      * every number in the original must survive (a dropped number is a lost fact),
      * no semicolons and at most one colon per line (the tell-tale of an LLM
        collapsing a list into a sentence),
      * it may not merge lines — the original line breaks ARE the formatting,
      * the distance must still be there.

    On rejection the caller sends the deterministic text, which is now written to be
    read aloud in the first place.
    """
    if not out.strip():
        return False, "empty"
    missing = _nums(base) - _nums(out)
    if missing:
        return False, f"dropped_number:{sorted(missing)[0]}"
    if ";" in out:
        return False, "semicolon"
    for line in out.splitlines():
        if line.count(":") > 1:
            return False, "colon_run"
    if len(out.splitlines()) < len(base.splitlines()):
        return False, "lines_merged"
    if distance_km is not None:
        forms = {f"{distance_km:.1f}", f"{distance_km:g}"}
        if not any(form in out for form in forms):
            return False, "distance_lost"
    return True, ""


def rephrase_advisory(language: str, base_message: str,
                      distance_km: float | None = None) -> str:
    """Reword the deterministic advisory without changing or losing any fact.

    Fail-open twice over: if the model is unavailable OR its output fails the
    structural guard above, the original deterministic message is returned.
    """
    if not base_message or distance_km is None:
        return base_message
    if not get_settings().advisory_rephrase_enabled:
        return base_message
    system = (
        "You are the text editor for a pastoralist water-and-pasture advisory bot. "
        "Rewrite the message you are given in natural, friendly plain language for a "
        "pastoralist. HARD RULES: keep the SAME language as the input; keep EVERY "
        "number exactly as written; keep the SAME number of lines (the line breaks "
        "are deliberate); never use semicolons, and never use colons to join clauses; "
        "no markdown, no URLs; do not add or remove a fact, distance or "
        "recommendation."
    )
    out = _chat(system, base_message)
    if not out:
        return base_message
    ok, why = rephrase_advisory_ok(base_message, out, distance_km)
    if not ok:
        log.warning("LLM rephrase rejected (%s) - keeping the deterministic text", why)
        return base_message
    return out[:600]
