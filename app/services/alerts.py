"""
Outbound alerts — the first time this system speaks without being spoken to.

The product promise is *Know first. Act in time.* Until now everything was a
reply: nothing reached a herder who did not ask, so a herder who did not already
suspect something learned nothing.

The first thing worth saying unprompted needs no satellite and no forecast.
`pastoralists.water_source_id` records which point each herder's animals drink
from, and it is indexed. When one herder reports that borehole broken, everyone
else who relies on it should be told before they walk there. Nobody was told.

Two things make this shippable now rather than after Meta approval:

  * WhatsApp allows free-form messages within 24h of the herder's last message,
    and every inbound is already recorded in `query_log`. So herders who have
    been in touch today can be reached with the existing `send_text` path —
    the same one `/dev/notify` already uses in production for build progress.
  * Everyone else needs a pre-approved template. `send_template` exists below;
    approval is the long pole and gates only that second group.

Discipline lives in code, not in good intentions
------------------------------------------------
An alerting system that is wrong, or merely noisy, is worse than no alerting
system: it teaches herders to ignore the channel, and the channel is the whole
product. So:

  * OFF BY DEFAULT. `ALERTS_ENABLED` must be set explicitly.
  * DRY RUN writes the full decision for every herder without sending, so an
    operator can read exactly who would have been messaged, and why.
  * DEDUP per herder per episode, with a cooldown.
  * RATE LIMIT per herder per day.
  * NEVER alert the reporter about their own report.
  * CORROBORATION is surfaced, not assumed: a single report is evidence, not
    truth (`water_status.py`), so a one-herder report is sent as explicitly
    unconfirmed rather than silently presented as fact.
  * EVERY DECISION IS LOGGED, including the suppressions. A ledger of only
    successes cannot answer "why didn't this herder hear about it".
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.db import get_pg_connection
from app.services import water_status

log = logging.getLogger(__name__)

# --- policy ------------------------------------------------------------------

#: Same herder, same point, same condition: do not repeat inside this window.
#: A pan refills and a pump gets repaired, so the same point failing again
#: months later IS a new event and does alert.
ALERT_COOLDOWN_DAYS = 14

#: A herder must not be buried. One bad day of reports must not become ten
#: messages — the eleventh is the one they stop reading.
MAX_ALERTS_PER_HERDER_PER_DAY = 3

#: WhatsApp's customer-service window. Outside it only templates may be sent.
FREEFORM_WINDOW_HOURS = 24

#: Statuses worth interrupting someone for. Note `intermittent` is absent: it
#: means the point is real and sometimes dry, which is not news.
ALERTABLE_STATUSES = water_status.UNUSABLE_STATUSES  # dry, broken, not_found


def alerts_enabled() -> bool:
    """Off unless explicitly switched on. The kill switch is an env var so it
    can be thrown without a deploy."""
    return os.environ.get("ALERTS_ENABLED", "").strip().lower() in ("1", "true", "yes")


# --- wording -----------------------------------------------------------------
# Deliberately short. This arrives unprompted, possibly while someone is walking.
# It must say: which point, what happened, how sure we are, what to do.

_TYPE_SW = {"borehole": "kisima", "well": "kisima", "river": "mto",
            "spring": "chemchemi", "pan": "dimbwi", "dam": "bwawa"}


def _point_label(name: str | None, water_type: str | None, swahili: bool) -> str:
    if name:
        return name
    if swahili:
        return _TYPE_SW.get(water_type or "", "chanzo cha maji")
    return water_type or "water point"


def compose_message(language: str, point_label: str, status: str,
                    confirmed: bool, reports: int) -> str:
    """The alert text. Honest about confidence, explicit about what to do."""
    swahili = language != "english"
    label = water_status.status_label(status, "swa" if swahili else "eng")

    if swahili:
        head = f"⚠️ Taarifa kuhusu maji yako: {point_label} — {label}."
        trust = (f"Imethibitishwa na wafugaji {reports}." if confirmed
                 else "Mfugaji mmoja ameripoti; bado haijathibitishwa.")
        act = ("Usipeleke mifugo huko kabla ya kuuliza. Tuma 'maji' upate "
               "chanzo kingine cha karibu.")
        return f"{head}\n{trust}\n{act}"

    head = f"⚠️ About your water point: {point_label} — {label}."
    trust = (f"Confirmed by {reports} herders." if confirmed
             else "Reported by one herder; not yet confirmed.")
    act = ("Do not take your animals there before checking. Send 'water' for "
           "the next nearest source.")
    return f"{head}\n{trust}\n{act}"


# --- decision ----------------------------------------------------------------


@dataclass
class AlertDecision:
    """What we decided for one herder, and why. Written to alert_log either way."""
    pastoralist_id: str
    phone: str
    language: str
    status: str            # alert_log.status — 'sent', 'dry_run', 'suppressed_*'
    channel: str | None = None
    message: str | None = None
    detail: dict = field(default_factory=dict)

    @property
    def would_send(self) -> bool:
        return self.status in ("sent", "dry_run")


RECIPIENTS_SQL = """
select p.id, p.phone_number, p.preferred_language,
       ws.name, ws.water_type, ws.status, ws.status_reports, ws.status_updated_at,
       -- Last time this herder messaged us: the WhatsApp free-form window.
       (select max(q.created_at) from query_log q
         where q.phone = p.phone_number
           and q.detail->>'event' = 'inbound') as last_inbound_at,
       -- Alerts already sent to this herder today (rate limit).
       (select count(*) from alert_log a
         where a.pastoralist_id = p.id
           and a.status in ('sent', 'dry_run')
           and a.created_at > now() - interval '1 day') as alerts_today,
       -- Have they already heard about THIS episode (dedup)?
       (select max(a.created_at) from alert_log a
         where a.pastoralist_id = p.id
           and a.event_key = %(event_key)s
           and a.status in ('sent', 'dry_run')) as last_same_event
from pastoralists p
join water_sources ws on ws.id = p.water_source_id
where p.water_source_id = %(water_source_id)s
"""

INSERT_ALERT_SQL = """
insert into alert_log (pastoralist_id, water_source_id, alert_type, event_key,
                       channel, status, detail)
values (%(pastoralist_id)s, %(water_source_id)s, %(alert_type)s, %(event_key)s,
        %(channel)s, %(status)s, %(detail)s::jsonb)
"""


def event_key_for(water_source_id: str, status: str) -> str:
    """Identifies the episode, not the moment — see migration 011."""
    return f"water_status:{water_source_id}:{status}"


def decide(row: dict, *, event_key: str, reporter_id: str | None,
           now: datetime | None = None, dry_run: bool = False) -> AlertDecision:
    """Pure decision for one candidate recipient. No I/O, so it is testable.

    Order matters: the cheapest and most important suppressions come first, and
    'self' precedes everything because telling someone their own report is
    always wrong regardless of other policy.
    """
    now = now or datetime.now(timezone.utc)
    pid = str(row["id"])
    base = dict(pastoralist_id=pid, phone=row["phone_number"],
                language=row.get("preferred_language") or "swahili")

    if reporter_id and pid == str(reporter_id):
        return AlertDecision(**base, status="suppressed_self")

    if not alerts_enabled() and not dry_run:
        return AlertDecision(**base, status="suppressed_disabled")

    last_same = row.get("last_same_event")
    if last_same and (now - last_same) < timedelta(days=ALERT_COOLDOWN_DAYS):
        return AlertDecision(**base, status="suppressed_dedup",
                             detail={"last_same_event": last_same.isoformat()})

    if int(row.get("alerts_today") or 0) >= MAX_ALERTS_PER_HERDER_PER_DAY:
        return AlertDecision(**base, status="suppressed_rate",
                             detail={"alerts_today": int(row["alerts_today"])})

    # Channel: free-form inside the 24h window, otherwise a template is needed.
    last_inbound = row.get("last_inbound_at")
    in_window = bool(last_inbound and (now - last_inbound) < timedelta(hours=FREEFORM_WINDOW_HOURS))
    if not in_window:
        # Templates are not wired up yet (Phase 3b). Record the miss rather than
        # silently dropping it — this count is the argument for submitting them.
        return AlertDecision(**base, status="suppressed_window",
                             detail={"last_inbound_at": last_inbound.isoformat()
                                     if last_inbound else None,
                                     "needs": "template"})

    reports = int(row.get("status_reports") or 0)
    confirmed = reports >= 2
    message = compose_message(
        language=base["language"],
        point_label=_point_label(row.get("name"), row.get("water_type"),
                                 swahili=base["language"] != "english"),
        status=row["status"], confirmed=confirmed, reports=reports)

    return AlertDecision(**base, status="dry_run" if dry_run else "sent",
                         channel="freeform", message=message,
                         detail={"reports": reports, "confirmed": confirmed,
                                 "water_status": row["status"]})


# --- execution ---------------------------------------------------------------


def notify_water_status_change(water_source_id: str, status: str,
                               reporter_pastoralist_id: str | None = None,
                               dry_run: bool = False) -> list[AlertDecision]:
    """Tell everyone who relies on this point that its status changed.

    Fail-open by design: this is called from the ground-truth path, and a herder
    submitting a report must never see an error because a broadcast failed.
    """
    if status not in ALERTABLE_STATUSES:
        return []

    key = event_key_for(water_source_id, status)
    decisions: list[AlertDecision] = []
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(RECIPIENTS_SQL, {"water_source_id": water_source_id,
                                             "event_key": key})
                rows = cur.fetchall()

            for row in rows:
                d = decide(row, event_key=key,
                           reporter_id=reporter_pastoralist_id, dry_run=dry_run)
                if d.status == "sent":
                    try:
                        from app.services import whatsapp_client

                        whatsapp_client.send_text(d.phone, d.message)
                    except Exception as e:  # noqa: BLE001
                        log.warning("alert send failed for %s: %s", d.phone, e)
                        d.status, d.detail = "failed", {**d.detail, "error": str(e)[:200]}
                decisions.append(d)

                with conn.cursor() as cur:
                    cur.execute(INSERT_ALERT_SQL, {
                        "pastoralist_id": d.pastoralist_id,
                        "water_source_id": water_source_id,
                        "alert_type": "water_status",
                        "event_key": key,
                        "channel": d.channel,
                        "status": d.status,
                        "detail": json.dumps({**d.detail, "message": d.message}),
                    })
            conn.commit()
    except Exception:  # noqa: BLE001
        log.exception("water-status alert broadcast failed for %s", water_source_id)
        return decisions

    sent = sum(1 for d in decisions if d.status == "sent")
    log.info("water-status alert %s: %d/%d sent (dry_run=%s)",
             key, sent, len(decisions), dry_run)
    return decisions


def summarise(decisions: list[AlertDecision]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in decisions:
        out[d.status] = out.get(d.status, 0) + 1
    return out
