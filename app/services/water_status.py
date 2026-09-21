"""Water-point status — what a herder actually tells us, and how it gates guidance.

Why this module exists: the satellite can say a point *exists* (GSW recurrence)
but not whether the pump is broken, the trough is silted or the pan is dry
today. The only source for that is the herder standing next to it. So the
system asks, stores the answer as the water point's STATUS, and then stops
guiding people to water that is known to be dead.

Design rules (range-science + honesty):
  * Status is time-bound. A dry pan can refill; a broken pump can be repaired.
    A bad status SUPPRESSES guidance for STALE_STATUS_DAYS, after which the
    point comes back with a "not confirmed" warning instead of silently
    disappearing (disappearing water is worse than an unverified one).
  * A single report from one herder is evidence, not truth.
  * UNKNOWN is a first-class state: most points have never been confirmed, and
    the map must say so rather than implying they are fine.

Pure functions only — no DB, no other app imports — so it is unit-testable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

STATUS_UNKNOWN = "unknown"
STATUS_FLOWING = "flowing"            # river/spring running
STATUS_FUNCTIONAL = "functional"      # pump/well/trough working, water present
STATUS_INTERMITTENT = "intermittent"  # seasonal / sometimes dry
STATUS_DRY = "dry"                    # exists but no water now
STATUS_BROKEN = "broken"              # infrastructure failed (pump, trough)
STATUS_NOT_FOUND = "not_found"        # no such point in the field any more

ALL_STATUSES = (STATUS_UNKNOWN, STATUS_FLOWING, STATUS_FUNCTIONAL,
                STATUS_INTERMITTENT, STATUS_DRY, STATUS_BROKEN, STATUS_NOT_FOUND)

# Statuses that mean "do not send a herd here right now".
UNUSABLE_STATUSES = (STATUS_DRY, STATUS_BROKEN, STATUS_NOT_FOUND)

# How long a bad report keeps suppressing guidance (pans refill, pumps get fixed).
STALE_STATUS_DAYS = 60
# After this many days without confirmation a point is flagged "check before use".
NEEDS_CHECK_DAYS = 45

# WhatsApp quick replies. Digits are the whole point: a herder answers in one tap.
DIGIT_INTENTS = {
    "1": "water_available",
    "2": "water_dry",
    "3": "water_not_found",
    "4": "water_broken",
    "5": "water_intermittent",
}

# Herder report -> water status.
REPORT_TO_STATUS = {
    "water_available": STATUS_FUNCTIONAL,
    "water_flowing": STATUS_FLOWING,
    "water_intermittent": STATUS_INTERMITTENT,
    "water_dry": STATUS_DRY,
    "water_broken": STATUS_BROKEN,
    "water_not_found": STATUS_NOT_FOUND,
}


_STATUS_LABEL_SWA = {
    STATUS_UNKNOWN: "haijathibitishwa",
    STATUS_FLOWING: "maji yanatiririka",
    STATUS_FUNCTIONAL: "maji yapo",
    STATUS_INTERMITTENT: "maji ya vipindi",
    STATUS_DRY: "imekauka",
    STATUS_BROKEN: "imeharibika",
    STATUS_NOT_FOUND: "haipo tena",
}
_STATUS_LABEL_ENG = {
    STATUS_UNKNOWN: "not yet confirmed",
    STATUS_FLOWING: "water flowing",
    STATUS_FUNCTIONAL: "water available",
    STATUS_INTERMITTENT: "seasonal water",
    STATUS_DRY: "dry",
    STATUS_BROKEN: "broken",
    STATUS_NOT_FOUND: "no longer there",
}


def status_for_report(report_type: str) -> Optional[str]:
    """Water status implied by a herder's report (None for non-water reports)."""
    return REPORT_TO_STATUS.get((report_type or "").strip().lower())


def intent_for_digit(text: str) -> Optional[str]:
    """A bare '1'..'5' quick reply -> report type (None when it isn't one)."""
    return DIGIT_INTENTS.get((text or "").strip())


def _as_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def age_days(value, now: Optional[datetime] = None) -> Optional[float]:
    """Age in days of a timestamp (None when absent/unparseable)."""
    dt = _as_dt(value)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)



def usable_for_guidance(status: Optional[str], status_updated_at=None,
                        now: Optional[datetime] = None) -> bool:
    """False when we should NOT steer a herd here: a bad status reported recently.
    Bad statuses older than STALE_STATUS_DAYS are allowed back (a dry pan refills)
    — with a warning, see needs_check()."""
    if status in UNUSABLE_STATUSES:
        a = age_days(status_updated_at, now)
        return a is not None and a > STALE_STATUS_DAYS
    return True


def needs_check(status: Optional[str], status_updated_at=None,
                last_confirmed=None, now: Optional[datetime] = None) -> bool:
    """True when the herder must verify water before relying on the trip."""
    if status in UNUSABLE_STATUSES:
        return True
    a = age_days(status_updated_at, now)
    if a is None:
        a = age_days(last_confirmed, now)
    if a is None:
        return True          # never confirmed by anyone
    return a > NEEDS_CHECK_DAYS


def status_label(status: Optional[str], lang: str = "swa") -> str:
    """Short label for lists and map popups ('imekauka', 'not yet confirmed')."""
    table = _STATUS_LABEL_SWA if lang in ("swa", "swahili") else _STATUS_LABEL_ENG
    return table.get(status or STATUS_UNKNOWN, table[STATUS_UNKNOWN])


# Full sentences about a water point, for prose that must read and SPEAK naturally.
# The terse labels above are fragments ("imekauka"), and embedding one in a sentence
# produces broken Swahili ("Maji ya chanzo hiki ni imekauka"). Anything that goes
# into an advisory or a voice note uses THIS table.
_STATUS_SENTENCE_SWA = {
    STATUS_UNKNOWN: "Hatuwezi kuthibitisha kama maji yapo kwenye chanzo hiki.",
    STATUS_FLOWING: "Maji yanatiririka kwenye chanzo hiki.",
    STATUS_FUNCTIONAL: "Maji yanapatikana kwenye chanzo hiki.",
    STATUS_INTERMITTENT: "Maji ya chanzo hiki ni ya vipindi.",
    STATUS_DRY: "Maji ya chanzo hiki yamekauka.",
    STATUS_BROKEN: "Pampu au mfumo wa chanzo hiki umeharibika.",
    STATUS_NOT_FOUND: "Chanzo hiki hakipatikani tena.",
}
_STATUS_SENTENCE_ENG = {
    STATUS_UNKNOWN: "We cannot confirm that there is water at this point.",
    STATUS_FLOWING: "Water is flowing at this point.",
    STATUS_FUNCTIONAL: "Water is available at this point.",
    STATUS_INTERMITTENT: "The water at this point is seasonal.",
    STATUS_DRY: "The water at this point has dried up.",
    STATUS_BROKEN: "The pump or system at this point is broken.",
    STATUS_NOT_FOUND: "This point can no longer be found.",
}


def status_sentence(status: Optional[str], lang: str = "swa") -> str:
    """A complete sentence about a point's water status (never a bare fragment)."""
    table = (_STATUS_SENTENCE_SWA if lang in ("swa", "swahili")
             else _STATUS_SENTENCE_ENG)
    return table.get(status or STATUS_UNKNOWN, table[STATUS_UNKNOWN])


def status_colour(status: Optional[str]) -> str:
    """Map pin colour for the interactive map (hex)."""
    if status in (STATUS_FLOWING, STATUS_FUNCTIONAL):
        return "#0ea5e9"          # confirmed water now
    if status == STATUS_INTERMITTENT:
        return "#f59e0b"          # seasonal / sometimes dry
    if status in UNUSABLE_STATUSES:
        return "#b91c1c"          # do not go there
    return "#6b7280"              # unknown / not confirmed


def check_question(lang: str = "swa") -> str:
    """The one-line question appended to an advisory so status gets reported.

    Wording matters more than it looks: this is the ONLY thing we ask a herder to
    do, and it must be about the water point we just described. (It used to ask
    "Majina hayo ni ya kweli?" - "are those names real?" - which referred to
    nothing in the advisory at all.)
    """
    if lang in ("swa", "swahili"):
        return ("Je, maji yapo kwenye chanzo hicho sasa? Jibu namba moja.\n"
                "1 maji yapo\n2 imekauka\n3 haipo tena\n4 pampu imeharibika\n"
                "5 maji ya vipindi")
    return ("Is there water at that point right now? Reply with one number.\n"
            "1 water is there\n2 it is dry\n3 no longer there\n"
            "4 pump broken\n5 seasonal water")


def thanks_for_report(report_type: str, lang: str = "swa") -> str:
    """Short acknowledgement — reciprocity matters more than length."""
    status = status_for_report(report_type)
    label_swa = status_label(status, "swa")
    label_eng = status_label(status, "eng")
    if lang in ("swa", "swahili"):
        return (f"Asante! Tumerekodi kuwa {label_swa}.\n"
                "Taarifa zako zinasaidia wachungaji wengine wa eneo lako.")
    return (f"Thank you! We recorded that it is {label_eng}.\n"
            "Your reports help other herders near you.")


__all__ = [
    "STATUS_UNKNOWN", "STATUS_FLOWING", "STATUS_FUNCTIONAL", "STATUS_INTERMITTENT",
    "STATUS_DRY", "STATUS_BROKEN", "STATUS_NOT_FOUND", "ALL_STATUSES",
    "UNUSABLE_STATUSES", "STALE_STATUS_DAYS", "NEEDS_CHECK_DAYS", "DIGIT_INTENTS",
    "REPORT_TO_STATUS", "status_for_report", "intent_for_digit", "age_days",
    "usable_for_guidance", "needs_check", "status_label", "status_colour",
    "check_question", "thanks_for_report",
]

