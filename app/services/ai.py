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


def rephrase_advisory(language: str, base_message: str,
                      distance_km: float | None = None) -> str:
    """Reword the deterministic advisory without adding or changing facts.

    Safety guardrail: if the LLM output drops the distance figure, or the LLM
    is unavailable, the original deterministic message is returned untouched.
    """
    if not base_message or distance_km is None:
        return base_message
    system = (
        "You are the text rewriter for a pastoralist water-and-pasture advisory "
        "bot. Rewrite the message you are given in natural, friendly plain "
        "language for a pastoralist. Hard rules: keep the SAME language as the "
        "input; do not add, remove, or change any fact, number, distance, or "
        "recommendation; no markdown, no emojis, no bullet points; max 2 short "
        "lines."
    )
    out = _chat(system, base_message)
    if not out:
        return base_message
    distance_forms = {f"{distance_km:.1f}", f"{distance_km:g}"}
    if not any(form in out for form in distance_forms):
        log.warning("LLM rephrase dropped the distance (%s) - keeping deterministic text",
                    distance_km)
        return base_message
    return out[:600]
