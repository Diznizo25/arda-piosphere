"""Mock-based test of the water-confirmation flow escape hatches.

Verifies a stuck herder can ALWAYS get out or get the list again:
  'orodha'/'list'  -> re-ask (fresh list)  -> sends list + map
  'hakuna'/'none'  -> clears state, guides to PIN
  number in range  -> confirms the water point
  garbage          -> informative retry (never silent)
"""
from __future__ import annotations

import sys
import types

sys.path.insert(0, ".")

from app.routers import whatsapp  # noqa: E402
from app.services import conversation  # noqa: E402

SENT: list[str] = []
_STATE: dict[str, tuple[str, dict]] = {}


def _fake_get_state(phone):
    return _STATE.get(phone, (None, {}))


def _fake_set_state(phone, state, data=None):
    _STATE[phone] = (state, data or {})


def _fake_clear_state(phone):
    _STATE.pop(phone, None)


conversation.get_state = _fake_get_state
conversation.set_state = _fake_set_state
conversation.clear_state = _fake_clear_state
conversation.state_age_seconds = lambda phone: 0


def fake_text(to, body):
    SENT.append(("text", body[:120]))


def fake_image(to, url, caption=None):
    SENT.append(("image", url[:60]))


def fake_list(to, body, button, rows, footer=None, title=None):
    SENT.append(("list", rows[0][1] if rows else ""))


def main() -> None:
    whatsapp.whatsapp_client.send_text = fake_text
    whatsapp.whatsapp_client.send_image_bytes_url = fake_image
    whatsapp.whatsapp_client.send_interactive_list = fake_list

    herder = types.SimpleNamespace(
        id="p1", phone_number="+254test", preferred_language="swahili",
        primary_species="camel", voice_replies=False, water_source_id=None,
        is_onboarded=True, first_name="Test",
    )
    # Mock _ask_confirm_water so it just records a re-ask (avoids DB).
    whatsapp._ask_confirm_water = lambda phone, p: (SENT.append(("reask", "list+map")), True)[1]

    # Case 1: stale state with ids; herder replies with an out-of-range number
    # -> retry prompt (informative, not silent).
    conversation.set_state(herder.phone_number, "onboarding.water",
                           {"nearby": ["a", "b", "c"]})
    ok = whatsapp._handle_confirm_water_reply(herder.phone_number, herder, "9")
    assert ok is False, "out-of-range number should fall through"
    state, _ = conversation.get_state(herder.phone_number)
    assert state == "onboarding.water", "state should persist for retry"
    print("case1 (out-of-range -> retry prompt, state kept): OK")

    # Case 2: 'orodha' -> re-ask (fresh list)
    ok = whatsapp._handle_confirm_water_reply(herder.phone_number, herder, "orodha")
    assert ok is True and any(s[0] == "reask" for s in SENT), SENT
    print("case2 ('orodha' -> re-show list): OK")

    # Case 3: 'list' (english) -> re-ask
    SENT.clear()
    ok = whatsapp._handle_confirm_water_reply(herder.phone_number, herder, "list")
    assert ok is True and any(s[0] == "reask" for s in SENT)
    print("case3 ('list' -> re-show list): OK")

    # Case 4: 'hakuna' -> clears state, guides to PIN (message sent)
    SENT.clear()
    whatsapp.whatsapp_client.send_text = fake_text  # ensure full text captured
    ok = whatsapp._handle_confirm_water_reply(herder.phone_number, herder, "hakuna")
    state, _ = conversation.get_state(herder.phone_number)
    assert ok is True and state is None
    assert any(s[0] == "text" and ("PIN" in s[1] or "kukisajili" in s[1]) for s in SENT), SENT
    print("case4 ('hakuna' -> clear + PIN guide): OK")

    # Case 5: number within range -> confirm water source (mock the confirm fn)
    conversation.set_state(herder.phone_number, "onboarding.water", {"nearby": ["a", "b", "c"]})
    SENT.clear()
    confirmed = []
    whatsapp._confirm_water_source = lambda phone, p, wid: confirmed.append(wid)
    ok = whatsapp._handle_confirm_water_reply(herder.phone_number, herder, "2")
    assert ok is True and confirmed == ["b"], confirmed
    print("case5 (number in range -> confirms): OK")

    # Case 6: 'cancel' -> clear + guide
    SENT.clear()
    ok = whatsapp._handle_confirm_water_reply(herder.phone_number, herder, "cancel")
    state, _ = conversation.get_state(herder.phone_number)
    assert ok is True and state is None and SENT
    print("case6 ('cancel' -> clear): OK")

    # Case 7: stale state (asked_at > 24h ago) + a random message -> re-ask fresh
    import time
    from datetime import datetime, timezone
    stale_ts = datetime.now(timezone.utc).timestamp() - 26 * 3600
    from datetime import datetime as _dt
    stale_iso = _dt.fromtimestamp(stale_ts, tz=timezone.utc).isoformat()
    conversation.set_state(herder.phone_number, "onboarding.water",
                           {"nearby": ["old1", "old2"], "asked_at": stale_iso})
    SENT.clear()
    ok = whatsapp._handle_confirm_water_reply(herder.phone_number, herder, "jambo")
    assert ok is True and any(s[0] == "reask" for s in SENT), SENT
    print("case7 (stale state -> fresh re-ask): OK")

    conversation.clear_state(herder.phone_number)
    print("\nAll water-confirmation escape paths OK.")


if __name__ == "__main__":
    main()
