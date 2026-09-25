# Arda Link — retention plan (settled)

*Why a pastoralist keeps using the system, what we build for it, and in what order.*

## The one rule

A pastoralist comes back for exactly one reason: **something he can use today, that
cost him one tap.** Everything in this plan is either that, or the rhythm that
reminds him it exists. If a feature does not give something usable before he has
finished reading it, it belongs in "deferred".

## Decisions taken (and the reasoning)

| Decision | Why |
|---|---|
| **No shaded rain band on the map.** Rain ships as *words by place names*. | Our rain data is a per-point forecast, not ground truth. A band claims spatial precision we do not have, and a wrong band looks authoritative — worse than a vague sentence. It would also be staler than the text (band = weekly at best, words = twice daily). Herders speak in place names, not colours: *"mvua ilinyesha upande wa Kipsing"*. |
| **Rain line names the places around the herder** — *"Kwako mm 3. Kipsing mm 18. Mvua zaidi kaskazini."* | Same information the band was for, without drawing anything. Uses coordinates of nearby water points and landmarks we already store. Refreshes twice daily. Survives as text and as voice. |
| **The map image carries its own date and the word "kadirio".** | The picture is 9–14 days old by design. A forwarded image loses the caption, so the image must state its own age or it quietly lies. |
| **Satellite refresh moves from 14 days to weekly per point**, as two alternating half-batches (4–5 points per run, twice a week). | The alert fires on 7-day rain signals, so a 14-day picture is a mismatch exactly when the decision is urgent (onset). Sentinel-2 revisits every 5 days, so 7-day composites are legitimate. Same total GEE work, smaller runs, less timeout risk. Consequence accepted: some weeks a point has no clean pass — then we say *"picha ya wiki hii haipo; hii ni ya wiki iliyopita"*. |
| **Pest and parasite advice is check-first.** We name the conditions and **where to look on the animal**. The herder's own eyes confirm or dismiss it. | No diagnosis, no treatment instruction. Being wrong costs a wasted look, not a panic or wasted money. It is the safest prediction we can ship, and it targets production loss (weight, milk) rather than disease. |
| **Cohort-first herd records.** Individual cards only for camels, milking cows, bulls and sale animals. | Shoats are counted in cohorts; only the animals that carry money get individual identity. |
| **Market prices and buyer–seller linking stay deferred.** | No value without a user base and traders. Revisit once a ward has density. |

## The five loops

| # | Loop | He receives | He gives (≤1 tap / 1 voice note) | Why he comes back | Status |
|---|---|---|---|---|---|
| 1 | **Water intelligence** | Is there water at the point he was about to walk to, how old is that report, how long is the queue, is it being repaired | One tap when he is there | Saves him a wasted walk. The strongest single reason to open the app. | One-tap report + 60-day gating built. Missing: fan-out to neighbours, queue question, "imekarabatiwa" closure |
| 2 | **Pest & parasite check** | "Conditions for ticks and worms are forming this week. Look here on the animal." Plus the evidence, in plain numbers | Saw it / didn't see it | A free reason to look at the animals this week, and his answer changes next week's message | Not built. Rules designed; rides on data we already store (rain, soil moisture, temperature, NDMI) |
| 3 | **Weekly picture + alerts** | One image, same layout every week: direction in walking days, best three options, water status, rain by place names. Plus rain-onset and dry-spell alerts | Read, forward, or answer one question | A predictable appointment, and a picture he recognises and forwards to his clan group — which is also our free acquisition | Map + live-map link built. Missing: date stamp, place-name rain line, weekly refresh, fixed weekly send |
| 4 | **Herd ledger with a payoff** | Monthly scorecard with rates (births, deaths, losses, milk) benchmarked anonymously against the ward, plus *"you should have 44, you counted 42"* | Weekly `zaidi / pungufu / hakuna`, and one voice note when something happens | His own numbers pay off without markets: he sees how his herd is doing and catches a real loss the week it happens | Not built. Household/group layer (two phones, one herd) sits inside it |
| 5 | **Contribution and pride** | *"Taarifa zako 14 zimewasaidia wachungaji 60"* | Nothing extra — earned from what he already sends | Reciprocity and standing in the ward. This is what turns a utility into a habit. | Not built. Small tally; rides on loops 1–4 |

Loops 1 and 2 are the engine: both are one-tap, both pay back in the same minute,
and both make our guidance better every time a herder uses them. If only one
survives contact with the field, it will be these.

## Build order

| Weeks | Build | Gate |
|---|---|---|
| 1–2 | Water loop: status fan-out to neighbours, queue question, repair closure | Reports per point per week rise for the same points |
| 2–3 | Pest & parasite windows, check-first loop (trigger: rain onset + weekly) | Observation replies arrive, and some contradict our window — that is honest, not a failure |
| 3 | Weekly picture fixes: date/estimate stamp, rain by place names, weekly half-batch satellite refresh | The weekly image is opened and answered |
| 4 | Fixed weekly send (same slot every week) + event alerts | Read receipts, replies, forwards |
| 5–6 | Herd ledger (household, cohort counts, change replies, scorecard, unaccounted check) | Changes recorded weekly; month-2 retention of alerted vs non-alerted households |
| Later | Animal cards and markings → lost-animal alerts (needs ward density); daily milk record; group goals | — |

## What we measure (so this is a plan, not a story)

- Weekly active households, and month-2 retention of the alerted vs non-alerted cohort.
- Sessions where the herder **gives** something after receiving something — the reciprocity test.
- Water reports per point per week — the health of loop 1.
- Pest observation replies, and how often they contradict our window — our honesty check.
- Herd changes recorded per household per month.
- Replies to the weekly image; "nimehamia" reports, which tell us the picture moved animals.

## Deliberately deferred

Symptom/outbreak surveillance (disease — only once county-aligned), market prices and
buyer–seller matching (needs users and traders), insurance and credit (needs ~6 months
of records), payments (licensing and fraud risk — never).

## The line we do not cross

We describe conditions and tell him where to look. We never say his animals have
something, never prescribe a drug or a dose, and never tell him to spend money. If he
finds something, we point him to the vet or the agrovet.
