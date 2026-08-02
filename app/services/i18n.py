"""
Advisory message templates in Borana and Swahili.

*** IMPORTANT: the Borana strings below are a best-effort draft, NOT verified
by a native speaker. Borana is under-resourced for MT/LLM translation and
mistakes here go straight to a herder making real decisions about their
animals. Before this goes anywhere near production, have a native Borana
speaker (ideally from Isiolo) review and correct every string in
BORANA_TEMPLATES. Swahili is more reliable but still worth a native check
given the domain-specific vocabulary (piosphere/forage terms don't have
standard everyday translations). Flag this explicitly to end users/testers
during the one-ward validation gate. ***
"""
from __future__ import annotations

from app.services.advisory_logic import ForageCondition, WaterReliability

Species = str  # "cattle" | "shoat" | "camel"

SPECIES_LABEL_SW = {"cattle": "ng'ombe", "shoat": "kondoo/mbuzi", "camel": "ngamia"}
SPECIES_LABEL_BO = {"cattle": "loon", "shoat": "hoolaa", "camel": "gaala"}  # DRAFT — verify

CONDITION_TEXT_SW = {
    ForageCondition.GREEN_GROWING: "malisho mabichi yanayoota",
    ForageCondition.DRY_FORAGE_AVAILABLE: "nyasi kavu nzuri ya malisho ipo",
    ForageCondition.BARE_DEGRADED: "eneo tupu, malisho hafifu",
    ForageCondition.UNCERTAIN: "hali ya malisho haijulikani wazi",
}

# DRAFT — needs native Borana speaker review before production use.
CONDITION_TEXT_BO = {
    ForageCondition.GREEN_GROWING: "marga magaariifi guddataa jira",
    ForageCondition.DRY_FORAGE_AVAILABLE: "marga gogaa gaarii tuni jira",
    ForageCondition.BARE_DEGRADED: "lafti duwwaa, marga hin jiru",
    ForageCondition.UNCERTAIN: "haala margaa sirriitti hin beekamne",
}

WATER_TEXT_SW = {
    WaterReliability.RELIABLE: "maji ya kutegemewa kipindi hiki",
    WaterReliability.SEASONAL: "maji ya msimu, huenda yasitosheleze mwaka mzima",
    WaterReliability.UNRELIABLE: "maji hayategemeki kipindi hiki — thibitisha kabla ya kwenda",
}

# DRAFT — needs native Borana speaker review before production use.
WATER_TEXT_BO = {
    WaterReliability.RELIABLE: "bishaan yeroo kana amanamaa dha",
    WaterReliability.SEASONAL: "bishaan waqtii, guutuu waggaa hin ga'u ta'a",
    WaterReliability.UNRELIABLE: "bishaan yeroo kana amanamaa miti — dursanii mirkaneessaa",
}


def format_advisory_message(
    language: str,
    species: Species,
    distance_km: float,
    condition: ForageCondition,
    seasonally_normal: bool,
    curing_stage_note: str | None,
    water_reliability: WaterReliability,
) -> str:
    if language == "swahili":
        species_label = SPECIES_LABEL_SW.get(species, species)
        condition_text = CONDITION_TEXT_SW[condition]
        water_text = WATER_TEXT_SW[water_reliability]
        lines = [
            f"Kwa {species_label} wako: maji ya karibu yapo umbali wa {distance_km:.1f} km.",
            f"Hali ya malisho karibu na maji hayo: {condition_text}.",
        ]
        if condition == ForageCondition.BARE_DEGRADED and not seasonally_normal:
            lines.append("Hali hii ni mbaya zaidi ya kawaida kwa msimu huu — angalia maeneo mengine.")
        elif condition == ForageCondition.BARE_DEGRADED and seasonally_normal:
            lines.append("Hii ni ya kawaida kwa msimu huu wa kiangazi.")
        if curing_stage_note == "still_curing":
            lines.append("Nyasi bado inakauka, si kavu kabisa.")
        lines.append(f"Maji: {water_text}.")
        return "\n".join(lines)

    # default: borana (DRAFT translations, see module docstring)
    species_label = SPECIES_LABEL_BO.get(species, species)
    condition_text = CONDITION_TEXT_BO[condition]
    water_text = WATER_TEXT_BO[water_reliability]
    lines = [
        f"{species_label} keessaniif: bishaan dhihoo km {distance_km:.1f} irratti argama.",
        f"Haalli margaa bishaan sana bira: {condition_text}.",
    ]
    if condition == ForageCondition.BARE_DEGRADED and not seasonally_normal:
        lines.append("Haalli kun waqtii kanaaf illee hin gaarii — bakka biraa ilaalaa.")
    elif condition == ForageCondition.BARE_DEGRADED and seasonally_normal:
        lines.append("Kun waqtii bonaa kanaaf idilee dha.")
    if curing_stage_note == "still_curing":
        lines.append("Margi ammallee gogaa jira, guutumaan hin gogne.")
    lines.append(f"Bishaan: {water_text}.")
    return "\n".join(lines)
