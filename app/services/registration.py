"""Herder registration / onboarding.

A new WhatsApp user is walked through a short guided flow (name -> language ->
animals) before the advisory becomes personal. On completion we backfill the
pastoralists row so every later reply can use the herder's name and herd
composition.
"""
from __future__ import annotations

import logging

from app.db import get_pg_connection

log = logging.getLogger(__name__)

# Keys the onboarding flow understands for "what animals do you have".
SPECIES_KEYS = {
    "cattle": ["cattle", "ng'ombe", "ngombe", "cow", "loon"],
    "shoat": ["shoat", "goat", "sheep", "kondoo", "mbuzi", "hoolaa"],
    "camel": ["camel", "ngamia", "gaala"],
}

ANIMAL_BUTTONS = [("cattle", "Ng'ombe"), ("shoat", "Mbuzi/Kondoo"), ("camel", "Ngamia")]
MIXED_BUTTONS = [("yes_mixed", "Ndiyo, mchanganyiko"), ("no_mixed", "Hapana, moja tu")]

SET_NAME_SQL = "update pastoralists set full_name = %(name)s where phone_number = %(phone)s"

SET_LANGUAGE_SQL = "update pastoralists set preferred_language = %(language)s where phone_number = %(phone)s"

COMPLETE_SQL = """
update pastoralists
set herd_composition = %(composition)s::jsonb,
    onboarded_at = coalesce(onboarded_at, now()),
    updated_at = now()
where phone_number = %(phone)s
"""

WELCOME = {
    "swahili": "Karibu {name}! 🙌\n"
               "Mimi ni Arda Link - mshauri wako wa malisho na maji.\n"
               "Nitakusaidia kupata taarifa za maji na malisho karibu nawe.",
    "english": "Welcome {name}! 🙌\n"
               "I'm Arda Link - your pasture and water advisor.\n"
               "I'll help you get water and grazing information near you.",
}

ASK_NAME = {
    "swahili": "Karibu! Tuanze. Jina lako ni nani?",
    "english": "Welcome! Let's begin. What is your name?",
}

ASK_LANGUAGE = {
    "swahili": "Asante, {name}! Unapenda kuzungumza lugha gani? (Swahili au English)",
    "english": "Thank you, {name}! Which language do you prefer? (Swahili or English)",
}

ASK_ANIMALS = {
    "swahili": "Sasa, una wanyama wa aina gani? (Chagua: ng'ombe, mbuzi/kondoo, au ngamia)",
    "english": "Now, what kind of animals do you have? (Choose: cattle, sheep/goats, or camels)",
}

ASK_COUNT = {
    "swahili": "Takriban wangapi? (k.m. '12') — tuma namba ya jumla ya wanyama wako.",
    "english": "Roughly how many? (e.g. '12') — send the total number of animals.",
}

NO_WATER_GUIDANCE = {
    "swahili": "Mashukuru! {name}, chanzo chako cha maji hakijasajiliwa bado.\n"
               "Hatua zinazofuata:\n"
               "1. Tuma eneo lako (location) kwenye WhatsApp.\n"
               "2. Tuma neno 'PIN' kulisajili.\n"
               "3. Tutakusaidia kuthibitisha ni chanzo gani, kisha tuanze kupima malisho yake.",
    "english": "Thanks! {name}, your water source is not registered yet.\n"
               "Next steps:\n"
               "1. Share your location on WhatsApp.\n"
               "2. Send the word 'PIN' to register it.\n"
               "3. We'll help you confirm what type it is, then start measuring its pasture.",
}

ONBOARDING_DONE = {
    "swahili": "Umesajiliwa kikamilifu, {name}! 🎉\n"
               "Sasa tunaweza kukupa ushauri wa kibinafsi. Tuma eneo lako (location) "
               "kupata taarifa za maji na malisho, au 'uzito' kupima uzito wa mnyama wako.",
    "english": "You're fully registered, {name}! 🎉\n"
               "We can now give you personal advice. Send your location to get water "
               "and pasture info, or 'weight' to measure your animal's weight.",
}


def detect_species(text: str) -> str | None:
    t = text.lower()
    for species, keys in SPECIES_KEYS.items():
        if any(k in t for k in keys):
            return species
    return None


def set_name(phone: str, name: str) -> None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SET_NAME_SQL, {"phone": phone, "name": name.strip()[:60]})
        conn.commit()


def set_language(phone: str, language: str) -> None:
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SET_LANGUAGE_SQL, {"phone": phone, "language": language})
        conn.commit()


def complete_onboarding(phone: str, composition: dict) -> None:
    import json

    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                COMPLETE_SQL,
                {"phone": phone, "composition": json.dumps(composition or {})},
            )
        conn.commit()
