"""MALISHO YA LEO — the flow: pin in, answer out, walk stored.

Everything that costs money or touches the network is injected: the COG reader,
the "better patch" lookup, the manyatta registry and the WhatsApp sender. What is
under test is the ORDER and the guarantees:

  * a pin always gets an answer, even with no satellite picture and no manyatta;
  * a double tap does not double the ledger;
  * the manyatta is asked for exactly once, and only after the answer;
  * the calibration question is asked on every real reading;
  * the answer never contains a drug, a dose, a diagnosis or a body-weight claim.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import io  # noqa: E402

from app.services import forage, grazing_flow  # noqa: E402

CALLS: dict = {"records": [], "states": [], "sent": []}


class Herder:
    id = "11111111-1111-1111-1111-111111111111"
    phone_number = "+254700000001"
    preferred_language = "swahili"
    primary_species = "cattle"
    voice_replies = False
    water_source_id = None
    water_interval = "daily"
    herd_composition = {"cattle": 18}


# --- inject everything that would touch the world ---------------------------
STATE: dict = {"manyatta": {"id": "m-1", "lat": 0.35, "lon": 37.58, "name": "Test"}}


def _fake_manyatta(phone):
    return STATE["manyatta"]


def _fake_record(phone, **kw):
    CALLS["records"].append(kw)
    return "event-1"


def _fake_minutes(phone):
    return (None, None)


def _fake_set_state(phone, state, data=None):
    CALLS["states"].append((state, data or {}))


def _fake_origin(phone, pastoralist):
    return {"lat": 0.35, "lon": 37.58, "from_manyatta": True,
            "manyatta_id": "m-1", "name": "Test"}


grazing_flow.manyattas.manyatta_for_phone = _fake_manyatta
grazing_flow.manyattas.record_grazing_event = _fake_record
grazing_flow.manyattas.recent_event_minutes = _fake_minutes
grazing_flow.conversation.set_state = _fake_set_state
grazing_flow.water_source_for_pin = lambda lon, lat, sp, iv: ("point-1", "reachable")
grazing_flow.snapshot_for = lambda pid: "05 Sep"
grazing_flow.temp_max_for = lambda pid: 37.0
grazing_flow.origin_for = _fake_origin
grazing_flow.mint_token = lambda phone: "tok-abc123"
grazing_flow.whatsapp_client.send_location_request = \
    lambda *a, **k: CALLS["sent"].append(("location", a, k))

DRY = forage.PatchSample(ndvi=0.08, satvi=0.30, bsi=0.06, ndmi=-0.05,
                         n_pixels=40, radius_m=250.0, pixel_m=94.0)
GREEN = forage.PatchSample(ndvi=0.46, satvi=0.33, bsi=0.06, ndmi=0.05,
                           n_pixels=40, radius_m=250.0, pixel_m=94.0)

# --- 1) the prompt: a MAP he can tap, and the honest fallback ---------------
base = grazing_flow.prompt_text("swa", "https://x/mapview/?graze=1&t=tok")
assert "ramani" in base.lower() and "https://x/mapview" in base, base
# the map is the PRIMARY instruction now, so the body mentions it even with no
# link; what disappears when there is no URL is the URL itself
assert "ramani" in grazing_flow.prompt_text("swa", "").lower()
assert "https://" not in grazing_flow.prompt_text("swa", "")
assert "location" in grazing_flow.prompt_text("eng", "").lower()
assert set(grazing_flow.LOCATION_BUTTON) == {"swahili", "english"}
assert set(grazing_flow.MAP_BUTTON) == {"swahili", "english"}
assert len(grazing_flow.MAP_BUTTON["swahili"]) <= 20, "WhatsApp's button-text limit"
print("prompt: map first, URL only as the fallback, both buttons present OK")

# --- 1b) the tappable map is what actually goes out -------------------------
sent: list = []
grazing_flow.whatsapp_client.send_cta_url_button = \
    lambda to, body, btn, url, image_url=None: (
        sent.append(("cta", body, btn, url, image_url)), True)[1]
grazing_flow.whatsapp_client.send_location_request = \
    lambda to, body, btn: sent.append(("loc", body, btn))
grazing_flow.map_url = lambda phone, p, lon=None, lat=None, point_id=None: (
    "https://x/mapview/?lat=0.35&lon=37.58&species=cattle&interval=daily"
    "&lang=swa&graze=1&t=tok-abc123" + (f"&id={point_id}" if point_id else ""))
grazing_flow.map_image_url = lambda p, lon, lat, pid: (
    f"https://x/map/{pid}.png?lat={lat}&lon={lon}&pasture=1" if pid else None)
grazing_flow.water_source_for_pin = lambda lon, lat, sp, iv: ("point-1", "reachable")

CALLS["states"].clear()
grazing_flow.prompt_for_pin("+254700000001", Herder(), lon=0.35, lat=37.58)
assert [s[0] for s in sent] == ["cta"], sent
assert sent[0][2] == grazing_flow.MAP_BUTTON["swahili"], sent[0][2]
assert "graze=1&t=tok-abc123" in sent[0][3], sent[0][3]
# the water point id is what makes the page draw the pasture layer: he must SEE
# where the grass is while choosing, not tap a blank street map
assert "id=point-1" in sent[0][3], sent[0][3]
assert sent[0][4] and "map/point-1.png" in sent[0][4], sent[0][4]
assert "ramani" in sent[0][1].lower()
# the state is set BEFORE the prompt, so a fast reply is never missed
assert CALLS["states"] and CALLS["states"][-1][0] == "graze.await", CALLS["states"]

# no map link possible: the one-tap location button takes over, and it is the
# ONLY message — asking twice is how a herder learns to ignore us
sent.clear()
grazing_flow.whatsapp_client.send_cta_url_button = lambda *a, **k: False
grazing_flow.prompt_for_pin("+254700000001", Herder(), lon=0.35, lat=37.58)
assert [s[0] for s in sent] == ["loc"], sent
assert "ramani" in sent[0][1].lower() and "https://x/mapview" in sent[0][1], sent[0][1]
grazing_flow.whatsapp_client.send_cta_url_button = \
    lambda to, body, btn, url, image_url=None: (
        sent.append(("cta", body, btn, url, image_url)), True)[1]
print("prompt wiring: CTA with the map image, token in the URL, single-message fallback OK")
# --- 2) a real reading: the whole answer, and the calibration question ------
CALLS["records"].clear()
CALLS["states"].clear()
res = grazing_flow.handle_pin("+254700000001", Herder(), 0.395, 37.58, source="pin",
                              sample_fn=lambda pid, lon, lat: DRY,
                              guidance_fn=lambda *a: None)
assert res.ok and res.recorded and not res.duplicate, res
assert res.text.startswith("🌿 MALISHO YA LEO"), res.text[:40]
assert "Picha ya 05 Sep" in res.text and "kadirio" in res.text.lower()
assert "kutoka manyatta yako" in res.text, "the origin must be named"
assert res.ask_manyatta is False, "the manyatta is known: never ask again"
assert "Malisho hapo yalikuwaje" in res.question
assert [b[0] for b in res.buttons] == ["gq:1", "gq:2", "gq:3"], res.buttons
assert res.spoken and "kilometa" in res.spoken and "MJ" not in res.spoken
forage.assert_clean(res.text)
assert CALLS["states"] and CALLS["states"][-1][0] == "graze.quality"
assert CALLS["records"] and CALLS["records"][-1]["source"] == "pin"
# the walk is the manyatta -> pin round trip, and it is in the message
assert 9 < res.advice.walk_km < 11, res.advice.walk_km
assert res.advice.quality.snapshot_as_of == "05 Sep"
assert res.point_id == "point-1"
print("reading: answer + question + stored walk, origin named, no re-asking OK")

# --- 3) a double tap must not double the ledger ----------------------------
grazing_flow.manyattas.recent_event_minutes = lambda phone: ("event-x", 5.0)
CALLS["records"].clear()
dup = grazing_flow.handle_pin("+254700000001", Herder(), 0.395, 37.58, source="pin",
                              sample_fn=lambda pid, lon, lat: DRY,
                              guidance_fn=lambda *a: None)
assert dup.ok and dup.duplicate and not dup.recorded, dup
assert CALLS["records"] == [], "a second tap inside 20 minutes must not store a walk"
grazing_flow.manyattas.recent_event_minutes = _fake_minutes
print("dedupe: one walk, one row, even when the pin arrives twice OK")

# --- 4) no reading: the REASON is named, and never "no satellite picture" ---
CALLS["records"].clear()
none = grazing_flow.handle_pin("+254700000001", Herder(), 1.0, 38.0, source="pin",
                               sample_fn=lambda pid, lon, lat: None,
                               guidance_fn=lambda *a: None)
assert not none.ok and none.reason in ("outside", "no_raster"), none
assert "Tumeandikisha mlipopanda" in none.text, none.text
assert "hatuna picha ya satellite" not in none.text.lower(), \
    "never tell a herder we have no satellite picture while he looks at one"
assert CALLS["records"], "the pin is real even when the picture is not"
assert CALLS["records"][-1]["advice"] is None
assert none.question == "", "no reading, so no question about a patch we could not see"

# nothing registered anywhere near the tap is its own, different situation
_saved_candidates = grazing_flow.patch_candidates
grazing_flow.patch_candidates = lambda *a, **k: []
nop = grazing_flow.handle_pin("+254700000001", Herder(), 1.0, 38.0, source="map")
assert not nop.ok and nop.reason == "no_point", nop
assert "chanzo chochote cha maji" in nop.text, nop.text
grazing_flow.patch_candidates = _saved_candidates
print("empty states: the cause is named, and it is never 'no satellite picture' OK")

# --- 4b) a tap is read from the raster the MAP PAINTED ----------------------
# The complaint this exists for: a herder tapped a spot on the satellite picture we
# drew for him and was told we have no satellite picture. The page knows which file
# it painted (`id=`), so that one must be tried first — he tapped pixels we showed
# him — and the coordinates are only a fallback.
tried: list = []


def _by_id(pid, lon, lat):
    tried.append(pid)
    return DRY if pid == "page-point" else None


hinted = grazing_flow.handle_pin("+254700000001", Herder(), 0.395, 37.58, source="map",
                                 point_id_hint="page-point", sample_fn=_by_id,
                                 guidance_fn=lambda *a: None)
assert hinted.ok and hinted.point_id == "page-point", hinted
assert tried and tried[0] == "page-point", tried
assert hinted.text.startswith("🌿 MALISHO YA LEO")

# and if the painted raster cannot be read, the neighbours are tried in order
tried.clear()


def _by_id_dead(pid, lon, lat):
    tried.append(pid)
    return None if pid == "dead-point" else DRY


fallback = grazing_flow.handle_pin(
    "+254700000001", Herder(), 0.395, 37.58, source="map", point_id_hint="dead-point",
    sample_fn=_by_id_dead, guidance_fn=lambda *a: None)
assert fallback.ok and fallback.point_id == "point-1", fallback
assert tried and tried[0] == "dead-point" and len(tried) >= 2, tried
print("tap reading: the painted raster wins, neighbours are the fallback OK")

# --- 5) the manyatta: asked once, after the answer, never again -------------
STATE["manyatta"] = None
CALLS["records"].clear()
fresh = grazing_flow.handle_pin("+254700000001", Herder(), 0.395, 37.58, source="pin",
                                sample_fn=lambda pid, lon, lat: DRY,
                                guidance_fn=lambda *a: None)
assert fresh.ok and fresh.ask_manyatta is True, fresh
assert fresh.text.startswith("🌿 MALISHO YA LEO"), "the answer still comes FIRST"
assert fresh.question and fresh.buttons, "the calibration question is still asked"
STATE["manyatta"] = {"id": "m-1", "lat": 0.35, "lon": 37.58, "name": "Test"}
again = grazing_flow.handle_pin("+254700000001", Herder(), 0.395, 37.58, source="pin",
                                sample_fn=lambda pid, lon, lat: DRY,
                                guidance_fn=lambda *a: None)
assert again.ask_manyatta is False, "registered once must mean never asked again"
print("manyatta: answer first, asked once, never twice OK")

# --- 6) a better patch is found, quantified, and offered first --------------
calls = {"n": 0}


def _sample_two(pid, lon, lat):
    calls["n"] += 1
    return DRY if calls["n"] == 1 else GREEN


moved = grazing_flow.handle_pin("+254700000001", Herder(), 0.395, 37.58, source="pin",
                                sample_fn=_sample_two,
                                guidance_fn=lambda pid, lon, lat: (45.0, 5.0))
codes = [o.code for o in moved.advice.options]
assert "move" in codes, codes
assert codes[0] == "move", "the free option must be first when it exists"
mv = moved.advice.options[0]
assert mv.free and mv.saves_mj > 0 and "malisho" in mv.title_swa.lower() or mv.saves_mj > 0
assert "Kaskazini-Mashariki" in mv.title_swa, mv.title_swa
assert codes.index("move") < codes.index("feed"), codes
print("better patch: located, read, quantified, offered before anything to buy OK")

# --- 7) the delivery rule: two messages, never three -------------------------
from app.routers import whatsapp as wa  # noqa: E402

out: list = []
wa.whatsapp_client.send_location_request = \
    lambda to, body, btn, *a, **k: out.append(("loc", body, btn))
wa.whatsapp_client.send_cta_url_button = \
    lambda to, body, btn, url, image_url=None, *a, **k: (
        out.append(("cta", body, url)), True)[1]
wa.whatsapp_client.send_quick_reply_buttons = \
    lambda to, body, btns, *a, **k: out.append(("btn", body, btns))
wa.whatsapp_client.send_text = lambda to, body, *a, **k: out.append(("text", body))
wa._send_reply = lambda phone, p, text, voice=False: out.append(("reply", text, voice))


def _result(**kw):
    base = dict(ok=True, text="TEXT", spoken="SPOKEN", question="Q?",
                buttons=[("gq:1", "1"), ("gq:2", "2"), ("gq:3", "3")])
    base.update(kw)
    return grazing_flow.GrazingResult(**base)


wa._deliver_grazing_result("+254", Herder(), _result(ask_manyatta=True))
assert [o[0] for o in out] == ["reply", "loc"], out
assert "manyatta" in out[1][1].lower(), out[1]
out.clear()
wa._deliver_grazing_result("+254", Herder(), _result(ask_manyatta=False))
assert [o[0] for o in out] == ["reply", "btn"], out
assert "Q?" in out[1][1] and len(out[1][2]) == 3, out[1]
out.clear()
speaker = Herder()
speaker.voice_replies = True
wa._deliver_grazing_result("+254", speaker, _result(ask_manyatta=False))
assert [o[0] for o in out] == ["reply", "text", "btn"], out
assert out[0][1] == "SPOKEN" and out[0][2] is True, "voice first, numbers as text too"
assert out[1][1] == "TEXT"
out.clear()
wa._deliver_grazing_result("+254", Herder(),
                           _result(prompt_location=True, ask_manyatta=True,
                                   lon=0.35, lat=37.58))
assert [o[0] for o in out] == ["cta"], \
    "after the manyatta, the grazing prompt must be the MAP, and only the map"
assert "graze=1&t=" in out[0][2] and "id=point-1" in out[0][2], out[0]
print("delivery: never more than two messages, voice carries the spoken summary OK")

# --- 8) reachability + the token policy (source-level, no live API) ---------
import inspect  # noqa: E402

assert wa.MENU_NUMBERS["11"] == "graze", wa.MENU_NUMBERS
assert wa.SERVICE_ALIASES["svc:graze"] == "graze"
for sections in (wa.MENU_SECTIONS, wa.MENU_SECTIONS_EN):
    ids = [row[0] for _, rows in sections for row in rows]
    assert "svc:graze" in ids, ids
    assert len(ids) <= 10, f"{len(ids)} rows is over WhatsApp's list limit"
for lang in ("swahili", "english"):
    assert "11." in wa.MENU_MSG[lang], f"11 missing from the {lang} fallback menu"
body = inspect.getsource(wa._run_service)
assert '"graze"' in body, "_run_service has no graze branch"
assert "grazing_flow" in inspect.getsource(wa._handle_graze_location)
assert "manyattas.manyatta_for_phone" in inspect.getsource(wa._handle_graze_request), \
    "the manyatta must be checked BEFORE asking for a pin"

src_gr = io.open(pathlib.Path(__file__).resolve().parents[1]
                 / "app/routers/grazing.py", encoding="utf-8").read()
assert "phone_for_token" in src_gr and 'payload.get("token")' in src_gr
assert 'payload["phone"]' not in src_gr and 'payload.get("phone")' not in src_gr, \
    "the public map endpoint must never take a phone number"
assert "expires_at" in io.open(pathlib.Path(__file__).resolve().parents[1]
                               / "app/services/grazing_flow.py", encoding="utf-8").read()

# the token table is the only thing standing in for identity
sql = io.open(pathlib.Path(__file__).resolve().parents[1]
              / "migrations/014_grazing_ledger.sql", encoding="utf-8").read()
assert "graze_tokens" in sql and "48 hours" in sql
assert "manyatta_id" in sql and "grazing_events" in sql
print("reachability: menu row, number 11, service branch, token-only map endpoint OK")

print("\nGRAZING FLOW OK")



