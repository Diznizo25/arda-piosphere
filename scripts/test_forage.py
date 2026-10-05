"""MALISHO YA LEO — the grazing ledger's arithmetic, wording and boundaries.

Four things are asserted mechanically here, because they are the promises the
feature makes to a herder who is going to spend money on the answer:

  1. the BAND: no reading is ever a point value, and the band widens where the
     sward is thin (where the satellite is least trustworthy);
  2. the VERDICT is only "deficit" when the WHOLE uncertainty band is negative —
     a noisy reading must never turn into "buy feed";
  3. nothing is recommended when the animals are already gaining (the ledger must
     be able to say "add nothing", or it is an advert);
  4. the BOUNDARY: no drug, no dose, no diagnosis, no body-weight claim, ever.

Pure module: no DB, no network, no COG. The satellite reader is injected.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import io  # noqa: E402

from app.services import forage  # noqa: E402

# --- 1) parameters load and are honest about their status --------------------
p = forage.load_params()
assert p["status"] == "needs_review", "the file must say it is not calibrated yet"
assert p["energy"]["terrain_factor"] == 1.0, \
    "slope is NOT computed yet, so the factor must stay neutral"
assert p["disclaimer"]["swa"] and "kadirio" in p["disclaimer"]["swa"].lower()
print("params: loaded, uncalibrated status declared, no fake slope factor OK")

# --- 2) geometry -------------------------------------------------------------
km = forage.walk_km(0.35, 37.58, 0.395, 37.58)          # ~5 km out, doubled back
assert 9.5 < km < 10.5, km
assert abs(forage.walk_km(0.35, 37.58, 0.35, 37.58) - 0.0) < 0.01
one_way = forage.walk_km(0.35, 37.58, 0.395, 37.58, round_trip=False)
assert abs(km - 2 * one_way) < 0.05, (km, one_way)
nlat, nlon = forage.offset_point(0.35, 37.58, 0.0, 10.0)   # due north, 10 km
assert nlat > 0.35 and abs(nlon - 37.58) < 0.02, (nlat, nlon)
assert abs(forage.haversine_km(0.35, 37.58, nlat, nlon) - 10.0) < 0.2
print("geometry: round trip, zero distance, bearing offset OK")

# --- 3) quality: the two axes, and the band ---------------------------------
green = forage.quality_from_bands(0.50, 0.34, 0.05, 0.06, snapshot_as_of="05 Sep")
assert green.condition == "green_growing", green.condition
assert green.protein == "adequate" and green.me_mj_per_kg_dm == p["quality"]["green_growing"]["me_mj_per_kg_dm"]
assert green.utilisable_mid_kg_ha > green.biomass_mid_kg_ha * 0.25

# cured dry grass: NDVI is low but SATVI says there IS standing forage — the exact
# correction this system exists to make, so the class must not be "bare".
cured = forage.quality_from_bands(0.08, 0.30, 0.06, -0.05, snapshot_as_of="05 Sep")
assert cured.condition == "dry_forage_available", cured.condition
assert cured.curing == "fully_cured", cured.curing
assert cured.label_swa == "nyasi kavu" and cured.protein == "low"
assert cured.me_mj_per_kg_dm == p["quality"]["dry_cured"]["me_mj_per_kg_dm"]

curing = forage.quality_from_bands(0.08, 0.30, 0.06, 0.05, snapshot_as_of="05 Sep")
assert curing.curing == "still_curing", curing.curing
assert curing.me_mj_per_kg_dm == p["quality"]["dry_curing"]["me_mj_per_kg_dm"]
assert curing.me_mj_per_kg_dm > cured.me_mj_per_kg_dm, \
    "grass still curing must be worth more than fully cured grass"

bare = forage.quality_from_bands(0.05, 0.02, 0.12, -0.05)
assert bare.condition == "bare_degraded", bare.condition
assert bare.utilisable_hi_kg_ha == 0, "bare ground yields nothing to harvest"

# the band, always: lo < mid < hi, and its width is the declared uncertainty
q = cured
assert q.biomass_lo_kg_ha < q.biomass_hi_kg_ha
ratio = q.biomass_hi_kg_ha / q.biomass_lo_kg_ha
unc = p["biomass"]["uncertainty_pct"] / 100.0
assert abs(ratio - (1 + unc) / (1 - unc)) < 0.02, ratio
assert q.snapshot_as_of == "05 Sep"
print("quality: green / curing / cured / bare, and a band that admits its width OK")

# --- 3b) the patch is a PATCH, not the whole ring ---------------------------
# The transform is in degrees; converting a 250 m radius with the wrong units made
# the "neighbourhood" the entire 25 km ring (77k pixels) and averaged a landscape
# into a spot reading. This asserts the window stays a spot.
import numpy as np  # noqa: E402

try:
    from app.services import raster_read  # noqa: F401

    _HAVE_RASTER = True
except Exception:  # noqa: BLE001 — e.g. Windows Application Control blocking a DLL
    _HAVE_RASTER = False


class _FakeTransform:
    a = 0.001          # ~111 m per pixel in longitude at this latitude
    e = -0.001
    c = 37.0
    f = 1.0


if not _HAVE_RASTER:
    # Loud, not silent: this suite is the gate that runs in the container, and a
    # quietly skipped assertion is exactly how a wrong-units bug ships.
    print("patch sampling: SKIPPED — rasterio unavailable on this host (run in Docker)")
else:
    grid = np.full((5, 64, 64), 0.10)
    grid[1, 0:25, 0:25] = 0.50          # a bright block far from the pin
    _real_read = raster_read.read_overview_array
    raster_read.read_overview_array = lambda *a, **k: (grid, _FakeTransform())
    try:
        ps = forage.sample_patch("fake-point", 37.0325, 0.9685, radius_m=250.0)
    finally:
        raster_read.read_overview_array = _real_read
    assert ps is not None, "the sampler returned nothing for a valid in-bounds pin"
    assert ps.n_pixels <= 49, f"{ps.n_pixels} pixels is not a 250 m patch"
    assert abs(ps.pixel_m - 111.3) < 6, ps.pixel_m
    assert abs(ps.satvi - 0.10) < 0.02, \
        f"the sample was contaminated by a far-away block (satvi={ps.satvi})"
    print("patch sampling: a 250 m disc, not a whole-ring average OK")

# --- 4) the ledger: the animal's side ---------------------------------------
spec = p["species"]["cattle"]
led = forage.ledger("cattle", 18, 14.0, cured, temp_max_c=38.0)
assert abs(led.body_kg - spec["body_kg"]) < 0.01
expected_maint = p["energy"]["maintenance_mj_per_kg075"] * (spec["body_kg"] ** 0.75)
assert abs(led.maintenance_mj - expected_maint) < 0.15, (led.maintenance_mj, expected_maint)
assert abs(led.activity_mj - expected_maint * p["energy"]["activity_frac"]) < 0.2
# locomotion: 2 J/kg/m over the whole walk, and it must scale with distance
loc_expected = p["energy"]["locomotion_j_per_kg_m"] * spec["body_kg"] * 14000 / 1_000_000
assert abs(led.locomotion_mj - loc_expected) < 0.1, (led.locomotion_mj, loc_expected)
assert led.heat_mj > 0, "38 C must cost something"
short = forage.ledger("cattle", 18, 4.0, cured, temp_max_c=38.0)
assert short.locomotion_mj < led.locomotion_mj < short.locomotion_mj * 4
assert short.required_mj < led.required_mj, "a longer walk must cost more energy"
assert led.walk_hours > 0 and led.walk_hours < 6, led.walk_hours
# no heat -> no heat term, and the same animal gets further
cool = forage.ledger("cattle", 18, 14.0, cured, temp_max_c=24.0)
assert cool.heat_mj == 0 and cool.required_mj < led.required_mj

# shoats and camels are not cattle with a different label
shoat = forage.ledger("shoat", 40, 6.0, cured)
camel = forage.ledger("camel", 10, 20.0, cured)
assert shoat.body_kg == p["species"]["shoat"]["body_kg"]
assert camel.body_kg == p["species"]["camel"]["body_kg"]
assert shoat.locomotion_mj < camel.locomotion_mj * 0.2, (shoat.locomotion_mj, camel.locomotion_mj)

# the harvest ceiling: thin swards let them fill up less, and at the very bottom
# no amount of hours helps — the sentence the message exists to carry
thin = forage.quality_from_bands(0.06, 0.08, 0.10, -0.05)
assert forage.harvest_factor(thin) < forage.harvest_factor(cured) < forage.harvest_factor(green)
assert forage.harvest_factor(bare) <= 0.2, "bare ground: they only get browse residue"
print("ledger: maintenance, activity, locomotion, heat, species, harvest ceiling OK")

# --- 5) the verdict is conservative, and it can say "add nothing" ------------
assert led.verdict == "deficit", led.verdict
assert led.balance_hi_mj < 0, "a deficit verdict requires the WHOLE band to be negative"
assert led.gap_mj > 0

# a green flush with a short walk: the animal is gaining, and the correct advice
# is to spend NOTHING. If this ever fails, the feature has become an advert.
rich = forage.ledger("cattle", 18, 2.0, green, temp_max_c=26.0)
assert rich.verdict == "surplus", (rich.verdict, rich.balance_mj, rich.balance_lo_mj)
adv_rich = forage.advise(green, species="cattle", head_count=18, walk_distance_km=2.0,
                         temp_max_c=26.0, from_manyatta=True)
assert adv_rich.options == [], "no supplement may be offered while they are gaining"
msg_rich = forage.message(adv_rich, "swa")
assert "wanakusanya zaidi" in msg_rich.lower(), msg_rich
assert "ng'ombe" in msg_rich, "species must be named in the herder's language"
assert "cattle" not in msg_rich, msg_rich
assert "kJ" not in msg_rich

# a band that straddles zero is "holding", never a deficit and never a purchase
mid = forage.quality_from_bands(0.20, 0.18, 0.10, 0.0)
lead = forage.ledger("cattle", 18, 9.0, mid, temp_max_c=30.0)
assert lead.verdict in ("holding", "deficit", "surplus")
if lead.balance_lo_mj < 0 < lead.balance_hi_mj:
    assert lead.verdict == "holding", lead
    assert forage.build_options(mid, lead, species="cattle") == []
print("verdict: conservative band, holding in the middle, 'add nothing' possible OK")

# --- 6) the offsets: free first, then cheap, then costed, then the exit ------
adv = forage.advise(cured, species="cattle", head_count=18, walk_distance_km=14.0,
                    temp_max_c=38.0, from_manyatta=True)
codes = [o.code for o in adv.options]
assert codes[0] == "water" and adv.options[0].free, codes
assert "mineral" in codes and "feed" in codes, codes
assert codes.index("mineral") < codes.index("feed"), "cheap before expensive"
assert adv.recommended is not None and adv.recommended.recommended
assert adv.recommended.free, "the recommended first step must always be free"
feed = next(o for o in adv.options if o.code == "feed")
assert feed.kg_per_head and feed.kg_per_head <= p["offset"]["energy_feed"]["max_kg_per_head_day"]
assert feed.cost_per_head_ksh and feed.herd_cost_ksh
assert feed.herd_cost_ksh == int(round(feed.cost_per_head_ksh * 18 / 10) * 10), feed
# the herder's own words for the mineral: a lick, described as feed
mineral = next(o for o in adv.options if o.code == "mineral")
assert "chakula" in mineral.detail_swa, mineral.detail_swa
assert mineral.herd_cost_ksh and mineral.herd_cost_ksh > 0

# unknown herd size: per-head figures only, and NO invented total
adv0 = forage.advise(cured, species="cattle", head_count=0, walk_distance_km=14.0,
                     temp_max_c=38.0)
assert all(o.herd_cost_ksh is None for o in adv0.options), "no herd total without a count"
assert "KWA KILA MNYAMA" in forage.message(adv0, "swa")
assert "kundi ~KSh" not in forage.message(adv0, "swa")

# with a better patch named, the move option comes FIRST and it is quantified —
# this is the advice that costs nothing and the reason the feature is worth using
bq = forage.quality_from_bands(0.45, 0.33, 0.06, 0.05)
better = {"quality": bq, "walk_km": 4.0, "direction": "Kaskazini-Mashariki"}
advm = forage.advise(cured, species="cattle", head_count=18, walk_distance_km=14.0,
                     temp_max_c=38.0, from_manyatta=True, better=better)
assert advm.options[0].code == "move", [o.code for o in advm.options]
assert advm.options[0].free and advm.options[0].saves_mj > 0
assert "Kaskazini-Mashariki" in advm.options[0].title_swa, advm.options[0].title_swa
assert "Bila gharama" in advm.options[0].detail_swa
assert "nyasi bora" in advm.options[0].detail_swa and "kutembea" in advm.options[0].detail_swa, \
    "the saving must name its two parts, not just a total"

# EVERY option's title and detail, both languages, must pass the boundary — an
# option far down the list may never be rendered in a given message, but a herder
# can still be shown it, and a detail line can be forwarded on its own. This is the
# assertion that catches a clinical word hiding in an option nobody rendered (it is
# how "(madini, si dawa)" was caught in a live message).
for _adv in (adv, advm, adv0):
    for _o in _adv.options:
        for _lang in ("swa", "eng"):
            forage.assert_clean(_o.title(_lang))
            forage.assert_clean(_o.detail(_lang))
print("offsets: free-first order, costed supplements, move quantified, no fake totals OK")

# --- 7) wording: the date, the origin, and the honest emptiness -------------
text = forage.message(adv, "swa")
eng = forage.message(adv, "eng")
assert text.startswith("🌿 MALISHO YA LEO"), text[:40]
assert eng.startswith("🌿 GRAZING TODAY"), eng[:40]
assert "Picha ya 05 Sep" in text, text
assert "kutoka manyatta yako" in text, "the origin of the distance must be named"
assert "Wanashiba lakini wanapungua" in text, text
assert "Kadirio kutoka satellite" in text.replace("—", "-"), text
assert "kadirio" in text.lower() and "estimate" in eng.lower()
# per-heel numbers the herder can act on, and the household total
assert "MJ" in text and "kg" in text
assert "~KSh" in text and "kundi ~KSh" in text
# the quantity word and the quantity number must be the SAME quantity
u_lo, u_hi = str(q.utilisable_lo_kg_ha), str(q.utilisable_hi_kg_ha)
assert f"{u_lo}-{u_hi} kg kwa hekta" in text, text
assert str(q.biomass_hi_kg_ha) not in text, \
    "standing crop must not be printed beside a word about what they can eat"

# no snapshot date -> say so, never imply "today"
unknown_date = forage.quality_from_bands(0.08, 0.30, 0.06, -0.05)
adv_nd = forage.advise(unknown_date, species="cattle", head_count=18,
                       walk_distance_km=14.0, temp_max_c=38.0)
assert "tarehe haijulikani" in forage.message(adv_nd, "swa")
# no origin -> no distance column at all, rather than a made-up zero
adv_nw = forage.advise(cured, species="cattle", head_count=18, walk_distance_km=0.0,
                       temp_max_c=38.0)
assert "haujulikani bado" in forage.message(adv_nw, "swa")

# the two reasons a herder must be told plainly
assert "satellite" in forage.no_data_message("swa").lower()
assert "Tumeandikisha" in forage.no_data_message("swa"), "the walk is still recorded"
assert "no satellite" in forage.no_data_message("eng").lower()
assert "recorded" in forage.no_data_message("eng").lower()
print("wording: date, origin, honest empty states, per-head + household OK")

# --- 8) the boundary, asserted over every rendered string -------------------
samples = [text, eng, forage.message(advm, "swa"), forage.message(advm, "eng"),
           msg_rich, forage.message(adv0, "swa"), forage.message(adv_nw, "swa"),
           forage.message(adv_nd, "eng"),
           forage.spoken_summary(adv, "swa"), forage.spoken_summary(adv, "eng"),
           forage.spoken_summary(advm, "swa"), forage.spoken_summary(advm, "eng"),
           forage.quality_question("swa"), forage.quality_question("eng"),
           forage.thanks_for_quality("good", "swa"), forage.thanks_for_quality("poor", "eng"),
           forage.no_data_message("swa"), forage.no_data_message("eng")]
for s in samples:
    forage.assert_clean(s)
    low = s.lower()
    assert "ugonjwa" not in low and "ana minyoo" not in low, s
    # never a claim about the animal's own body weight or a weight change
    assert "ana kilo" not in low and "weight will" not in low, s
    assert "kg ya mnyama" not in low, s
# the negative control: the guard must actually fire
try:
    forage.assert_clean("Mpe dawa ya minyoo")
    raise AssertionError("the guardrail did not fire on a drug word")
except AssertionError as e:
    assert "forbidden" in str(e), e
# the voice summary must stay number-light and free of units a voice mangles
for s in (forage.spoken_summary(adv, "swa"), forage.spoken_summary(adv, "eng")):
    assert "MJ" not in s, s
assert "kilometa" in forage.spoken_summary(adv, "swa")
print("boundary: no drug, no dose, no diagnosis, no body-weight claim OK")

# --- 9) the one-tap answer ---------------------------------------------------
assert forage.quality_for_digit("1") == "good"
assert forage.quality_for_digit(" 3 ") == "poor"
assert forage.quality_for_digit("mazuri") == "good"
assert forage.quality_for_digit("mbaya") == "poor"
assert forage.quality_for_digit("12") is None, "a herd size is not an answer"
assert forage.quality_for_digit("gq:1") is None, \
    "the button id is stripped by the router, not swallowed here"
print("one-tap: 1/2/3 and the words, everything else ignored OK")

# --- 10) the service must be REACHABLE, not merely correct ------------------
# A herder does not read release notes: if the grazing ledger is not in the menu,
# the number pad, the location path AND the map, it may as well not exist.
root = pathlib.Path(__file__).resolve().parents[1]
wa = io.open(root / "app/routers/whatsapp.py", encoding="utf-8").read()
assert "🌿 MALISHO YA LEO" in wa and "🌿 GRAZING TODAY" in wa, "not in the menu"
assert '"11": "graze"' in wa, "menu number 11 must resolve to the grazing service"
assert '"svc:graze": "graze"' in wa, "the clickable row has no service behind it"
assert 'elif service == "graze":' in wa
assert "_handle_graze_request(phone, pastoralist" in wa
assert '"graze.await"' in wa and '"graze.manyatta"' in wa, "the pin flow has no state"
assert '"graze.quality"' in wa, "the one-tap grass answer has no state"
assert "_handle_graze_quality" in wa and 'startswith("gq:")' in wa
assert "GRAZE_KEYWORDS" in wa and "malisho ya leo" in wa
assert 'record_ground_truth' in wa and 'pasture_good' in wa, "no calibration loop"

wac = io.open(root / "app/services/whatsapp_client.py", encoding="utf-8").read()
assert "def send_cta_url_button" in wac, "no tap-the-map button"
assert '"cta_url"' in wac and '"display_text"' in wac
assert "image header" in wac, "the map must ride as the message header"
assert "send_location_request" in wac and "location_request_message" in wac
assert "falling back to text" in wac, "a rejected button must fall back to text"
gr_src = io.open(root / "app/services/grazing_flow.py", encoding="utf-8").read()
assert "def send_pin_prompt" in gr_src, "the map-first prompt is not a single place"
assert "MAP_BUTTON" in gr_src and "&id=" in gr_src, \
    "the tap-map must be sent with the water point id so the pasture layer draws"
assert "def map_image_url" in gr_src, "no map image for the message header"

dev = io.open(root / "app/routers/dev.py", encoding="utf-8").read()
assert '@router.post("/graze")' in dev, "no /dev/graze probe to review the wording"

gr = io.open(root / "app/routers/grazing.py", encoding="utf-8").read()
assert '@router.post("/pin")' in gr and "phone_for_token" in gr, "map tap has no endpoint"

mv = io.open(root / "app/routers/mapview.py", encoding="utf-8").read()
assert "graze: int = Query" in mv and "D.graze" in mv, "the map has no tap-to-pin mode"
assert "g_hint" in mv and "D.graze.endpoint" in mv

main = io.open(root / "app/main.py", encoding="utf-8").read()
assert "grazing.router" in main, "the grazing endpoint is not mounted"

sql = io.open(root / "migrations/014_grazing_ledger.sql", encoding="utf-8").read()
for needle in ("create table if not exists manyattas", "create table if not exists grazing_events",
               "create table if not exists graze_tokens", "herder_quality",
               "enable row level security", "force row level security"):
    assert needle in sql, f"{needle!r} missing from migration 014"
assert sql.count("enable row level security") == sql.count("force row level security")
print("wiring: menu, number 11, keywords, pin flow, map tap, probe, migration OK")

print("\nFORAGE LEDGER OK")




