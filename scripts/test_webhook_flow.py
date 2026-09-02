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
        is_onboarded=True, first_name="Test",
    )
    SENT.clear()
    asked = []
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
