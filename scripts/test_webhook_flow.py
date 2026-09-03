"""Mock-based test of webhook dedup + the confirm-first location flow.

Covers:
  * _already_processed dedups the same message id (Meta redeliveries)
  * _handle_location asks to confirm the water point FIRST for an onboarded
    herder who hasn't confirmed yet (never a presumptuous advisory)
"""
from __future__ import annotations

import sys
import types

sys.path.insert(0, ".")

from app.routers import whatsapp  # noqa: E402
from app.services import conversation  # noqa: E402

SENT: list[str] = []


def _fake_get_state(phone):
    return None, {}


def _fake_set_state(phone, state, data=None):
    pass


def _fake_clear_state(phone):
    pass


conversation.get_state = _fake_get_state
conversation.set_state = _fake_set_state
conversation.clear_state = _fake_clear_state
conversation.state_age_seconds = lambda phone: 0


def fake_text(to, body):
    SENT.append(("text", body[:80]))


def fake_buttons(to, body, buttons):
    SENT.append(("buttons", body[:80]))


def main() -> None:
    # --- dedup ---
    whatsapp._PROCESSED.clear()
    assert whatsapp._already_processed("wamid.1") is False
    assert whatsapp._already_processed("wamid.1") is True, "same id must dedupe"
    assert whatsapp._already_processed("wamid.2") is False
    print("dedup: OK")

    # --- confirm-first location flow ---
    herder = types.SimpleNamespace(
        id="p1", phone_number="+254test", preferred_language="swahili",
        primary_species="camel", voice_replies=False, water_source_id=None,
        is_onboarded=True, first_name="Test", water_interval="daily",
    )
    SENT.clear()
    asked = []
    # NOTE: keep the REAL _ask_confirm_water on the confirm-first path so a
    # NameError/latent bug inside it fails this test (regression: _now_iso was
    # undefined and the herder got NO reply — logged crash only).
    nearby_fake = [{
        "water_source_id": f"w{i}", "name": f"Point {i}", "water_type": "well",
        "ward": "Oldonyiro", "county": "Isiolo", "lon": 37.5 + i * 0.01,
        "lat": 0.30, "distance_km": float(i), "direction_swa": "Kaskazini",
    } for i in range(1, 4)]
    real_ask = whatsapp._ask_confirm_water
    whatsapp.get_last_location = lambda phone: (37.58, 0.35)
    whatsapp.water_reach.list_nearby_water_sources = lambda lon, lat, limit=10: nearby_fake
    whatsapp.whatsapp_client.send_text = lambda to, body: SENT.append(("text", body[:80]))
    whatsapp.whatsapp_client.send_image_bytes_url = lambda to, url, caption=None: \
        SENT.append(("image", str(url)[:80]))
    whatsapp.whatsapp_client.send_interactive_list = lambda *a, **k: SENT.append(("list", "rows"))
    whatsapp._send_confirmation_map = lambda *a, **k: SENT.append(("map", "sent"))
    try:
        ok = real_ask(herder.phone_number, herder)
        assert ok is True, "confirm ask should succeed"
        assert any(s[0] == "text" for s in SENT), "list text must be sent"
        print("confirm-ask real path (no crash, list text sent): OK")
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"confirm-ask crashed: {e!r}") from e
    finally:
        whatsapp._ask_confirm_water = real_ask

    SENT.clear()
    whatsapp._ask_confirm_water = lambda phone, p: (asked.append(1), True)[1]
    whatsapp._handle_location(herder.phone_number, herder,
                              {"latitude": 0.35, "longitude": 37.58})
    assert asked, "unconfirmed onboarded herder must be asked to confirm FIRST"
    assert not any("advisory" in str(s) for s in SENT)
    print("confirm-first location flow: OK (no presumptuous advisory sent)")

    # --- confirmed herder still gets the advisory ---
    herder.water_source_id = "some-id"
    SENT.clear()
    adv = []
    whatsapp.get_advisory = lambda req: (adv.append(req), types.SimpleNamespace(
        found=True, message="advisory-msg"))[1]
    whatsapp._send_reply = lambda phone, p, text, voice=False: SENT.append(("reply", text))
    whatsapp._handle_location(herder.phone_number, herder,
                              {"latitude": 0.35, "longitude": 37.58})
    assert adv and any("advisory-msg" in s[1] for s in SENT), SENT
    print("confirmed herder gets advisory: OK")

    print("\nWebhook dedup + confirm-first flow OK.")


if __name__ == "__main__":
    main()
