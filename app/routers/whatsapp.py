"""
WhatsApp Business Cloud API webhook. Handles the two message types the core
loop needs: a location share (triggers the advisory) and a text reply used
either to set the herder's species/language or as ground-truth feedback
(see app/services/ground_truth.py for the latter, wired in below).

No GEE calls happen here — this only reads precomputed COGs via
app.services.advisory_service (architecture principle #1).
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, Request, Response, HTTPException

from app.config import get_settings
from app.models.schemas import AdvisoryRequest
from app.services import whatsapp_client
from app.services.advisory_service import get_advisory
from app.services.ground_truth import parse_ground_truth_intent, record_ground_truth
from app.services.pastoralists import get_pastoralist, upsert_pastoralist, update_last_location

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])

SPECIES_KEYWORDS = {
    "cattle": ["cattle", "cow", "ng'ombe", "ngombe", "loon"],
    "shoat": ["shoat", "sheep", "goat", "kondoo", "mbuzi", "hoolaa"],
    "camel": ["camel", "ngamia", "gaala"],
}

LANGUAGE_KEYWORDS = {
    "swahili": ["swahili", "kiswahili"],
    "borana": ["borana", "afaan borana", "oromo"],
}

ASK_SPECIES_TEXT = {
    "swahili": "Kabla sijakupa jibu, niambie wanyama wako ni gani: ng'ombe, kondoo/mbuzi, au ngamia?",
    "borana": "Utuma bishaan/margaa dura, horiin keessan maal akka ta'e naaf himaa: loon, hoolaa/re'ee, moo gaala?",
}


@router.get("")
def verify_webhook(request: Request):
    """Meta's webhook verification handshake (hub.mode / hub.verify_token / hub.challenge)."""
    settings = get_settings()
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


def _verify_signature(body: bytes, signature_header: str | None) -> bool:
    settings = get_settings()
    if not settings.whatsapp_app_secret:
        log.warning("WHATSAPP_APP_SECRET not set — skipping signature verification (dev only!)")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(settings.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    provided = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)


@router.post("")
async def receive_webhook(request: Request):
    raw_body = await request.body()
    if not _verify_signature(raw_body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                _handle_message(message)

    return {"status": "ok"}


def _handle_message(message: dict) -> None:
    phone = message["from"]
    msg_type = message.get("type")

    pastoralist = get_pastoralist(phone) or upsert_pastoralist(phone)

    if msg_type == "location":
        _handle_location(phone, pastoralist, message["location"])
    elif msg_type == "text":
        _handle_text(phone, pastoralist, message["text"]["body"])
    elif msg_type == "interactive":
        reply = message["interactive"].get("button_reply", {})
        _handle_text(phone, pastoralist, reply.get("id", ""))
    else:
        log.info(f"Ignoring unsupported message type from {phone}: {msg_type}")


def _handle_location(phone: str, pastoralist, location: dict) -> None:
    lat, lon = location["latitude"], location["longitude"]
    update_last_location(phone, lon, lat)

    if not pastoralist.primary_species:
        whatsapp_client.send_quick_reply_buttons(
            phone,
            ASK_SPECIES_TEXT[pastoralist.preferred_language],
            [("cattle", "Ng'ombe/Loon"), ("shoat", "Mbuzi/Hoolaa"), ("camel", "Ngamia/Gaala")],
        )
        return

    req = AdvisoryRequest(lat=lat, lon=lon, species=pastoralist.primary_species,
                           language=pastoralist.preferred_language)
    result = get_advisory(req)
    whatsapp_client.send_text(phone, result.message)


def _handle_text(phone: str, pastoralist, text: str) -> None:
    text_lower = text.strip().lower()

    for species, keywords in SPECIES_KEYWORDS.items():
        if any(k in text_lower for k in keywords):
            upsert_pastoralist(phone, species=species)
            confirm = {
                "swahili": f"Sawa, nimeandika wanyama wako. Tuma tena eneo lako (location) upate jibu.",
                "borana": f"Tole, horii keessan galmeesse. Bakka jirtan ergaa deebii argachuuf.",
            }[pastoralist.preferred_language]
            whatsapp_client.send_text(phone, confirm)
            return

    for language, keywords in LANGUAGE_KEYWORDS.items():
        if any(k in text_lower for k in keywords):
            upsert_pastoralist(phone, language=language)
            whatsapp_client.send_text(
                phone,
                "Sawa, nitatumia Kiswahili." if language == "swahili" else "Tole, Afaan Boranaan isiniif deebisa.",
            )
            return

    gt_intent = parse_ground_truth_intent(text_lower)
    if gt_intent is not None:
        record_ground_truth(pastoralist, gt_intent, text)
        thanks = {
            "swahili": "Asante kwa taarifa! Itatusaidia kuboresha maelezo ya eneo hilo.",
            "borana": "Galatoomaa odeeffannoo kanaaf! Kun bakka sana fooyyessuuf nu gargaara.",
        }[pastoralist.preferred_language]
        whatsapp_client.send_text(phone, thanks)
        return

    whatsapp_client.send_text(
        phone,
        {
            "swahili": "Tuma eneo lako (location) ili nikupe taarifa za maji na malisho karibu nawe.",
            "borana": "Bakka jirtan (location) naaf ergaa akkan odeeffannoo bishaanii fi margaa isiniif kennuuf.",
        }[pastoralist.preferred_language],
    )
