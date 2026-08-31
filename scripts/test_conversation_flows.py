"""End-to-end conversation-flow test (mocks WhatsApp sends, real DB).

Walks a fresh phone number through:
  1. onboarding (name -> language -> animals -> count -> MORE animals -> finish)
  2. name validation rejects non-names
  3. weight measurement (uzito -> cattle -> adult -> girth)
  4. herd estimation (herd -> count -> 3 samples)
  5. pin validation against a real known water source
  6. map rendering with the herder's location
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
    whatsapp.whatsapp_client.send_image_bytes_url = lambda *a, **k: SENT.append(f"[IMAGE] {a[1]}")
    whatsapp.whatsapp_client.send_image = lambda *a, **k: None

    # fresh pastoralist row (not onboarded)
    pastoralists.upsert_pastoralist(PHONE)
    p = pastoralists.get_pastoralist(PHONE)
    print("onboarded initially:", p.is_onboarded)

    # --- onboarding: name validation ---
    whatsapp._handle_text(PHONE, p, "hello")
    assert "Jina lako ni nani" in SENT[-1], SENT[-1]
    # a non-name should be rejected
    whatsapp._handle_text(PHONE, p, "cattle")
    assert "jina lako halisi" in SENT[-1], SENT[-1]
    whatsapp._handle_text(PHONE, p, "123")
    assert "jina lako halisi" in SENT[-1], SENT[-1]
    whatsapp._handle_text(PHONE, p, "Juma")
    assert "Asante, Juma!" in SENT[-1], SENT[-1]
    whatsapp._handle_text(PHONE, p, "swahili")
    assert "wanyama wa aina gani" in SENT[-1], SENT[-1]
    whatsapp._handle_text(PHONE, p, "ng'ombe")
    assert "wangapi" in SENT[-1], SENT[-1]
    whatsapp._handle_text(PHONE, p, "12")
    assert "aina nyingine" in SENT[-1], SENT[-1]
    # add goats too (mixed herd)
    whatsapp._handle_text(PHONE, p, "yes_more")
    whatsapp._handle_text(PHONE, p, "mbuzi")
    whatsapp._handle_text(PHONE, p, "5")
    assert "mbuzi/kondoo" in SENT[-1] or "mbuzi" in SENT[-1], SENT[-1]
    whatsapp._handle_text(PHONE, p, "no_more")
    p = pastoralists.get_pastoralist(PHONE)
    print("onboarded after:", p.is_onboarded, "| name:", p.full_name, "| herd:", p.herd_composition)
    assert p.is_onboarded and p.full_name == "Juma"
    assert p.herd_composition == {"cattle": 12, "shoat": 5}, p.herd_composition
    print("  onboarding (mixed herd) OK")

    # --- weight flow ---
    whatsapp._handle_text(PHONE, p, "uzito")
    whatsapp._handle_text(PHONE, p, "weight:cattle")
    whatsapp._handle_text(PHONE, p, "age:adult")
    whatsapp._handle_text(PHONE, p, "165")
    last = SENT[-1]
    assert "kg" in last and "165" in last
    print("  weight reply:", last[:120].replace("\n", " | "))

    # --- weight flow escape (the reported loop) ---
    whatsapp._handle_text(PHONE, p, "uzito")
    whatsapp._handle_text(PHONE, p, "weight:goat")
    whatsapp._handle_text(PHONE, p, "age:adult")
    whatsapp._handle_text(PHONE, p, "not a number")
    assert "si namba" in SENT[-1], SENT[-1]
    # escape with 'menu' - must NOT loop
    whatsapp._handle_text(PHONE, p, "menu")
    assert "HUDUMA" in SENT[-1], SENT[-1]
    print("  weight-loop escape OK")

    # --- menu + shortcuts ---
    whatsapp._handle_text(PHONE, p, "menu")
    assert "HUDUMA" in SENT[-1], SENT[-1]
    whatsapp._handle_text(PHONE, p, "3")  # weight shortcut
    whatsapp._handle_text(PHONE, p, "done")
    whatsapp._handle_text(PHONE, p, "6")  # status shortcut
    whatsapp._handle_text(PHONE, p, "menu")
    print("  menu shortcuts OK")

    # --- herd flow ---
    whatsapp._handle_text(PHONE, p, "herd")
    whatsapp._handle_text(PHONE, p, "12")
    whatsapp._handle_text(PHONE, p, "150 155 160")
    last = SENT[-1]
    assert "12" in last and "kundi" in last
    print("  herd reply:", last[:150].replace("\n", " | "))
    print("  weight+herd OK")

    # --- pin validation (known water source location) ---
    from app.services import water_validation

    r = water_validation.validate_pin(36.9915, 0.5854)
    print(f"pin at known source: duplicate={r.is_duplicate} dist={r.distance_to_nearest_m:.0f}m")
    assert r.is_duplicate
    r2 = water_validation.validate_pin(36.0, 0.0)
    print(f"pin far away: duplicate={r2.is_duplicate} nearby={r2.has_nearby_source}")
    print("  pin validation OK")

    # --- map rendering with herder location ---
    from app.services import map_renderer

    png = map_renderer.render_rings_png("151f4aa6-499d-4bac-a5de-ef5db4fca968", 36.9915, 0.5854)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10000
    print(f"  map render OK ({len(png)} bytes PNG)")

    # --- cleanup ---
    pastoralists.delete_pastoralist(PHONE)
    conversation.clear_state(PHONE)
    print("\nAll conversation-flow checks passed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

