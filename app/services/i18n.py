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
    ForageCondition.GREEN_GROWING: "Malisho karibu na maji ni mabichi na yanaota.",
    ForageCondition.DRY_FORAGE_AVAILABLE: "Malisho karibu na maji ni nyasi kavu nzuri.",
    ForageCondition.BARE_DEGRADED: "Malisho karibu na maji ni hafifu, kama eneo tupu.",
    ForageCondition.UNCERTAIN: "Hatuwezi kusoma wazi hali ya malisho karibu na maji.",
}

CONDITION_TEXT_EN = {
    ForageCondition.GREEN_GROWING: "The pasture near that water is fresh and growing.",
    ForageCondition.DRY_FORAGE_AVAILABLE: "The pasture near that water is good dry forage.",
    ForageCondition.BARE_DEGRADED: "The pasture near that water is thin, close to bare ground.",
    ForageCondition.UNCERTAIN: "We cannot read the pasture near that water clearly.",
}

WATER_TEXT_SW = {
    WaterReliability.RELIABLE: "Maji hapa yanategemewa kipindi hiki.",
    WaterReliability.SEASONAL: "Haya ni maji ya msimu, huenda yasitosheleze mwaka mzima.",
    WaterReliability.UNRELIABLE: "Maji hayategemeki kipindi hiki. Thibitisha kabla ya kuanza safari.",
    WaterReliability.UNKNOWN: "Hatujathibitisha kama maji yapo. Thibitisha kabla ya kuanza safari.",
}

WATER_TEXT_EN = {
    WaterReliability.RELIABLE: "Water here is reliable for now.",
    WaterReliability.SEASONAL: "This is seasonal water, and it may not last all year.",
    WaterReliability.UNRELIABLE: "Water is unreliable right now. Verify before you set off.",
    WaterReliability.UNKNOWN: "We have not confirmed that water is there. Verify before you set off.",
}

_SUPPORTED = ("swahili", "english")

# Grazing-zone notes. These describe the USUAL grazing reach for the species at
# the herder's watering routine — NOT a biological collapse threshold. Being
# outside it is not "instant death"; it means every long daily walk costs the
# animal condition, especially in a harsh/dry season.
#
# STYLE RULE FOR EVERY STRING IN THIS FILE (learned from herder feedback): these
# are read aloud by a voice note and read on a phone in a pasture. So: one idea per
# line, short sentences, no colons or semicolons inside a sentence, no repeated
# nouns, no bracketed codes. Units are spelled out ("kilomita"), and uncertainty is
# a sentence ("Huu ni makadirio"), not a parenthetical. scripts/test_message_style.py
# enforces this.
ZONE_WARN_SW = {
    "far": "⚠️ Uko mbali kidogo kuliko eneo la kawaida la malisho. Eneo la kawaida "
           "ni kilomita {eff:.0f} kutoka maji. Wanyama bado wanaweza, lakini "
           "wanaanza kuchoka. Pinduka taratibu kuelekea majini.",
    "critical": "⚠️ Uko mbali zaidi ya eneo la kawaida la malisho. Eneo la kawaida "
                "ni kilomita {eff:.0f} kutoka maji. Matembezi marefu hivyo "
                "hupunguza nguvu ya mnyama. Rudi karibu na maji leo.",
}
ZONE_WARN_EN = {
    "far": "⚠️ You are a little farther than the usual grazing zone. The usual zone "
           "is {eff:.0f} km from water. The animals can still cope, but they are "
           "tiring. Turn back toward water.",
    "critical": "⚠️ You are beyond the usual grazing zone. The usual zone is "
                "{eff:.0f} km from water. Long walks like this cost your animals "
                "condition. Head back to water today.",
}

# Actionable advice when the forage/season is harsh (we know this from the
# satellite indices — no extra compute).
DRY_HARSH_SW = ("☀️ Msimu ni mkavu na malisho ni machache. Wape wanyama maji "
                "mapema asubuhi. Walishe karibu na maji, na usiwakimbize "
                "matembezi marefu kila siku.")
DRY_HARSH_EN = ("☀️ It is a dry season and forage is scarce. Water your animals "
                "early in the morning. Let them graze close to water, and do not "
                "force long walks every day.")


# A herder standing AT their water point is the common case, and "kilomita 0.0" is
# not something anyone would say aloud. Below ~100 m we say "right here" instead.
_NEAR_KM = 0.1


def _distance_clause_sw(distance_km: float) -> str:
    if distance_km < _NEAR_KM:
        return "maji yapo hapa karibu nawe."
    return f"maji ya karibu yapo kilomita {distance_km:.1f} kutoka hapa."


def _distance_clause_en(distance_km: float) -> str:
    if distance_km < _NEAR_KM:
        return "the water is right here next to you."
    return f"the nearest water is {distance_km:.1f} km away."


def format_advisory_message(
    language: str,
    species: Species,
    distance_km: float,
    condition: ForageCondition,
    seasonally_normal: bool,
    curing_stage_note: str | None,
    water_reliability: WaterReliability,
    grazing_zone: str | None = None,
    effective_radius_km: float | None = None,
    dry_harsh: bool = False,
) -> str:
    if language not in _SUPPORTED:
        language = "swahili"

    if language == "english":
        species_label = SPECIES_LABEL_EN.get(species, species)
        condition_text = CONDITION_TEXT_EN[condition]
        water_text = WATER_TEXT_EN[water_reliability]
        lines = [f"For your {species_label}, {_distance_clause_en(distance_km)}", condition_text]
        if condition == ForageCondition.BARE_DEGRADED and not seasonally_normal:
            lines.append("That is worse than usual for this season, so consider other areas.")
        elif condition == ForageCondition.BARE_DEGRADED and seasonally_normal:
            lines.append("That is normal for this dry season.")
        if curing_stage_note == "still_curing":
            lines.append("The grass is still curing, not fully dry yet.")
        if grazing_zone in ZONE_WARN_EN and effective_radius_km:
            lines.append(ZONE_WARN_EN[grazing_zone].format(eff=effective_radius_km))
        if dry_harsh:
            lines.append(DRY_HARSH_EN)
        lines.append(water_text)
        return "\n".join(lines)

    # swahili (default)
    species_label = SPECIES_LABEL_SW.get(species, species)
    condition_text = CONDITION_TEXT_SW[condition]
    water_text = WATER_TEXT_SW[water_reliability]
    lines = [
        f"Kwa {species_label} wako, {_distance_clause_sw(distance_km)}",
        condition_text,
    ]
    if condition == ForageCondition.BARE_DEGRADED and not seasonally_normal:
        lines.append("Hii ni mbaya zaidi ya kawaida kwa msimu huu, fikiria maeneo mengine.")
    elif condition == ForageCondition.BARE_DEGRADED and seasonally_normal:
        lines.append("Hii ni ya kawaida kwa msimu huu wa kiangazi.")
    if curing_stage_note == "still_curing":
        lines.append("Nyasi bado inakauka, si kavu kabisa.")
    if grazing_zone in ZONE_WARN_SW and effective_radius_km:
        lines.append(ZONE_WARN_SW[grazing_zone].format(eff=effective_radius_km))
    if dry_harsh:
        lines.append(DRY_HARSH_SW)
    lines.append(water_text)
    return "\n".join(lines)

