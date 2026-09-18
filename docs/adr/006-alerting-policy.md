# ADR 006 — What earns an unprompted message

**Status:** accepted · **Phase:** 3 · **Date:** 2026-09-19

## Context

The product promise is *Know first. Act in time.* Until this phase everything
was a reply. Nothing reached a herder who did not ask, so a herder who did not
already suspect something learned nothing — which is the opposite of knowing
first.

The single highest-value thing to say unprompted needs **no satellite and no
forecast**. `pastoralists.water_source_id` already records which point each
herder's animals drink from, and it is indexed. When one herder reports that
borehole broken, everybody else who relies on it should be told before they walk
there. Nobody was told. They walked there and found it dead.

## Decision

`app/services/alerts.py` broadcasts water-status changes, triggered from
`ground_truth.record_ground_truth()` **after** the report commits.

### It ships without waiting for Meta

WhatsApp permits free-form messages within 24h of the herder's last message, and
every inbound is already recorded in `query_log` with `detail.event='inbound'`.
So herders who have been in touch today are reachable **now**, through the same
`send_text` path `/dev/notify` already uses in production.

`send_template()` is added for everyone else (Phase 3b). Approval is the long
pole and gates only that second group — so alerting starts delivering value
while the paperwork is in flight, and `suppressed_window` counts become the
evidence for how much the templates are worth.

### Discipline is in code, not in good intentions

| Rule | Why |
| --- | --- |
| **Off by default** (`ALERTS_ENABLED`) | A kill switch that needs a deploy is not a kill switch |
| **Dry run** writes every decision without sending | Review the blast radius on real data before a single phone buzzes |
| **Never alert the reporter** | Echoing someone's own report back is always wrong — checked before all other policy |
| **Dedup per episode**, 14-day cooldown | Same herder, same point, same condition, twice in a fortnight is noise |
| **Rate limit**, 3/herder/day | One bad day of reports must not become ten messages; the eleventh is the one they stop reading |
| **Only unusable statuses** | `intermittent` means the point is real and sometimes dry. Not news |
| **Every decision logged**, suppressions included | A ledger of only successes cannot answer "why didn't this herder hear about it" |

### Evidence is surfaced, never assumed

`water_status.py` already states the principle: *a single report from one herder
is evidence, not truth.* We do **not** hold the alert until a second report —
speed is the point, and a broken pump is worth knowing about early. Instead the
message says which it is:

> Imethibitishwa na wafugaji 2. *(Confirmed by 2 herders.)*
>
> Mfugaji mmoja ameripoti; bado haijathibitishwa. *(One herder reported; not yet confirmed.)*

A herder who is told "unconfirmed" can ask around. A herder told a falsehood
confidently stops trusting the channel.

### Every alert offers a way forward

*"Usipeleke mifugo huko kabla ya kuuliza. Tuma 'maji' upate chanzo kingine cha
karibu."* — Do not take your animals there before checking; send 'water' for the
next nearest source. Being told your water is dead with no alternative is not
help.

### The episode, not the moment

`event_key = water_status:<uuid>:<status>` identifies the *episode*. Re-reports
of the same condition inside the cooldown are suppressed; the same point
breaking again months later is a new event and does alert, because pans refill
and pumps get repaired.

## Consequences

- `migrations/012_alert_log.sql` adds `alert_log` and allows `kind='alert'` in
  `query_log` so the ops dashboard sees alert activity.
- Alerting is inert until `ALERTS_ENABLED` is set. **Run
  `scripts/alerts_dry_run.py` and read every line before setting it.**
- A false "your borehole is broken" costs more trust than ten correct alerts
  earn. If false alarms appear, the fix is corroboration thresholds and better
  wording — not turning the channel off, and not loosening the rate limit.

## What would change our mind

If `suppressed_window` dominates the dry run — most herders unreachable because
they have not messaged in 24h — then templates are not an enhancement but a
prerequisite, and Phase 3b moves ahead of everything else.
