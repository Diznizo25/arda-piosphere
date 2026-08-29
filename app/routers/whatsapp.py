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
from app.services import water_reach, water_sources, whatsapp_client
from app.services.advisory_service import get_advisory
from app.services.ground_truth import parse_ground_truth_intent, record_ground_truth
from app.services.pastoralists import (
    get_pastoralist,
    upsert_pastoralist,
    update_last_location,
    get_last_location,
    delete_pastoralist,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])

SPECIES_KEYWORDS = {
    "cattle": ["cattle", "cow", "ng'ombe", "ngombe", "loon"],
    "shoat": ["shoat", "sheep", "goat", "kondoo", "mbuzi", "hoolaa"],
    "camel": ["camel", "ngamia", "gaala"],
}

LANGUAGE_KEYWORDS = {
    "swahili": ["swahili", "kiswahili"],
    "english": ["english", "kiingereza", "ingereza"],
}

MAP_KEYWORDS = ["map", "ramani", "picha", "diagram", "chati"]

PIN_KEYWORDS = ["pin", "register", "ongeza", "andika", "new water", "regist"]

ASK_SPECIES_TEXT = {
    "swahili": "Kabla sijakupa jibu, niambie wanyama wako ni gani: ng'ombe, kondoo/mbuzi, au ngamia?",
    "english": "Before I answer, tell me your animals: cattle, sheep/goats, or camels?",
}

NEW_WATER_POINT_OFFER = {
    "swahili": "Eneo hili haliko kwenye mfumo wetu bado. Tuma 'PIN' niweke kama chanzo kipya cha maji, "
               "na tutaanza kupima malisho yake.",
    "english": "This location is not in our system yet. Reply 'PIN' to register it as a new water point, "
               "and we will start measuring its pasture.",
}

WATER_ADDED_MSG = {
    "swahili": "Asante! Tumeongeza eneo hili kama chanzo kipya cha maji. Data ya malisho itaonekana "
               "baada ya kukamilika kwa hesabu ya satelaiti (inachukua dakika kadhaa).",
    "english": "Thank you! We have registered this as a new water point. Pasture data will appear once "
               "the satellite calculation is done (takes a few minutes).",
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
            [("cattle", "Ng'ombe"), ("shoat", "Mbuzi/Kondoo"), ("camel", "Ngamia")],
        )
        return

    req = AdvisoryRequest(lat=lat, lon=lon, species=pastoralist.primary_species,
                           language=pastoralist.preferred_language)
    result = get_advisory(req)

    if result.found:
        whatsapp_client.send_text(phone, result.message)
        return

    # No known water point reaches this location -> offer to register a new one.
    whatsapp_client.send_text(phone, NEW_WATER_POINT_OFFER[pastoralist.preferred_language])


def _handle_text(phone: str, pastoralist, text: str) -> None:
    text_lower = text.strip().lower()

    # Data-deletion request (see the privacy policy + /data-deletion page).
    if text_lower == "delete" or text_lower == "futa":
        delete_pastoralist(phone)
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Taarifa zako zimefutwa. Kwa usaidizi, tutumie ujumbe.",
                "english": "Your data has been deleted. Message us if you need help.",
            }[pastoralist.preferred_language],
        )
        return

    for species, keywords in SPECIES_KEYWORDS.items():
        if any(k in text_lower for k in keywords):
            upsert_pastoralist(phone, species=species)
            confirm = {
                "swahili": "Sawa, nimeandika wanyama wako. Tuma tena eneo lako (location) upate jibu.",
                "english": "Okay, I have noted your animals. Send your location again for an answer.",
            }[pastoralist.preferred_language]
            whatsapp_client.send_text(phone, confirm)
            return

    for language, keywords in LANGUAGE_KEYWORDS.items():
        if any(k in text_lower for k in keywords):
            upsert_pastoralist(phone, language=language)
            whatsapp_client.send_text(
                phone,
                "Sawa, nitatumia Kiswahili." if language == "swahili" else "Okay, I will reply in English.",
            )
            return

    # Map request: send the rings for the nearest reachable water point.
    if any(k in text_lower for k in MAP_KEYWORDS):
        _handle_map_request(phone, pastoralist)
        return

    # Pin request: register a new water point at the herder's last shared location.
    if any(k in text_lower for k in PIN_KEYWORDS):
        _handle_pin_request(phone, pastoralist)
        return

    gt_intent = parse_ground_truth_intent(text_lower)
    if gt_intent is not None:
        record_ground_truth(pastoralist, gt_intent, text)
        thanks = {
            "swahili": "Asante kwa taarifa! Itatusaidia kuboresha maelezo ya eneo hilo.",
            "english": "Thank you for the report! It will help us improve information for that area.",
        }[pastoralist.preferred_language]
        whatsapp_client.send_text(phone, thanks)
        return

    whatsapp_client.send_text(
        phone,
        {
            "swahili": "Tuma eneo lako (location) ili nikupe taarifa za maji na malisho karibu nawe.",
            "english": "Send your location so I can give you water and pasture information near you.",
        }[pastoralist.preferred_language],
    )


def _handle_map_request(phone: str, pastoralist) -> None:
    """Send a map of the species rings for the nearest reachable water point."""
    settings = get_settings()
    if not settings.app_public_base_url:
        log.warning("APP_PUBLIC_BASE_URL not set — cannot build map image link")
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Samahani, ramani haipatikani kwa sasa.",
                "english": "Sorry, the map is not available right now.",
            }[pastoralist.preferred_language],
        )
        return

    loc = get_last_location(phone)
    if loc is None:
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Kwanza tuma eneo lako (location) ili nikutumie ramani ya maeneo.",
                "english": "First send your location so I can send you the area map.",
            }[pastoralist.preferred_language],
        )
        return
    if not pastoralist.primary_species:
        whatsapp_client.send_text(phone, ASK_SPECIES_TEXT[pastoralist.preferred_language])
        return

    lon, lat = loc
    candidates = water_reach.find_nearest_reachable_water(lon, lat, pastoralist.primary_species, limit=1)
    if not candidates:
        whatsapp_client.send_text(phone, NEW_WATER_POINT_OFFER[pastoralist.preferred_language])
        return

    water_source_id = candidates[0].water_source_id
    url = f"{settings.app_public_base_url.rstrip('/')}/map/{water_source_id}.png"
    whatsapp_client.send_image_bytes_url(
        phone,
        url,
        caption={
            "swahili": "Ramani ya duara za wanyama wako karibu na chanzo hiki cha maji.",
            "english": "Map of your animals' rings around this water source.",
        }[pastoralist.preferred_language],
    )


def _handle_pin_request(phone: str, pastoralist) -> None:
    """Register the herder's last shared location as a new water point."""
    loc = get_last_location(phone)
    if loc is None:
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Tuma eneo lako (location) kwanza, kisha tuma 'PIN' kuliandikisha.",
                "english": "Send your location first, then send 'PIN' to register it.",
            }[pastoralist.preferred_language],
        )
        return
    lon, lat = loc
    try:
        water_sources.create_water_source(lon=lon, lat=lat, source_type="ground_truth",
                                          confidence=0.5)
        whatsapp_client.send_text(phone, WATER_ADDED_MSG[pastoralist.preferred_language])
    except Exception as e:  # noqa: BLE001
        log.exception(f"Failed to register water point for {phone}")
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Samahani, hatukuweza kuongeza eneo hili. Jaribu tena baadaye.",
                "english": "Sorry, we could not register this area. Please try again later.",
            }[pastoralist.preferred_language],
        )
