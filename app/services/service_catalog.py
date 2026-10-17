"""The service catalogue: the ONE list of what Arda Link can actually do.

WHY THIS FILE EXISTS
--------------------
The grounded chat layer (app/services/chat.py) may only phrase facts it was
handed. That rule was silently costing us the most common question a new herder
asks -- "what can you do for me?" -- because the facts bundle had no section for
our own services, so the honest answer was "I did not understand" or the static
menu. A language model asked to describe our services with no grounding invents
them; asking it to read the router's menu code is worse. So the catalogue is
DATA, owned by the code, and the model may only quote from it.

Every entry must be something the router can actually run today:

    id            -> the service name in whatsapp.SERVICE_ALIASES / _run_service
    menu_ids      -> the interactive ids that reach it (what a tap sends back)
    say           -> the word a herder types to get it (the trigger)
    what          -> one sentence, herder words, no numbers, no promises
    answers       -> the question it answers, in herder words (used for ranking)

`scripts/test_service_catalog.py` fails if the router grows a service this file
does not describe, or if a menu id dies here -- so the catalogue cannot drift.

RULES FOR ADDING A SERVICE (read before editing)
------------------------------------------------
1. Add it to the router FIRST (menu id, alias, _run_service branch), then here.
2. `say` must be a word the router's keyword lists actually recognise, or the
   catalogue would tell a herder to send something that reaches nothing.
3. Never describe a capability we do not have. "Market prices" is explicitly
   out of scope (config/pastoral_knowledge.yaml, market_timing).
4. No numbers in `what`: a service description is not a measurement, and the
   chat validator would reject an invented digit anyway.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Service:
    id: str
    menu_ids: tuple[str, ...]
    say_sw: str
    say_en: str
    what_sw: str
    what_en: str
    answers: str  # short intent label used for ranking a free-text question


SERVICES: tuple[Service, ...] = (
    Service(
        # The router resolves BOTH menu ids to one service name ("location"), so the
        # catalogue has one entry for both: the water point's status/queue/map and
        # the water-and-pasture picture where he stands.
        id="location", menu_ids=("svc:water", "svc:place"),
        say_sw="MAJI", say_en="WATER",
        what_sw="hali ya chanzo chako, foleni, ramani na malisho pale ulipo",
        what_en="your point's status, queue, map and the pasture where you are",
        answers="water status queue borehole dry working where am i place near me grazing here",
    ),
    Service(
        id="rain", menu_ids=("svc:rain",),
        say_sw="MVUA", say_en="RAIN",
        what_sw="mvua na ukame karibu nawe",
        what_en="rain and drought near you",
        answers="rain drought forecast weather dry spell",
    ),
    Service(
        id="graze", menu_ids=("svc:graze",),
        say_sw="MALISHO", say_en="GRAZE",
        what_sw="pin mlipopanda; thamani ya malisho na gharama",
        what_en="pin where the herd grazed; what that feed was worth",
        answers="grazed today pasture worth feed cost ledger",
    ),
    Service(
        id="pest", menu_ids=("svc:pest",),
        say_sw="KUPE", say_en="PEST",
        what_sw="wapi kuangalia kupe na minyoo kwenye mnyama, na lini",
        what_en="where to look for ticks and worms on the animal, and when",
        answers="ticks worms parasites look animal rain",
    ),
    Service(
        id="weight", menu_ids=("svc:animals", "svc:weigh"),
        say_sw="UZITO", say_en="WEIGHT",
        what_sw="kupima uzito wa mnyama mmoja kwa kipimo cha kifua",
        what_en="measure one animal's weight from its heart girth",
        answers="weight weigh animal kilos sell",
    ),
    Service(
        id="herd", menu_ids=("svc:herd_est",),
        say_sw="HERD", say_en="HERD",
        what_sw="kukadiria uzito wa kundi zima kwa sampuli",
        what_en="estimate the whole herd's weight from a few animals",
        answers="herd whole group estimate total weight",
    ),
    Service(
        id="map", menu_ids=(),
        say_sw="MAP", say_en="MAP",
        what_sw="ramani ya maeneo ya malisho karibu na maji yako",
        what_en="the map of grazing zones around your water",
        answers="map picture see zones rings",
    ),
    Service(
        id="pin", menu_ids=("svc:pin",),
        say_sw="PIN", say_en="PIN",
        what_sw="kuandikisha chanzo kipya cha maji pale ulipo",
        what_en="register a new water point where you are",
        answers="new water point register add borehole pan tap",
    ),
    Service(
        id="change", menu_ids=(),
        say_sw="BADILISHA", say_en="CHANGE",
        what_sw="kubadilisha chanzo chako cha maji, jina lake au aina yake",
        what_en="change your water point, its name or its type",
        answers="change point different another rename type wrong",
    ),
    Service(
        id="status", menu_ids=("svc:status",),
        say_sw="STATUS", say_en="STATUS",
        what_sw="maendeleo ya ujenzi wa chanzo chako",
        what_en="how the build of your water point is going",
        answers="progress build status steps",
    ),
    Service(
        id="voice", menu_ids=("svc:voice",),
        say_sw="SAUTI", say_en="VOICE",
        what_sw="kujibiwa kwa sauti au kwa maandishi",
        what_en="voice replies or text replies",
        answers="voice listen audio hear text",
    ),
    Service(
        id="language", menu_ids=("svc:lang",),
        say_sw="LUGHA", say_en="LANGUAGE",
        what_sw="kubadilisha Kiswahili au English",
        what_en="switch between Swahili and English",
        answers="language swahili english speak",
    ),
)

BY_ID: dict[str, Service] = {s.id: s for s in SERVICES}

# Router ids that are STEPS, not capabilities: "animals" is the one-tap chooser
# between weighing one animal and estimating the herd, "weigh"/"herd_est" are its
# two buttons, and "menu" is the menu itself. Describing them as services would hand
# the model trigger words a herder cannot type, so each is mapped onto the
# capability it leads to (None = nothing to advertise).
NAVIGATION_ALIASES: dict[str, str | None] = {
    "animals": "weight",
    "weigh": "weight",
    "herd_est": "herd",
    "menu": None,
}

# The ids the router's menu/aliases can produce. Kept here so the test can assert
# both directions (router -> catalogue and catalogue -> router).
ROUTER_SERVICE_IDS = tuple(s.id for s in SERVICES)


def _text(service: Service, lang: str, which: str) -> str:
    sw = lang != "english"
    if which == "say":
        return service.say_sw if sw else service.say_en
    return service.what_sw if sw else service.what_en


def describe(lang: str = "swahili", ids: list[str] | tuple[str, ...] | None = None) -> list[dict]:
    """Catalogue rows for a prompt/fact bundle: [{service, say, what}]."""
    chosen = [BY_ID[i] for i in ids if i in BY_ID] if ids else list(SERVICES)
    return [{"service": s.id, "say": _text(s, lang, "say"),
             "what": _text(s, lang, "what")} for s in chosen]


def _rank(service: Service, haystack: str) -> int:
    words = set(haystack.split())
    return sum(1 for w in service.answers.split() if w in words)


def for_question(question: str, lang: str = "swahili", limit: int = 3,
                 prefer: tuple[str, ...] = ()) -> list[dict]:
    """The services that fit this question, best first (deterministic, no model).

    Ranking is a plain word overlap against `answers`, so it is free, testable and
    cannot invent a service. `prefer` puts services the caller already identified
    (from the keyword router) first, because that router is more specific than a
    bag of words.
    """
    q = (question or "").lower()
    ordered: list[Service] = [BY_ID[i] for i in prefer if i in BY_ID]
    scored = sorted(
        (s for s in SERVICES if s.id not in {o.id for o in ordered}),
        key=lambda s: (-_rank(s, q), SERVICES.index(s)),
    )
    ordered.extend(scored)
    return describe(lang, [s.id for s in ordered[:max(1, limit)]])


def names(lang: str = "swahili") -> str:
    """One-line list of the everyday services, for a capability answer.

    Six entries on purpose: a WhatsApp reply is read in a pasture, and the two
    settings (voice, language) are already in 'menu'. The rest are reachable by
    their word and by the menu.
    """
    everyday = ("location", "rain", "graze", "pest", "weight", "pin")
    parts = []
    for sid in everyday:
        s = BY_ID[sid]
        parts.append(f"{_text(s, lang, 'say')} - {_text(s, lang, 'what')}")
    return "; ".join(parts)
