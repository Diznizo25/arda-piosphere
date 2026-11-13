"""One switchboard: which capabilities a pastoralist can SEE in WhatsApp.

WHY THIS FILE EXISTS
--------------------
The pilot now serves ONE job — "when will the grass in my ring run out, and where
do I take the herd" — plus the one-time registration that makes that answer
personal (his species, his water point, his language). Everything else that was
built along the way (weight tape, herd estimate, ticks and worms, the grazing
ledger, build status, voice/language settings) still WORKS when a herder sends
its word, but it must not be on the menu. A man deciding whether to move 200
cattle should not have to read nine options to find the one that matters in a
dry week.

Two questions, deliberately kept apart
--------------------------------------
    is_enabled(service)  may this run at all?   (keyword, deep link, old habit)
    is_listed(service)   does it appear in the menu?

'voice' and 'language' are ENABLED but not listed as services: a herder who
learned 'SAUTI' or 'english' keeps it, and nobody new is asked to choose. Hiding
a capability from a menu is a product decision; deleting a word someone already
uses would be a silent break.

The ONE exception is the language row itself (ALWAYS_LISTED, below). It is not a
capability we are piloting — it is how he hears us — so the row stays on the menu
even when the settings section is hidden, and ARDA_FEATURES cannot take it away.
A herder who cannot read the language we answer in has no other way to say so.

Overridable without a deploy: ARDA_FEATURES="drought,pasture,register" (or any
comma list of the feature ids below). Unknown ids are ignored, and an empty or
unrecognisable value falls back to the default — a typo in an env var must never
empty the menu a herder depends on.

`scripts/test_features.py` pins the contract: the default menu lists ONLY the
drought, pasture and registration rows, and every enabled service still has a
branch in the router.
"""
from __future__ import annotations

import os

# Feature id -> (Swahili label, English label). The labels are what a section
# title would say; they are herder words, not internal names.
FEATURE_LABEL: dict[str, tuple[str, str]] = {
    "drought": ("Ukame na mvua", "Drought and rain"),
    "pasture": ("Malisho na ramani", "Pasture and maps"),
    "register": ("Andikisha chanzo", "Register a point"),
    "livestock": ("Mifugo", "Livestock"),
    "settings": ("Mipangilio", "Settings"),
}

# Service id (as used by whatsapp._run_service / service_catalog) -> feature id.
# Anything not listed here (menu, navigation steps) is always allowed: the router
# decides what it means.
SERVICE_FEATURE: dict[str, str] = {
    "rain": "drought",
    "map": "pasture",
    "location": "pasture",
    "depletion": "pasture",
    "pin": "register",
    "change": "register",
    "graze": "livestock",
    "pest": "livestock",
    "weight": "livestock",
    "herd": "livestock",
    "herd_est": "livestock",
    "animals": "livestock",
    "weigh": "livestock",
    "status": "livestock",
    "voice": "settings",
    "language": "settings",
}

# What a pastoralist gets today: the drought/pasture service and registration.
DEFAULT_FEATURES: tuple[str, ...] = ("drought", "pasture", "register")

# Enabled but never advertised (see the module docstring).
UNLISTED_FEATURES: frozenset[str] = frozenset({"settings"})

# Services that are ALWAYS offered, whatever ARDA_FEATURES says: they are not a
# capability we are piloting, they are how the herder hears us. Exactly one today —
# the language switch — and it is deliberately the last row of the menu, in its own
# section, so it is reachable without competing with the one job the menu serves.
ALWAYS_LISTED: frozenset[str] = frozenset({"language"})

ENV_VAR = "ARDA_FEATURES"



def enabled_features() -> tuple[str, ...]:
    """Active feature ids, from ARDA_FEATURES or the default set.

    Deterministic and order-stable (the env order is preserved), so the menu is
    identical between two calls with the same environment.
    """
    raw = (os.getenv(ENV_VAR) or "").strip()
    if not raw:
        return DEFAULT_FEATURES
    chosen = tuple(
        part.strip().lower() for part in raw.split(",") if part.strip().lower() in FEATURE_LABEL
    )
    # A value we cannot read is not a licence to hide everything.
    return chosen or DEFAULT_FEATURES


def is_enabled(service: str | None) -> bool:
    """May this service run for a herder right now?"""
    feature = SERVICE_FEATURE.get(service or "")
    if feature is None:
        return True
    if feature in UNLISTED_FEATURES:
        # Settings are not a capability a herder chooses from a menu; they are how
        # he hears us. They stay available whether or not the pilot advertises them.
        return True
    return feature in enabled_features()


def is_listed(service: str | None) -> bool:
    """Does this service appear in the menu?"""
    if (service or "") in ALWAYS_LISTED:
        # How he hears us is not a pilot feature (see ALWAYS_LISTED).
        return True
    feature = SERVICE_FEATURE.get(service or "")
    if feature is None:
        return False  # only rows tied to a feature are offered
    if feature in UNLISTED_FEATURES:
        return False
    return feature in enabled_features()


def filter_rows(rows, alias_lookup=None):
    """Keep the menu rows whose id resolves to a listed service.

    `alias_lookup` maps a row id ("svc:rain") to a service name; the router passes
    its own SERVICE_ALIASES so the menu and the dispatcher cannot drift.
    """
    out = []
    for row in rows:
        rid = row[0]
        service = (alias_lookup or {}).get(rid, rid.replace("svc:", ""))
        if is_listed(service):
            out.append(row)
    return out


def filter_sections(sections, alias_lookup=None):
    """Filter a menu's sections, dropping any section that ends up empty.

    An empty WhatsApp list section is rejected by the API, so this is not
    cosmetic: an empty section would turn the whole menu into an error.
    """
    out = []
    for title, rows in sections:
        kept = filter_rows(rows, alias_lookup)
        if kept:
            out.append((title, kept))
    return out


def hidden_service_reply(lang: str = "swahili") -> str:
    """What a herder gets when he asks for a service we no longer offer.

    Never silence (he asked, he deserves an answer) and never an apology that
    implies a fault: this is a choice, and it comes with the two things we do.
    """
    if (lang or "").startswith("en") or lang == "english":
        return ("Right now I help with two things: the drought and pasture outlook, "
                "and registering your water point.\n\n"
                "Send:\n"
                "• WHEN GRASS RUNS OUT — how many days the grass in your ring may last\n"
                "• MVUA — rain and drought near you\n"
                "• MAP — the grazing map\n"
                "• PIN — register your water point")
    return ("Kwa sasa nakusaidia mambo mawili: ukame na malisho, na "
            "kuandikisha chanzo chako cha maji.\n\n"
            "Tuma:\n"
            "• MALISHO YATAISHA — siku ngapi malisho yako yanaweza kuisha\n"
            "• MVUA — mvua na ukame karibu nawe\n"
            "• MAP — ramani ya malisho\n"
            "• PIN — andikisha chanzo chako cha maji")


__all__ = [
    "FEATURE_LABEL",
    "SERVICE_FEATURE",
    "DEFAULT_FEATURES",
    "UNLISTED_FEATURES",
    "ALWAYS_LISTED",
    "ENV_VAR",
    "enabled_features",
    "is_enabled",
    "is_listed",
    "filter_rows",
    "filter_sections",
    "hidden_service_reply",
]
