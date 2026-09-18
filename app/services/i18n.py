"""
Advisory message templates in Swahili and English.

Swahili strings are the primary ones (the service targets Kenyan pastoralists);
English is provided as a fallback/preference. The Swahili wording is kept
simple and domain-appropriate (piosphere/forage terms), and should be reviewed
with native speakers during the field validation gate.
"""
from __future__ import annotations

from datetime import datetime

from app.services.advisory_logic import ForageCondition, WaterPresence, WaterReliability

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

#: Present-tense water, reported SEPARATELY from reliability so a climatology
#: can never speak in the present tense. Only WATER_SEEN and NO_WATER_SEEN say
#: anything; UNCERTAIN/UNKNOWN stay silent rather than guess.
PRESENCE_TEXT_SW = {
    WaterPresence.WATER_SEEN: "💧 Satelaiti iliona maji hapa {when}.",
    WaterPresence.NO_WATER_SEEN: ("💧 Satelaiti haikuona maji hapa {when} — "
                                  "huenda pamekauka. Uliza kabla ya kwenda."),
}
PRESENCE_TEXT_EN = {
    WaterPresence.WATER_SEEN: "💧 The satellite saw water here {when}.",
    WaterPresence.NO_WATER_SEEN: ("💧 The satellite saw no water here {when} — "
                                  "it may be dry. Ask before you go."),
}

_SUPPORTED = ("swahili", "english")

# Grazing-zone notes. These describe the USUAL grazing reach for the species at
# the herder's watering routine — NOT a biological collapse threshold. Being
# outside it is not "instant death"; it means every long daily walk costs the
# animal condition, especially in a harsh/dry season.
ZONE_WARN_SW = {
    "far": "⚠️ Umeenda mbali kuliko eneo la kawaida la malisho (~{eff:.0f} km kutoka maji). "
           "Wanyama bado wanaweza, lakini wanaanza kuchoka — pinduka taratibu kuelekea majini.",
    "critical": "⚠️ Hapa ni mbali zaidi ya eneo la kawaida la malisho (~{eff:.0f} km kutoka maji). "
                "Kila siku ya matembezi marefu hivyo hupunguza nguvu na hali ya mnyama — "
                "rudi karibu na maji leo.",
}
ZONE_WARN_EN = {
    "far": "⚠️ You are farther than the usual grazing zone (~{eff:.0f} km from water). "
           "Animals can still cope, but they are tiring — turn back toward water.",
    "critical": "⚠️ This is beyond the usual grazing zone (~{eff:.0f} km from water). "
                "Every day of such long walks costs condition — head back to water today.",
}

# Actionable advice when the forage/season is harsh (we know this from the
# satellite indices — no extra compute).
DRY_HARSH_SW = ("☀️ Msimu ni mkavu na malisho ni machache. Ushauri: wanyama wanywe maji "
                "mapema asubuhi, waende malisho karibu na maji, na usiwakimbize "
                "matembezi marefu kila siku — wasipoteze hali.")
DRY_HARSH_EN = ("☀️ Dry season — forage is scarce. Advice: water your animals early, "
                "let them graze closer to water, and avoid long forced walks every "
                "day so they don't lose condition.")


# --- quantity, direction, freshness -----------------------------------------
# These three lines are what turns "one word about 1,960 km²" into something a
# herder can act on. All three come from data the system already computes.

#: Deliberately COARSE bands rather than a raw percentage.
#:
#: The thresholds behind the classification are, by config/advisory_thresholds.yaml's
#: own admission, "starting defaults, not validated against ground truth yet".
#: A label degrades gracefully when a threshold is slightly off; "17%" invites a
#: precision the data cannot yet support. Move to numbers after the thresholds
#: are calibrated against real herder reports (research note R1).
_QUANTITY_BANDS_SW = [
    (0.65, "Sehemu kubwa ya eneo hili ina malisho"),
    (0.35, "Karibu nusu ya eneo hili ina malisho"),
    (0.15, "Sehemu ndogo tu ya eneo hili ina malisho"),
    (0.0,  "Eneo hili lina malisho machache sana"),
]
_QUANTITY_BANDS_EN = [
    (0.65, "Most of this area has grazing"),
    (0.35, "About half of this area has grazing"),
    (0.15, "Only a small part of this area has grazing"),
    (0.0,  "There is very little grazing in this area"),
]

#: Fallback cap when the caller does not pass the herder's reach. Beyond their
#: own effective radius the "nearest good patch" is not a walk, it is a
#: migration, and offering it as guidance is bad advice: a cattle herder with a
#: 7 km ring must not be pointed at grazing 18 km away as though they could
#: simply go there.
MAX_PATCH_GUIDANCE_KM = 30.0

#: Past this the satellite reading is old enough to say so out loud.
SOFTEN_AFTER_DAYS = 21


def _quantity_line(class_fractions: dict[str, float] | None, english: bool) -> str | None:
    """How much of the ring is grazeable, as a band rather than a number."""
    if not class_fractions:
        return None
    usable = (class_fractions.get("green_growing", 0.0)
              + class_fractions.get("dry_forage", 0.0))
    bands = _QUANTITY_BANDS_EN if english else _QUANTITY_BANDS_SW
    text = next(t for floor, t in bands if usable >= floor)
    green = class_fractions.get("green_growing", 0.0)
    # Fresh green is worth naming separately: it is better feed than cured grass
    # and it is the thing a herder will walk furthest for.
    if green >= 0.15:
        text += (" (some of it fresh green)" if english
                 else " (baadhi ni malisho mabichi)")
    return text + "."


def _direction_line(bearing_deg: float | None, distance_km: float | None,
                    english: bool, max_km: float | None = None) -> str | None:
    """Which way to walk. Already computed for the map, previously discarded.

    `max_km` is the herder's effective reach for their species and watering
    interval. A patch beyond it is real, but walking to it is not a day's
    grazing trip, so we stay silent rather than imply it is.
    """
    if bearing_deg is None or distance_km is None:
        return None
    if distance_km > (max_km or MAX_PATCH_GUIDANCE_KM):
        return None
    from app.services.map_renderer import _compass_label, _compass_swa

    if distance_km < 0.5:
        return ("The best grazing is right around you."
                if english else "Malisho bora yapo hapa ulipo.")
    if english:
        return (f"Best grazing: {_compass_label(bearing_deg)}, "
                f"{distance_km:.1f} km from you.")
    return (f"Malisho bora: {_compass_swa(bearing_deg)}, "
            f"km {distance_km:.1f} kutoka ulipo.")


def _presence_line(presence, observed_at: datetime | None, english: bool) -> str | None:
    """What the satellite actually saw at the water point, and when."""
    table = PRESENCE_TEXT_EN if english else PRESENCE_TEXT_SW
    text = table.get(presence)
    if text is None:
        return None
    if observed_at is None:
        when = "recently" if english else "hivi karibuni"
    else:
        when = f"on {observed_at:%d %b}" if english else f"tarehe {observed_at:%d/%m}"
    return text.format(when=when)


def _age_line(observed_at: datetime | None, english: bool,
              now: datetime | None = None) -> str | None:
    """Tell the herder how old the satellite reading is.

    piosphere_zones.last_computed was written by four pipeline scripts and read
    by nothing, so a six-month-old reading was served with exactly the same
    confidence as one from yesterday, and the date never reached the herder.
    """
    if observed_at is None:
        return None
    if now is None:
        now = datetime.now(observed_at.tzinfo) if observed_at.tzinfo else datetime.now()
    days = (now - observed_at).days
    if days < 0:
        return None
    if days > SOFTEN_AFTER_DAYS:
        return (f"(Satellite image is {days} days old — conditions may have changed.)"
                if english else
                f"(Picha ya satelaiti ina siku {days} — hali huenda imebadilika.)")
    return (f"(Satellite image: {observed_at:%d %b}.)" if english
            else f"(Satelaiti: picha ya {observed_at:%d/%m}.)")


def format_advisory_message(
    language: str,
    species: Species,
    distance_km: float,
    condition: ForageCondition,
    seasonally_normal: bool | None,
    curing_stage_note: str | None,
    water_reliability: WaterReliability,
    grazing_zone: str | None = None,
    effective_radius_km: float | None = None,
    dry_harsh: bool = False,
    water_presence=None,
    class_fractions: dict[str, float] | None = None,
    patch_bearing_deg: float | None = None,
    patch_distance_km: float | None = None,
    observed_at: datetime | None = None,
    #: Injectable clock. Production leaves it None; tests pin it so a golden
    #: file cannot drift just because the date rolled over.
    now: datetime | None = None,
) -> str:
    if language not in _SUPPORTED:
        language = "swahili"

    english = language == "english"
    quantity = _quantity_line(class_fractions, english)
    direction = _direction_line(patch_bearing_deg, patch_distance_km, english,
                                max_km=effective_radius_km)
    age = _age_line(observed_at, english, now=now)
    presence = _presence_line(water_presence, observed_at, english)

    if english:
        species_label = SPECIES_LABEL_EN.get(species, species)
        condition_text = CONDITION_TEXT_EN[condition]
        water_text = WATER_TEXT_EN[water_reliability]
        lines = [
            f"For your {species_label}: nearest water is {distance_km:.1f} km away.",
            quantity or f"Pasture condition near that water: {condition_text}.",
        ]
        if direction:
            lines.append(direction)
        # `seasonally_normal is None` means VCI could not be read. Say nothing
        # rather than implying the season is abnormal.
        if condition == ForageCondition.BARE_DEGRADED and seasonally_normal is False:
            lines.append("This is worse than usual for this season — consider other areas.")
        elif condition == ForageCondition.BARE_DEGRADED and seasonally_normal is True:
            lines.append("This is normal for the dry season.")
        if curing_stage_note == "still_curing":
            lines.append("Grass is still curing, not fully dry yet.")
        if grazing_zone in ZONE_WARN_EN and effective_radius_km:
            lines.append(ZONE_WARN_EN[grazing_zone].format(eff=effective_radius_km))
        if dry_harsh:
            lines.append(DRY_HARSH_EN)
        lines.append(f"Water: {water_text}.")
        if presence:
            lines.append(presence)
        if age:
            lines.append(age)
        return "\n".join(lines)

    # swahili (default)
    species_label = SPECIES_LABEL_SW.get(species, species)
    condition_text = CONDITION_TEXT_SW[condition]
    water_text = WATER_TEXT_SW[water_reliability]
    lines = [
        f"Kwa {species_label} wako: maji ya karibu yapo umbali wa {distance_km:.1f} km.",
        quantity or f"Hali ya malisho karibu na maji hayo: {condition_text}.",
    ]
    if direction:
        lines.append(direction)
    if condition == ForageCondition.BARE_DEGRADED and seasonally_normal is False:
        lines.append("Hali hii ni mbaya zaidi ya kawaida kwa msimu huu — angalia maeneo mengine.")
    elif condition == ForageCondition.BARE_DEGRADED and seasonally_normal is True:
        lines.append("Hii ni ya kawaida kwa msimu huu wa kiangazi.")
    if curing_stage_note == "still_curing":
        lines.append("Nyasi bado inakauka, si kavu kabisa.")
    if grazing_zone in ZONE_WARN_SW and effective_radius_km:
        lines.append(ZONE_WARN_SW[grazing_zone].format(eff=effective_radius_km))
    if dry_harsh:
        lines.append(DRY_HARSH_SW)
    lines.append(f"Maji: {water_text}.")
    if presence:
        lines.append(presence)
    if age:
        lines.append(age)
    return "\n".join(lines)

