"""
The mentor layer — one guarded rewrite for every service.

Why this exists: the rules compute what is true (a fodder band, an energy gap, a
parasite window, a water status), and rules write flat. A herder does not read
bands, he reads a person who knows the land telling him what today means. So the
model is allowed to do ONE thing: say the same facts like a mentor would.

The architecture rule is unchanged from the day this codebase started — **rules
compute, the model only rephrases** — and this module is the guard that makes that
true for EVERY service, not just the water advisory that had it first:

  * every number in the source text must survive, in the same form (a dropped figure
    is a lost fact), and NO new number may appear (an invented figure is worse);
  * the safety labels of the source text must survive: if it said kadirio (estimate)
    or named a vet, so does the rewrite;
  * no drug, dose, injection, diagnosis or vaccine word, ever — this is the guard
    that matters most now that a model touches every message;
  * the language cannot flip, markdown and new links cannot appear, and the text
    cannot be flattened into one run or padded out;
  * a rewrite that fails ANY of this is thrown away and the deterministic text —
    which is already written to be read aloud — goes out instead.

Fail-open everywhere: no key, a timeout, an empty reply, a rejected rewrite, and
the herder still gets his answer.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache

import yaml

from app.config import CONFIG_DIR, get_settings
from app.services import ai

log = logging.getLogger(__name__)

VOICE_PATH = CONFIG_DIR / "mentor_voice.yaml"

# The union of every service's own forbidden list, applied to every rewrite: once a
# model is allowed to touch the pest message, its boundary is this module's job.
FORBIDDEN_WORDS = (
    "dawa", "sindano", "chanjo", "antibiotic", "dewormer", "deworming", "dose",
    "dosage", "mg/kg", "ugonjwa", "drug", "injection", "vaccine", "ivermectin",
    "oxytetracycline", "cypermethrin",
)

# If the SOURCE text carries one of these, the rewrite must carry one too. Each
# tuple is one meaning; either language form satisfies it.
SAFETY_MARKERS = (
    ("kadirio", "estimate"),
    ("si kipimo", "not a measurement"),
    ("afisa wa mifugo", "vet", "veterinary"),
    ("hatujathibitisha", "not confirmed", "unconfirmed"),
)

_SWA_MARKERS = ("kwa", "na", "ya", "wa", "siku", "majani", "maji", "ng'ombe",
                "kadirio", "leo", "hii", "wewe", "hatuna", "hakuna")
_ENG_MARKERS = ("the", "and", "your", "for", "with", "today", "estimate",
                "water", "grass", "cattle", "not", "this")


@lru_cache(maxsize=2)
def load_voice() -> dict:
    with open(VOICE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_system(kind: str, lang: str) -> str:
    """The system prompt: persona + rules + this service's goal + output contract."""
    v = load_voice()
    goal = ((v.get("services") or {}).get(kind)
            or (v.get("services") or {}).get("default") or {}).get("goal", "").strip()
    rules = "\n".join(f"- {r}" for r in v.get("rules") or [])
    language = "Swahili (Kiswahili)" if lang in ("swa", "swahili") else "English"
    return (
        f"{str(v.get('persona') or '').strip()}\n\n"
        f"YOUR TASK: {goal}\n\n"
        f"HARD RULES\n{rules}\n"
        f"- Write in {language}.\n"
        "- Output the message only: no preamble, no explanation of what you did."
    )


def _nums(text: str) -> set[str]:
    """Numeric tokens, comma-decimals normalised (4,3 == 4.3)."""
    return {m.replace(",", ".") for m in re.findall(r"\d+(?:[.,]\d+)?", text or "")}


def _looks_swahili(text: str) -> bool:
    words = set(re.findall(r"[a-z']+", (text or "").lower()))
    return len(words & set(_SWA_MARKERS)) >= len(words & set(_ENG_MARKERS))


def _urls(text: str) -> set[str]:
    return set(re.findall(r"https?://\S+", text or ""))


def rephrase_ok(base: str, out: str, *, lang: str | None = None,
                max_chars: int = 900) -> tuple[bool, str]:
    """May this whole-text REWRITE replace the deterministic text? (ok, reason)

    Written as a list of ways to LOSE, because the failure mode that matters is a
    rewrite that reads beautifully and says something we never measured.

    This is the strict sibling of `insight_ok`: here the text IS the message, so every
    figure must survive. Use it for prose services (a pest window, the welcome-back
    lines); use `insight_ok` when the message is a data table and the model is writing
    a short lead instead of a replacement.
    """
    base, out = base or "", (out or "").strip()
    if not out:
        return False, "empty"

    low = out.lower()
    for word in FORBIDDEN_WORDS:
        if word in low:
            return False, f"forbidden_word:{word}"

    base_nums, out_nums = _nums(base), _nums(out)
    dropped = base_nums - out_nums
    if dropped:
        return False, f"dropped_number:{sorted(dropped)[0]}"
    invented = out_nums - base_nums
    if invented:
        return False, f"new_number:{sorted(invented)[0]}"

    base_low = base.lower()
    for group in SAFETY_MARKERS:
        if any(m in base_low for m in group) and not any(m in low for m in group):
            return False, f"safety_label_lost:{group[0]}"

    if lang in ("swa", "swahili") and _looks_swahili(base) and not _looks_swahili(out):
        return False, "language_flipped"
    if lang in ("eng", "english") and _looks_swahili(out):
        return False, "language_flipped"

    if ";" in out:
        return False, "semicolon"
    if any(line.count(":") > 1 for line in out.splitlines()):
        return False, "colon_run"
    if any(tok in out for tok in ("**", "##", "`", "__")):
        return False, "markdown"
    if _urls(out) - _urls(base):
        return False, "new_url"

    base_lines = [ln for ln in base.splitlines() if ln.strip()]
    out_lines = [ln for ln in out.splitlines() if ln.strip()]
    # It may merge a little (a mentor does combine two clauses), but a data list
    # flattened into one run is the failure we have already seen live.
    if base_lines and len(out_lines) < max(1, (len(base_lines) + 1) // 2):
        return False, "flattened"
    if len(out) > max(max_chars, int(len(base) * 1.6)):
        return False, "too_long"
    return True, ""


def insight_ok(base: str, insight: str, *, facts: dict | None = None,
               lang: str | None = None, max_chars: int = 420) -> tuple[bool, str]:
    """May this INSIGHT lead the message? (ok, reason-if-not)

    Different shape from `rephrase_ok` on purpose. A whole-text rewrite is held to
    "every number must survive", which is right when the text IS the message — and
    useless when the text is a data table, because then the model cannot write
    anything human without failing. So an insight may use FEWER numbers — and never
    one we did not compute:

      * a number in the insight must exist in the data text or the facts (no invented
        figures — the one rule that must never bend),
      * it must stay an insight: short, a few lines, no markdown, no new link,
      * it may not carry a forbidden word, and it may not flip language.

    The estimate labels are NOT required here, because the composed message sends the
    data text underneath the insight — the labels travel with the figures they belong
    to. That is also why a missing label cannot be lost by an insight.
    """
    base, insight = base or "", (insight or "").strip()
    if not insight:
        return False, "empty"
    low = insight.lower()
    for word in FORBIDDEN_WORDS:
        if word in low:
            return False, f"forbidden_word:{word}"

    allowed = _nums(base) | _nums(json.dumps(facts or {}, default=str))
    invented = _nums(insight) - allowed
    if invented:
        return False, f"new_number:{sorted(invented)[0]}"

    if lang in ("swa", "swahili") and _looks_swahili(base) and not _looks_swahili(insight):
        return False, "language_flipped"
    if lang in ("eng", "english") and _looks_swahili(insight):
        return False, "language_flipped"

    if ";" in insight or "**" in insight or "`" in insight or "##" in insight:
        return False, "formatting"
    if _urls(insight) - _urls(base):
        return False, "new_url"
    if len([ln for ln in insight.splitlines() if ln.strip()]) > 5:
        return False, "too_many_lines"
    if len(insight) > max_chars:
        return False, "too_long"
    return True, ""


def insight(kind: str, base_text: str, *, facts: dict | None = None,
            lang: str = "swa", max_chars: int = 420) -> str:
    """A short mentor insight from the computed facts — or "" (fail-open).

    This is the piece that answers "the data is right but nobody talks like that":
    two to four sentences that say what today MEANS, with the figures left to the
    data block underneath rather than crammed into the sentence.
    """
    base = base_text or ""
    settings = get_settings()
    if not base.strip() or not getattr(settings, "mentor_insights_enabled", True):
        return ""
    payload = json.dumps(facts or {}, ensure_ascii=False, default=str)
    if len(payload) > 2500:
        payload = payload[:2500]
    system = (
        build_system(kind, lang)
        + "\n\nNOW: write ONLY a short insight — 2 to 4 short sentences, at most 5 "
          "lines, no lists, no headings, no numbers of your own. Use a figure only if "
          "it is in FACTS or TEXT and it helps him decide. Say what today means and "
          "the one thing to do first. The figures themselves are printed under your "
          "words, so do not repeat them all."
    )
    out = ai._chat(system, f"FACTS={payload}\n\nTEXT:\n{base}")
    if not out:
        return ""
    ok, why = insight_ok(base, out, facts=facts, lang=lang, max_chars=max_chars)
    if not ok:
        log.warning("mentor insight rejected (%s) - sending the data text alone", why)
        return ""
    return out.strip()


def compose(lead: str, base_text: str) -> str:
    """The mentor's words, then the figures behind them. One place, one shape."""
    return f"{lead}\n\n———\n{base_text}" if lead else base_text


def voiced(kind: str, base_text: str, *, facts: dict | None = None,
           lang: str = "swa", max_chars: int = 420) -> str:
    """A message a herder reads: the mentor's insight, then the data underneath.

    Insight first because that is what a mentor says; figures underneath because they
    are the evidence for it, and because a herder who wants to argue with us needs the
    numbers to argue with. Fail-open: no insight, and the data text goes alone.
    """
    return compose(insight(kind, base_text, facts=facts, lang=lang, max_chars=max_chars),
                   base_text)


def rewrite(kind: str, base_text: str, *, facts: dict | None = None,
            lang: str = "swa", max_chars: int = 900) -> str:
    """Say the same facts like a mentor — or hand back the text unchanged.

    The whole-text path: for services whose message is already prose (a pest window,
    the welcome-back lines), where the rewrite replaces the text and therefore must
    keep every figure. `facts` is the computed payload (bands, verdict, walk, window,
    status) so the model explains rather than restates; it is grounding, never a
    source of new numbers — the number guard runs on the TEXT.
    """
    base = base_text or ""
    if not base.strip():
        return base
    settings = get_settings()
    if not getattr(settings, "mentor_insights_enabled", True):
        return base

    payload = json.dumps(facts or {}, ensure_ascii=False, default=str)
    if len(payload) > 2500:
        payload = payload[:2500]
    user = f"FACTS={payload}\n\nTEXT:\n{base}"
    out = ai._chat(build_system(kind, lang), user)
    if not out:
        return base

    ok, why = rephrase_ok(base, out, lang=lang, max_chars=max_chars)
    if not ok:
        log.warning("mentor rewrite rejected (%s) - keeping the deterministic text", why)
        return base
    return out[:max_chars]


# ---------------------------------------------------------------------------
# per-service facts: what the model is allowed to explain
# ---------------------------------------------------------------------------

def graze_facts(advice) -> dict:
    """The grazing ledger's numbers, flattened for grounding."""
    try:
        q, led = advice.quality, advice.ledger
        return {
            "walk_km": led.walk_km, "walk_hours": led.walk_hours,
            "from_manyatta": bool(advice.from_manyatta),
            "species": led.species, "head_count": led.head_count,
            "forage": q.label_swa, "forage_condition": q.condition,
            "me_mj_per_kg_dm": q.me_mj_per_kg_dm,
            "biomass_kg_ha": [q.biomass_lo_kg_ha, q.biomass_hi_kg_ha],
            "utilisable_kg_ha": [q.utilisable_lo_kg_ha, q.utilisable_hi_kg_ha],
            "required_mj": led.required_mj, "intake_mj": led.intake_mj,
            "balance_mj": led.balance_mj,
            "balance_band_mj": [led.balance_lo_mj, led.balance_hi_mj],
            "verdict": led.verdict, "snapshot": q.snapshot_as_of,
            "options": [{"code": o.code, "free": o.free} for o in advice.options],
        }
    except Exception:  # noqa: BLE001
        return {}


def pest_facts(outlook) -> dict:
    """The window tiers and the evidence behind them.

    Uses the outlook's OWN definition of "active" (`outlook.active`), rather than
    re-deriving it from the windows — a window dataclass has no `active` flag, and
    inventing the filter here would silently send the model an empty fact set.
    """
    try:
        wins = list(getattr(outlook, "active", ()) or ())
        return {
            "highest_tier": outlook.highest_tier,
            "windows": [
                {"key": w.key, "tier": w.tier, "score": getattr(w, "score", None),
                 "evidence": w.reason_lines("swa")}
                for w in wins
            ],
        }
    except Exception:  # noqa: BLE001
        return {}


def water_facts(status: str | None = None, age_days: float | None = None,
                queue: str | None = None) -> dict:
    return {"status": status, "report_age_days": age_days, "queue": queue}
