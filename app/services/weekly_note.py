"""The weekly note — one message a week, same shape every time, one tap back.

Why this module exists: retention is a rhythm. A herder who gets something useful
every Monday at the same time, with one question he can answer with one digit, comes
back; a system that only answers when spoken to is a website he forgets.

The note is deliberately tiny and identical in shape every week:
    rain -> where it rained -> his water point and its queue -> pest check -> map link
    -> the one-tap question.

Rules it respects:
  * Nothing is invented. A missing line is simply absent (no filler, no "no data"
    padding), so an empty week yields a short honest note rather than a fake one.
  * It always ends with ONE question, and that question is about his own water
    point, so the answer can be recorded as ground truth on a known point.
  * Same layout every week: the herder should recognise it in two seconds and know
    where the one number he might send sits.
"""
from __future__ import annotations

from typing import Iterable, Optional

_TITLE = {"swa": "TAARIFA YA WIKI", "eng": "WEEKLY NOTE"}
_MAP_LABEL = {"swa": "Ramani", "eng": "Map"}


def build_note(*, lang: str = "swa", place: str | None = None,
               water_line: str, lines: Iterable[str] = (),
               question: str | None = None, map_url: str | None = None) -> str:
    """Assemble the weekly note.

    `water_line` is always present (the note is anchored on the herder's own water
    point); everything else is optional and simply left out when unavailable.
    """
    i = "swa" if lang in ("swa", "swahili") else "eng"
    head = f"🐐 {_TITLE[i]}"
    if place:
        head = f"{head} — {place}"

    parts: list[str] = [head]
    for line in lines:
        if line:
            parts.append(str(line).strip())
    parts.append(water_line.strip())
    if map_url:
        parts.append(f"{_MAP_LABEL[i]}: {map_url}")
    if question:
        parts.append(question.strip())
    return "\n\n".join(parts)


def week_label(lang: str = "swa") -> str:
    """Human label for 'this week' (kept here so wording stays in one place)."""
    return "wiki hii" if lang in ("swa", "swahili") else "this week"


def already_sent(week_start, sent_week_start) -> bool:
    """Prevent the same herder getting the same week's note twice."""
    if sent_week_start is None or week_start is None:
        return False
    return str(week_start)[:10] == str(sent_week_start)[:10]


def can_message_now(last_inbound_hours: Optional[float]) -> bool:
    """WhatsApp's 24-hour window: free-form messages only inside it.

    Outside it a business message needs an approved template, so the weekly sender
    records those herders as 'outside_24h_window' instead of firing into the void
    and reporting a success we cannot see.
    """
    return last_inbound_hours is not None and last_inbound_hours <= 24.0


__all__ = ["build_note", "week_label", "already_sent", "can_message_now"]
