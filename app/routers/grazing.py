"""
Grazing pins from the map page (POST /grazing/pin).

A herder taps the spot on /mapview instead of sending a WhatsApp location. The
page cannot know who he is — the link travels through clan groups — so the URL
carries a short-lived token minted when we sent it (migration 014, graze_tokens),
and this endpoint trades that token for the phone number.

The reply goes back over WhatsApp, not into the page: the phone is where he keeps
his record, and a page he closes would take the answer with it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.services import grazing_flow, pastoralists, whatsapp_client

log = logging.getLogger(__name__)
router = APIRouter(prefix="/grazing", tags=["grazing"])


@router.post("/pin")
async def grazing_pin(request: Request) -> dict:
    """Body: {lat, lon, token, note?}. Answers over WhatsApp; JSON for the page.

    Deliberately does NOT accept a phone number: the token is the only credential
    here, so a forwarded link can never be turned into an answer for someone else's
    number, and no phone number is ever written into a URL.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid JSON body")
    try:
        lat = float(payload["lat"])
        lon = float(payload["lon"])
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="lat and lon are required")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="lat/lon out of range")

    phone = grazing_flow.phone_for_token(str(payload.get("token") or ""))
    if not phone:
        # Expired or unknown token: the honest answer to the page is "reopen the
        # link from WhatsApp", not a silent failure.
        raise HTTPException(status_code=403,
                            detail="This map link has expired. Open it again from WhatsApp.")

    herder = None
    try:
        herder = pastoralists.get_pastoralist(phone)
    except Exception:  # noqa: BLE001
        log.exception("grazing pin: herder lookup failed")
    if herder is None:
        herder = pastoralists.upsert_pastoralist(phone)

    hint = str(payload.get("point_id") or "").strip() or None
    try:
        res = grazing_flow.handle_pin(phone, herder, lat, lon, source="map",
                                      point_id_hint=hint)
    except Exception:  # noqa: BLE001
        log.exception("grazing pin handling failed")
        raise HTTPException(status_code=500, detail="could not read that spot")

    # Text, always: the figures have to be re-readable, and a voice note that plays
    # once is the wrong shape for numbers a herder may act on tomorrow.
    try:
        if res.text:
            whatsapp_client.send_text(phone, res.text)
        if res.ask_manyatta:
            whatsapp_client.send_location_request(
                phone,
                {"swahili": "Niambie eneo la manyatta yako ili nipime umbali vizuri "
                            "kila siku — mara moja tu.",
                 "english": "Send your manyatta location so I can measure the walk "
                            "properly — once only."}[herder.preferred_language],
                grazing_flow.LOCATION_BUTTON[
                    "english" if herder.preferred_language == "english" else "swahili"])
        elif res.question:
            whatsapp_client.send_quick_reply_buttons(
                phone, res.question, list(res.buttons))
    except Exception:  # noqa: BLE001
        log.exception("grazing pin reply failed (non-fatal)")

    return {
        "ok": bool(res.ok),
        "reason": res.reason,
        "recorded": bool(res.recorded),
        "sent_whatsapp": bool(res.text),
        # The page shows this while he waits for the WhatsApp message. It must not
        # say "no satellite picture" either — the whole point of the reason codes.
        "confirmation": ("Nimepima eneo hilo. Jibu limekutumwa kwenye WhatsApp."
                         if res.ok else
                         "Tumeandikisha mlipopanda. Maelezo kwa nini hatukuweza "
                         "kupima malisho hapo yamekutumwa kwenye WhatsApp."),
    }
