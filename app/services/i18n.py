"""
Advisory message templates in Swahili and English.

Swahili strings are the primary ones (the service targets Kenyan pastoralists);
English is provided as a fallback/preference. The Swahili wording is kept
simple and domain-appropriate (piosphere/forage terms), and should be reviewed
with native speakers during the field validation gate.
"""
from __future__ import annotations

from app.services.advisory_logic import ForageCondition, WaterReliability

Species = str  # "cattle" | "shoat" | "camel"

SPECIES_LABEL_SW = {"cattle": "ng'ombe", "shoat": "kondoo/mbuzi", "camel": "ngamia"}
SPECIES_LABEL_EN = {"cattle": "cattle", "shoat": "sheep/goats", "camel": "camels"}

CONDITION_TEXT_SW = {
    ForageCondition.GREEN_GROWING: "malisho mabichi yanayoota",
    ForageCondition.DRY_FORAGE_AVAILABLE: "nyasi kavu nzuri ya malisho ipo",
    ForageCondition.BARE_DEGRADED: "eneo tupu, malisho hafifu",
    ForageCondition.UNCERTAIN: "hali ya malisho haijulikani wazi",
}

CONDITION_TEXT_EN = {
    ForageCondition.GREEN_GROWING: "fresh growing pasture",
    ForageCondition.DRY_FORAGE_AVAILABLE: "good dry forage is available",
    ForageCondition.BARE_DEGRADED: "bare ground, very little pasture",
    ForageCondition.UNCERTAIN: "pasture condition is unclear",
}

WATER_TEXT_SW = {
    WaterReliability.RELIABLE: "maji ya kutegemewa kipindi hiki",
    WaterReliability.SEASONAL: "maji ya msimu, huenda yasitosheleze mwaka mzima",
    WaterReliability.UNRELIABLE: "maji hayategemeki kipindi hiki — thibitisha kabla ya kwenda",
    WaterReliability.UNKNOWN: "uhakika wa maji haujulikani kwa sasa — thibitisha kabla ya kwenda",
}

WATER_TEXT_EN = {
    WaterReliability.RELIABLE: "reliable water for now",
    WaterReliability.SEASONAL: "seasonal water — may not last all year",
    WaterReliability.UNRELIABLE: "water is unreliable right now — verify before going",
    WaterReliability.UNKNOWN: "water reliability unknown — verify before going",
}

_SUPPORTED = ("swahili", "english")


def format_advisory_message(
    language: str,
    species: Species,
    distance_km: float,
    condition: ForageCondition,
    seasonally_normal: bool,
    curing_stage_note: str | None,
    water_reliability: WaterReliability,
) -> str:
    if language not in _SUPPORTED:
        language = "swahili"

    if language == "english":
        species_label = SPECIES_LABEL_EN.get(species, species)
        condition_text = CONDITION_TEXT_EN[condition]
        water_text = WATER_TEXT_EN[water_reliability]
        lines = [
            f"For your {species_label}: nearest water is {distance_km:.1f} km away.",
            f"Pasture condition near that water: {condition_text}.",
        ]
        if condition == ForageCondition.BARE_DEGRADED and not seasonally_normal:
            lines.append("This is worse than usual for this season — consider other areas.")
        elif condition == ForageCondition.BARE_DEGRADED and seasonally_normal:
            lines.append("This is normal for the dry season.")
        if curing_stage_note == "still_curing":
            lines.append("Grass is still curing, not fully dry yet.")
        lines.append(f"Water: {water_text}.")
        return "\n".join(lines)

    # swahili (default)
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

