"""End-to-end conversation-flow test (mocks WhatsApp sends, real DB).

Walks a fresh phone number through:
  1. onboarding (name -> language -> animals -> count)
  2. weight measurement (uzito -> cattle -> adult -> girth)
  3. herd estimation (herd -> count -> 3 samples)
  4. pin validation against a real known water source
Cleans up all created rows afterwards.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PHONE = "254799000009"  # test-only number
SENT: list[str] = []


def fake_send_text(to: str, body: str) -> None:
    SENT.append(body)


def fake_buttons(to: str, body: str, buttons: list[tuple[str, str]]) -> None:
    SENT.append(f"[BUTTONS] {body} :: {[b[0] for b in buttons]}")


def main() -> int:
    from app.routers import whatsapp
    from app.services import conversation, pastoralists

    # Make the test idempotent: clear any stale rows from a previous run.
    pastoralists.delete_pastoralist(PHONE)
    conversation.clear_state(PHONE)

    whatsapp.whatsapp_client.send_text = fake_send_text
    whatsapp.whatsapp_client.send_quick_reply_buttons = fake_buttons
    whatsapp.whatsapp_client.send_image_bytes_url = lambda *a, **k: None
    whatsapp.whatsapp_client.send_image = lambda *a, **k: None

    # fresh pastoralist row (not onboarded)
    pastoralists.upsert_pastoralist(PHONE)
    p = pastoralists.get_pastoralist(PHONE)
    print("onboarded initially:", p.is_onboarded)

    # --- onboarding ---
    whatsapp._handle_text(PHONE, p, "hello")
    assert "Jina lako ni nani" in SENT[-1], SENT[-1]
    whatsapp._handle_text(PHONE, p, "Juma")
    assert "Asante, Juma!" in SENT[-1], SENT[-1]
    whatsapp._handle_text(PHONE, p, "swahili")
    assert "wanyama wa aina gani" in SENT[-1], SENT[-1]
    whatsapp._handle_text(PHONE, p, "ng'ombe")
    assert "wangapi" in SENT[-1], SENT[-1]
    whatsapp._handle_text(PHONE, p, "12")
    p = pastoralists.get_pastoralist(PHONE)
    print("onboarded after:", p.is_onboarded, "| name:", p.full_name, "| herd:", p.herd_composition)
    assert p.is_onboarded and p.full_name == "Juma"
    print("  onboarding OK")

    # --- weight flow ---
    whatsapp._handle_text(PHONE, p, "uzito")
    whatsapp._handle_text(PHONE, p, "weight:cattle")
    whatsapp._handle_text(PHONE, p, "age:adult")
    whatsapp._handle_text(PHONE, p, "165")
    last = SENT[-1]
    print("  weight reply:", last[:120].replace("\n", " | "))
    assert "kg" in last and "165" in last
    print("  single weight OK")

    # --- herd flow ---
    whatsapp._handle_text(PHONE, p, "herd")
    whatsapp._handle_text(PHONE, p, "12")
    whatsapp._handle_text(PHONE, p, "150 155 160")
    last = SENT[-1]
    print("  herd reply:", last[:150].replace("\n", " | "))
    assert "12" in last and "kundi" in last
    print("  herd OK")

    # --- pin validation (known water source location) ---
    from app.services import water_validation

    # 151f4aa6 is at 36.9915, 0.5854
    r = water_validation.validate_pin(36.9915, 0.5854)
    print(f"pin at known source: duplicate={r.is_duplicate} dist={r.distance_to_nearest_m:.0f}m")
    assert r.is_duplicate
    r2 = water_validation.validate_pin(36.0, 0.0)  # somewhere empty
    print(f"pin far away: duplicate={r2.is_duplicate} nearby={r2.has_nearby_source}")
    print("  pin validation OK")

    # --- cleanup ---
    pastoralists.delete_pastoralist(PHONE)
    conversation.clear_state(PHONE)
    print("\nAll conversation-flow checks passed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
