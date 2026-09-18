# WhatsApp message templates

WhatsApp permits free-form messages only within **24 hours** of the herder's
last inbound message. Anything proactive outside that window requires a
template approved in Meta Business Manager.

Approval takes days to weeks and **gates nothing else**, so submit early and let
it run in the background while the alert logic is built and dry-run.

Sent via `whatsapp_client.send_template(to, template_name, language_code,
body_params)`. The name and language must match the approved entry exactly.

## Status

| Template | Languages | Purpose | Status |
| --- | --- | --- | --- |
| `water_status_changed` | sw, en | A point this herder relies on was reported unusable | **not submitted** |
| `forage_declining` | sw, en | Their ring crossed a depletion threshold | not drafted (Phase 4) |
| `rain_upstream` | sw, en | Rain fell within reach; forage expected in ~N days | not drafted (Phase 4) |

## `water_status_changed` — draft for submission

Category: **UTILITY**. It is a service update about a resource the user has
explicitly registered, not marketing.

Swahili body:

```
⚠️ Taarifa kuhusu maji yako: {{1}} — {{2}}.
{{3}}
Usipeleke mifugo huko kabla ya kuuliza. Tuma 'maji' upate chanzo kingine cha karibu.
```

English body:

```
⚠️ About your water point: {{1}} — {{2}}.
{{3}}
Do not take your animals there before checking. Send 'water' for the next nearest source.
```

| Param | Meaning | Example |
| --- | --- | --- |
| `{{1}}` | Local name of the water point | `Oldonyiro borehole` |
| `{{2}}` | Status in the herder's language | `imeharibika` / `broken` |
| `{{3}}` | Confidence line | `Imethibitishwa na wafugaji 2.` |

Keep the wording identical to `alerts.compose_message()` so a herder sees the
same message whichever channel it arrived on. If Meta requires a change, change
`compose_message` to match — not the other way round.

## Before submitting

The Swahili strings across this project have not yet been reviewed by native
speakers (noted in the main README). A template is **frozen once approved** and
changing it means re-submission, so the review should happen before submission,
not after.
