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
import sys
import threading
import time
from collections import deque

from fastapi import APIRouter, Request, Response, HTTPException

from app.config import get_settings
from app.models.schemas import AdvisoryRequest
from app.services import (
    ai,
    build_tracker,
    conversation,
    map_renderer,
    registration,
    speech,
    water_reach,
    water_sources,
    water_validation,
    weight as weight_service,
    whatsapp_client,
)
from app.services.advisory_service import get_advisory
from app.services.ground_truth import record_ground_truth
from app.services.pastoralists import (
    get_pastoralist,
    upsert_pastoralist,
    update_last_location,
    get_last_location,
    set_water_source,
    get_water_source,
    delete_pastoralist,
    set_voice_replies,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])

# --- webhook delivery robustness ---------------------------------------------
# Meta redelivers webhooks when our response is slow or a delivery is retried.
# A herder must NEVER receive duplicates, so we (a) ACK Meta immediately and
# process asynchronously in a background thread, and (b) de-duplicate by the
# WhatsApp message id for a short window.
_PROCESSED: deque[tuple[float, str]] = deque(maxlen=600)
_PROCESSED_LOCK = threading.Lock()
_DEDUP_WINDOW_S = 600
_PHONE_LOCKS: dict[str, threading.Lock] = {}
_PHONE_LOCKS_GUARD = threading.Lock()


def _already_processed(msg_id: str) -> bool:
    """True if this exact message id was seen within the dedup window."""
    now = time.time()
    with _PROCESSED_LOCK:
        while _PROCESSED and now - _PROCESSED[0][0] > _DEDUP_WINDOW_S:
            _PROCESSED.popleft()
        for ts, mid in _PROCESSED:
            if mid == msg_id:
                return True
        _PROCESSED.append((now, msg_id))
        return False


def _phone_lock(phone: str) -> threading.Lock:
    with _PHONE_LOCKS_GUARD:
        lock = _PHONE_LOCKS.get(phone)
        if lock is None:
            lock = threading.Lock()
            _PHONE_LOCKS[phone] = lock
        return lock


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

WATER_KEYWORDS = ["maji", "water", "chanzo", "source"]

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

VOICE_ERR_MSG = {
    "swahili": "Samahani, sikuelewa ujumbe wako wa sauti. Jaribu kuandika ujumbe au tuma eneo lako (location).",
    "english": "Sorry, I could not understand your voice note. Please type a message or share your location.",
}

ASK_CONFIRM_WATER = {
    "swahili": "💧 {name}, bado hujathibitisha chanzo chako cha maji. Ili nipe taarifa sahihi, "
               "chagua chanzo ambacho wanyama wako wanakunywa kutoka (namba 1-{n}):\n\n{list}\n\n"
               "★ Kama hakipo, tuma 'hakuna' nikusaidie kuliandikisha kipya.",
    "english": "💧 {name}, you haven't confirmed your water point yet. So I give you the right "
               "info, choose the water point your animals drink from (numbers 1-{n}):\n\n{list}\n\n"
               "★ If it's not there, send 'none' and I'll help you register it new.",
}

ASK_CONFIRM_WATER_RETRY = {
    "swahili": "Chagua chanzo kwa:\n"
               "• Namba ya chanzo (k.m. '2')\n"
               "• 'orodha' kuona orodha + ramani tena\n"
               "• 'hakuna' kama chanzo chako hakipo kwenye orodha\n"
               "• 'menu' kwa huduma nyingine / 'cancel' kuacha",
    "english": "Choose your water point by:\n"
               "• sending its number (e.g. '2')\n"
               "• sending 'list' to see the list + map again\n"
               "• sending 'none' if your water point isn't in the list\n"
               "• sending 'menu' for other services / 'cancel' to stop",
}

WATER_CONFIRMED = {
    "swahili": "Sawa! Nimekumbuka chanzo chako cha maji: {name}. 🎯\n"
               "Sasa nitakupa ramani ya malisho na duara za wanyama wako.",
    "english": "Got it! I've remembered your water point: {name}. 🎯\n"
               "Now I'll show you the pasture map with your animals' rings.",
}

WATER_CONFIRM_SKIP = {
    "swahili": "Sawa. Kama chanzo chako hakipo kwenye orodha, unaweza kukisajili sasa:\n"
               "1. Tuma eneo lako (location) pale wanyama wako wanakunywa.\n"
               "2. Tuma 'PIN' nikuulize aina yake na jina lake.\n\n"
               "Au tuma 'menu' kuona huduma nyingine.",
    "english": "Okay. If your water point isn't in the list, you can register it now:\n"
               "1. Share your location where your animals drink.\n"
               "2. Send 'PIN' and I'll ask its type and name.\n\n"
               "Or send 'menu' to see other services.",
}

SOURCE_TYPE_LABEL = {
    "satellite_gsw": "Maji (GSW)",
    "osm": "Maji (OSM)",
    "wpdx": "Maji (WPDx)",
    "ilri": "Maji (ILRI)",
    "ground_truth": "Chanzo kilichothibitishwa",
}

_PINNED_TYPE_SWA = {"borehole": "kisima (borehole)", "well": "kisima cha kuchimba",
                    "river": "mto", "spring": "chemchemi", "dam": "bwawa (lami)",
                    "pan": "bwawa", "tap": "mfereji", "lake": "ziwa"}
_EN_TYPE_SWA = {"borehole": "borehole", "well": "well", "river": "river",
                "spring": "spring", "dam": "dam", "pan": "water pan",
                "tap": "tap", "lake": "lake"}


def _water_type_swa(nearby: dict, lang: str = "swahili") -> str:
    """Human type label for a nearby water option (kisima/mto/...) so points
    with the same ward are still distinguishable."""
    t = nearby.get("water_type")
    if t:
        if lang == "swahili":
            return _PINNED_TYPE_SWA.get(t, t)
        return _EN_TYPE_SWA.get(t, t)
    return "maji" if lang == "swahili" else "water"


def _source_label(nearby: dict, lang: str = "swahili") -> str:
    """Human label for a nearby water option: the LOCAL NAME when we have one
    (that's how a pastoralist identifies a water point), else a landmark-based
    description like 'Kisima karibu na Burat', else the ward."""
    if nearby.get("name"):
        return nearby["name"]
    try:
        from app.services.map_renderer import _water_label

        lbl = _water_label(nearby, "swa" if lang == "swahili" else "eng")
        if "karibu na" in lbl or " near " in lbl:
            return lbl
    except Exception:  # noqa: BLE001
        pass
    if nearby.get("ward"):
        return nearby["ward"]
    return "Maji" if lang == "swahili" else "Water"

VOICE_TOO_LONG_MSG = {
    "swahili": "Ujumbe wa sauti ni mrefu sana. Tuma ujumbe mfupi (chini ya dakika moja) au andika ujumbe.",
    "english": "That voice note is too long. Send a shorter one (under a minute) or type your message.",
}

VOICE_ON_MSG = {
    "swahili": "Sawa! Nitakujibu kwa sauti sasa.",
    "english": "Okay! I will reply by voice from now on.",
}

VOICE_OFF_MSG = {
    "swahili": "Sawa, nitarudi kujibu kwa maandishi.",
    "english": "Okay, I will go back to text replies.",
}

VOICE_KEYWORDS = ["sauti", "voice", "speak", "spika", "sikiliza"]
TEXT_KEYWORDS = ["maandishi", "text", "maandiko"]

STATUS_KEYWORDS = ["status", "hali", "progress", "maendeleo", "uko wapi"]

WEIGHT_KEYWORDS = ["weight", "uzito", "pima", "measure"]
MENU_KEYWORDS = ["menu", "huduma", "services", "msaada", "help", "home",
                 "chagua", "options", "vitendo", "orodha", "help"]
HERD_KEYWORDS = ["herd", "kundi", "wingi", "zote"]
AGE_KEYWORDS = {
    "adult": ["adult", "mzima", "wazima", "kubwa", "big"],
    "young": ["young", "mdogo", "ndogo", "kidogo", "small", "calf", "kondoo mdogo"],
}
DONE_KEYWORDS = ["done", "stop", "isha", "kumaliza", "finish", "maliza"]
ANOTHER_KEYWORDS = ["another", "nyingine", "tena", "more", "zaidi"]

WEIGHT_MSG = {
    "swahili": "PIMA UZITO WA MNYAMA 🐄\n\nChagua aina ya mnyama:",
    "english": "MEASURE ANIMAL WEIGHT 🐄\n\nChoose the type of animal:",
}

MENU_MSG = {
    "swahili": "🌿 ARDA LINK — HUDUMA ZETU\n\n"
               "1. 📍 ENEO (location) — taarifa za maji na malisho karibu nawe\n"
               "2. 🏷 PIN — andikisha chanzo chako cha maji kipya\n"
               "3. ⚖️ UZITO — pima uzito wa mnyama (mkanda wa kifua)\n"
               "4. 🐄 HERD — kadiria uzito wa kundi zima\n"
               "5. 🗺 MAP — ramani ya maeneo ya malisho\n"
               "6. 📊 STATUS — hali ya ujenzi wa chanzo chako\n"
               "7. 🗣 SAUTI — jibu kwa sauti / MAANDISHI kwa maandishi\n"
               "8. 🌍 SWAHILI / ENGLISH — badilisha lugha\n\n"
               "Tuma neno linalofaa (k.m. 'uzito') au namba ya huduma.",
    "english": "🌿 ARDA LINK — OUR SERVICES\n\n"
               "1. 📍 LOCATION — water & pasture info near you\n"
               "2. 🏷 PIN — register your new water point\n"
               "3. ⚖️ WEIGHT — measure an animal's weight (heart-girth tape)\n"
               "4. 🐄 HERD — estimate the weight of a whole herd\n"
               "5. 🗺 MAP — map of the grazing zones\n"
               "6. 📊 STATUS — your water point build progress\n"
               "7. 🗣 VOICE — reply by voice / TEXT for text\n"
               "8. 🌍 SWAHILI / ENGLISH — change language\n\n"
               "Send the matching word (e.g. 'weight') or the number.",
}

MENU_NUMBERS = {
    "1": "location", "2": "pin", "3": "weight", "4": "herd",
    "5": "map", "6": "status", "7": "voice", "8": "language",
}

WEIGHT_ANIMAL_BUTTONS = [
    ("weight:cattle", "Ng'ombe"),
    ("weight:goat", "Mbuzi"),
    ("weight:sheep", "Kondoo"),
    ("weight:camel", "Ngamia"),
]

WEIGHT_AGE_BUTTONS = [
    ("age:adult", "Mzima"),
    ("age:young", "Mdogo"),
]

ASK_AGE = {
    "swahili": "Mnyama ni mzima au mdogo?",
    "english": "Is the animal adult or young?",
}

ASK_GIRTH = {
    "swahili": "Sawa! Sasa pima kifua kwa mkanda.\n\n{guide}\n\nTuma namba ya sentimita (k.m. '165').",
    "english": "Okay! Now measure the chest with the tape.\n\n{guide}\n\nSend the number in centimeters (e.g. '165').",
}

GIRTH_INVALID = {
    "swahili": "Namba hiyo haiwezekani ({error}). Angalia kipimo na ujaribu tena (k.m. '165'), "
               "au tuma 'menu' kuona huduma zingine.",
    "english": "That measurement is not possible ({error}). Check the tape and try again (e.g. '165'), "
               "or send 'menu' to see other services.",
}

WEIGHT_RESULT = {
    "swahili": "Mnyama wako ana uzito wa takriban {weight} kg (kifua {girth} cm).\n\n{medication}\n\nPima mwingine? Tuma 'uzito'. Ukipenda kupima kundi zima, tuma 'herd'.",
    "english": "Your animal weighs approximately {weight} kg (heart girth {girth} cm).\n\n{medication}\n\nMeasure another? Send 'weight'. To estimate a whole herd, send 'herd'.",
}

ASK_HERD_COUNT = {
    "swahili": "Kundi lako lina wanyama wangapi wa aina hii? (tuma namba, k.m. '15')",
    "english": "How many animals of this type are in your herd? (send a number, e.g. '15')",
}

ASK_SAMPLE = {
    "swahili": "Sasa pima {sample} wanyama na utume vipimo vyao kwa sentimita.\n"
               "Tuma namba zote kwa ujumbe mmoja ukiachana na nafasi (k.m. '150 155 160')\n"
               "au utume moja kwa moja — nitaendelea kuzichukua hadi utakaposema 'done'.",
    "english": "Now measure {sample} animals and send their girths in cm.\n"
              "Send all numbers in one message separated by spaces (e.g. '150 155 160')\n"
              "or send them one at a time — I'll keep collecting until you say 'done'.",
}

SAMPLE_PARTIAL = {
    "swahili": "Nimepata vipimo {count}. Nimehesabu wanyama wachache zaidi? Tuma namba, au 'done' kumaliza.",
    "english": "Got {count} measurements so far. Measure a few more? Send a number, or 'done' to finish.",
}

HERD_RESULT = {
    "swahili": "Makadirio ya kundi lako la {species} ({count} wanyama, sampuli {sample}):\n"
               "• Wastani kwa mnyama: ~{mean} kg\n"
               "• Jumla: ~{total} kg\n"
               "• Kiwango cha uhakika: {low} - {high} kg\n\n"
               "{medication}\n\nPima kundi lingine? Tuma 'uzito'. Tuma 'done' kumaliza.",
    "english": "Estimate for your {species} herd ({count} animals, sample {sample}):\n"
              "• Average per animal: ~{mean} kg\n"
              "• Total: ~{total} kg\n"
              "• Confidence range: {low} - {high} kg\n\n"
              "{medication}\n\nMeasure another herd? Send 'weight'. Send 'done' to finish.",
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
                msg_id = message.get("id") or ""
                # De-duplicate Meta redeliveries of the same message.
                if msg_id and _already_processed(msg_id):
                    continue
                # ACK Meta immediately; process asynchronously so a slow reply
                # (COG read, map render) never triggers a Meta retry = duplicate.
                threading.Thread(
                    target=_handle_message_guarded,
                    args=(message,),
                    daemon=True,
                ).start()

    return {"status": "ok"}


def _handle_message_guarded(message: dict) -> None:
    """Run one message, serialised per phone + crash-isolated. Never raises.

    A crash must NEVER leave the herder silently stuck: we log it AND send a
    plain "try again" reply (any message with its own reply semantics below can
    override it by handling itself)."""
    phone = message.get("from") or ""
    with _phone_lock(phone):
        try:
            _handle_message(message)
        except Exception:  # noqa: BLE001
            log.exception("webhook handler crashed for a message")
            try:
                from app.services import query_log

                query_log.log_query(
                    kind="other",
                    phone=phone,
                    result="error",
                    detail={"event": "webhook_crash",
                            "type": message.get("type"),
                            "error": repr(sys.exc_info()[1])[:200]},
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                # Fail-open fallback so the herder knows the message arrived and
                # can simply retry (never a black hole).
                lang = None
                try:
                    from app.services.pastoralists import get_pastoralist

                    p = get_pastoralist(phone)
                    lang = p.preferred_language if p else None
                except Exception:  # noqa: BLE001
                    lang = None
                whatsapp_client.send_text(
                    phone,
                    {
                        "swahili": "Pole, kulikuwa na tatizo kidogo. Tuma ujumbe wako "
                                   "tena (k.m. eneo lako au 'menu').",
                        "english": "Sorry, something went wrong for a moment. Please "
                                   "send your message again (your location or 'menu').",
                    }.get(lang, "Sorry, something went wrong. Please try again."),
                )
            except Exception:  # noqa: BLE001
                log.exception("failed to send crash fallback reply")


def _log_inbound(message: dict) -> None:
    """Record every inbound message in query_log so the ops dashboard shows live
    WhatsApp traffic (and we can diagnose 'not replying' instantly). Fail-open."""
    try:
        from app.services import query_log

        msg_type = message.get("type")
        snippet = ""
        if msg_type == "text":
            snippet = (message.get("text") or {}).get("body", "")[:80]
        elif msg_type == "location":
            snippet = "location"
        elif msg_type == "interactive":
            snippet = "interactive"
        elif msg_type == "audio":
            snippet = "audio"
        query_log.log_query(
            kind="other",
            phone=message.get("from"),
            result="ok",
            detail={"event": "inbound", "type": msg_type, "text": snippet},
        )
    except Exception:  # noqa: BLE001
        pass


def _handle_message(message: dict) -> None:
    phone = message["from"]
    msg_type = message.get("type")
    _log_inbound(message)

    pastoralist = get_pastoralist(phone) or upsert_pastoralist(phone)

    if msg_type == "location":
        # Locations always update the herder's last known location, but if a
        # guided flow is active (onboarding/pin), let the flow own the reply.
        location = message["location"]
        update_last_location(phone, location["longitude"], location["latitude"])
        state, _ = conversation.get_state(phone)
        if state and state.startswith("pin."):
            _handle_active_flow(phone, pastoralist, None)
        elif state and state.startswith("onboarding."):
            if state == "onboarding.water":
                # A new location means the nearby list is stale: re-ask with a
                # FRESH list based on where they are now.
                _ask_confirm_water(phone, pastoralist)
            else:
                _handle_active_flow(phone, pastoralist, None)
        else:
            _handle_location(phone, pastoralist, location)
    elif msg_type == "text":
        _handle_text(phone, pastoralist, message["text"]["body"])
    elif msg_type == "interactive":
        inter = message["interactive"]
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        _handle_text(phone, pastoralist, reply.get("id", ""))
    elif msg_type == "audio":
        _handle_audio(phone, pastoralist, message.get("audio", {}))
    else:
        log.info(f"Ignoring unsupported message type from {phone}: {msg_type}")


def _send_reply(phone: str, pastoralist, text: str, voice: bool = False) -> None:
    """Send a text reply, or a synthesized voice note when `voice` is requested.

    Fail-open: if TTS or the media upload ever fails, the reply goes out as
    plain text so the herder always gets an answer.
    """
    if not voice or not text:
        whatsapp_client.send_text(phone, text)
        return
    audio = speech.synthesize_speech(text, pastoralist.preferred_language)
    media_id = whatsapp_client.upload_media(audio) if audio else None
    if media_id is None:
        log.warning("Voice reply unavailable - falling back to text for %s", phone)
        whatsapp_client.send_text(phone, text)
        return
    whatsapp_client.send_audio(phone, media_id)


def _handle_audio(phone: str, pastoralist, audio: dict) -> None:
    """Transcribe a WhatsApp voice note and process the result as text.

    Runs in a background thread so the webhook returns 200 immediately (Meta
    retries slow webhooks); the reply is sent via the Graph API when done.
    """
    threading.Thread(
        target=_process_voice_note, args=(phone, pastoralist, audio), daemon=True
    ).start()


def _process_voice_note(phone: str, pastoralist, audio: dict) -> None:
    media_id = (audio or {}).get("id")
    if not media_id:
        log.warning(f"Voice note from {phone} without a media id")
        return
    audio_bytes = whatsapp_client.download_media(media_id)
    if not audio_bytes:
        whatsapp_client.send_text(phone, VOICE_ERR_MSG[pastoralist.preferred_language])
        return

    transcription = speech.transcribe_voice_note(
        audio_bytes, pastoralist.preferred_language
    )
    if transcription is None:
        whatsapp_client.send_text(phone, VOICE_ERR_MSG[pastoralist.preferred_language])
        return
    if transcription.duration_s > speech.MAX_DURATION_S:
        whatsapp_client.send_text(phone, VOICE_TOO_LONG_MSG[pastoralist.preferred_language])
        return
    if not transcription.text:
        whatsapp_client.send_text(phone, VOICE_ERR_MSG[pastoralist.preferred_language])
        return

    log.info(f"Voice note from {phone} [{transcription.language}] conf={transcription.confidence:.2f}: "
             f"{transcription.text!r}")
    # A voice note gets a voice reply (conversational symmetry).
    _handle_text(phone, pastoralist, transcription.text, voice=True)


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

    # A herder who hasn't confirmed their water point yet should confirm it FIRST:
    # an advisory only makes sense once we know which water point is THEIRS
    # (otherwise we'd describe a random nearby source as if it were their water).
    if pastoralist.is_onboarded and not pastoralist.water_source_id:
        _ask_confirm_water(phone, pastoralist)
        return

    req = AdvisoryRequest(lat=lat, lon=lon, species=pastoralist.primary_species,
                           language=pastoralist.preferred_language)
    result = get_advisory(req)

    if result.found:
        _send_reply(phone, pastoralist, result.message, voice=pastoralist.voice_replies)
        return

    # No known water point reaches this location -> offer to register a new one.
    _send_reply(phone, pastoralist, NEW_WATER_POINT_OFFER[pastoralist.preferred_language],
                voice=pastoralist.voice_replies)


def _handle_text(phone: str, pastoralist, text: str, voice: bool = False) -> None:
    text_lower = text.strip().lower()

    # Data-deletion request (see the privacy policy + /data-deletion page).
    if text_lower == "delete" or text_lower == "futa":
        delete_pastoralist(phone)
        conversation.clear_state(phone)
        _send_reply(
            phone,
            pastoralist,
            {
                "swahili": "Taarifa zako zimefutwa. Kwa usaidizi, tutumie ujumbe.",
                "english": "Your data has been deleted. Message us if you need help.",
            }[pastoralist.preferred_language],
            voice=voice,
        )
        return

    # An in-progress guided flow (onboarding / weight / pin) owns the turn.
    if _handle_active_flow(phone, pastoralist, text):
        return

    # Brand-new (or never-onboarded) users are guided through registration first.
    if not pastoralist.is_onboarded:
        _start_onboarding(phone, pastoralist, text, voice=voice)
        return

    # Voice-reply preference toggle.
    if any(k in text_lower for k in VOICE_KEYWORDS):
        set_voice_replies(phone, True)
        pastoralist.voice_replies = True
        _send_reply(phone, pastoralist, VOICE_ON_MSG[pastoralist.preferred_language], voice=True)
        return
    if any(k in text_lower for k in TEXT_KEYWORDS):
        set_voice_replies(phone, False)
        pastoralist.voice_replies = False
        _send_reply(phone, pastoralist, VOICE_OFF_MSG[pastoralist.preferred_language], voice=False)
        return

    for species, keywords in SPECIES_KEYWORDS.items():
        if any(k in text_lower for k in keywords):
            upsert_pastoralist(phone, species=species)
            confirm = {
                "swahili": "Sawa, nimeandika wanyama wako. Tuma tena eneo lako (location) upate jibu.",
                "english": "Okay, I have noted your animals. Send your location again for an answer.",
            }[pastoralist.preferred_language]
            _send_reply(phone, pastoralist, confirm, voice=voice)
            return

    for language, keywords in LANGUAGE_KEYWORDS.items():
        if any(k in text_lower for k in keywords):
            upsert_pastoralist(phone, language=language)
            _send_reply(
                phone,
                pastoralist,
                "Sawa, nitatumia Kiswahili." if language == "swahili" else "Okay, I will reply in English.",
                voice=voice,
            )
            return

    # Water-point confirmation / info (remembered water source).
    if any(k in text_lower for k in WATER_KEYWORDS):
        _handle_water_request(phone, pastoralist)
        return

    # Map request: send the rings for the nearest reachable water point.
    if any(k in text_lower for k in MAP_KEYWORDS):
        _handle_map_request(phone, pastoralist)
        return

    # Build-status request: current progress of the herder's pinned point.
    if any(k in text_lower for k in STATUS_KEYWORDS):
        _handle_status_request(phone, pastoralist)
        return

    # Pin request: register a new water point at the herder's last shared location.
    if any(k in text_lower for k in PIN_KEYWORDS):
        _handle_pin_request(phone, pastoralist)
        return

    # Weight request: start the heart-girth weight flow.
    if any(k in text_lower for k in WEIGHT_KEYWORDS):
        _start_weight_flow(phone, pastoralist)
        return

    # Menu / help request: show all services.
    if any(k in text_lower for k in MENU_KEYWORDS):
        _show_menu(phone, pastoralist)
        return

    # Menu number shortcuts (1-8).
    if text_lower in MENU_NUMBERS:
        service = MENU_NUMBERS[text_lower]
        if service == "location":
            whatsapp_client.send_text(
                phone,
                {
                    "swahili": "Tuma eneo lako (location) kwenye WhatsApp.",
                    "english": "Share your location on WhatsApp.",
                }[pastoralist.preferred_language],
            )
        elif service == "pin":
            _handle_pin_request(phone, pastoralist)
        elif service == "weight":
            _start_weight_flow(phone, pastoralist)
        elif service == "herd":
            species = pastoralist.primary_species or "cattle"
            conversation.set_state(phone, "weight.herd_count",
                                   {"species": species, "samples": []})
            whatsapp_client.send_text(phone, ASK_HERD_COUNT[pastoralist.preferred_language])
        elif service == "map":
            _handle_map_request(phone, pastoralist)
        elif service == "status":
            _handle_status_request(phone, pastoralist)
        elif service == "voice":
            set_voice_replies(phone, True)
            pastoralist.voice_replies = True
            _send_reply(phone, pastoralist, VOICE_ON_MSG[pastoralist.preferred_language], voice=True)
        elif service == "language":
            whatsapp_client.send_text(
                phone,
                {
                    "swahili": "Tuma 'english' kwa Kiingereza, au 'swahili' kwa Kiswahili.",
                    "english": "Send 'swahili' for Swahili, or 'english' for English.",
                }[pastoralist.preferred_language],
            )
        return

    gt_intent = ai.classify_report(text)
    if gt_intent is not None:
        record_ground_truth(pastoralist, gt_intent, text)
        thanks = {
            "swahili": "Asante kwa taarifa! Itatusaidia kuboresha maelezo ya eneo hilo.",
            "english": "Thank you for the report! It will help us improve information for that area.",
        }[pastoralist.preferred_language]
        _send_reply(phone, pastoralist, thanks, voice=voice)
        return

    _show_menu(phone, pastoralist)


def _handle_water_request(phone: str, pastoralist) -> None:
    """'maji'/'water': show the herder's remembered water point, or run the
    confirmation flow (named nearby list + numbered map) if not confirmed yet."""
    if pastoralist.water_source_id:
        try:
            ws = get_water_source(phone)
        except Exception:  # noqa: BLE001
            ws = None
        if ws:
            label = ws["name"] or ws["ward"] or "Maji"
            whatsapp_client.send_text(
                phone,
                {
                    "swahili": f"Chanzo chako cha maji kimeandikishwa: {label} "
                               f"({ws['county']}).\nTuma eneo lako (location) au 'map' kuona taarifa zake.",
                    "english": f"Your registered water point: {label} "
                               f"({ws['county']}).\nSend your location or 'map' to see its info.",
                }[pastoralist.preferred_language],
            )
            return
    if get_last_location(phone) is None:
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Ili nikuonyeshe vyanzo vya maji karibu nawe, kwanza tuma eneo lako "
                           "(location) kwenye WhatsApp.",
                "english": "To show you the water points near you, first share your location "
                           "on WhatsApp.",
            }[pastoralist.preferred_language],
        )
        return
    _ask_confirm_water(phone, pastoralist)


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
    species = pastoralist.primary_species or "camel"

    # Prefer the herder's CONFIRMED water point when they are still within its
    # ring (they told us where their animals drink); otherwise fall back to the
    # nearest reachable point. This keeps every map about THEIR water point.
    confirmed_id = pastoralist.water_source_id
    water_source_id = None
    if confirmed_id:
        try:
            reachable = water_reach.find_nearest_reachable_water(lon, lat, species, limit=20)
            if any(c.water_source_id == confirmed_id for c in reachable):
                water_source_id = confirmed_id
        except Exception:  # noqa: BLE001
            log.exception("confirmed water reach check failed")
    if water_source_id is None:
        candidates = water_reach.find_nearest_reachable_water(lon, lat, species, limit=1)
        if not candidates:
            whatsapp_client.send_text(phone, NEW_WATER_POINT_OFFER[pastoralist.preferred_language])
            return
        water_source_id = candidates[0].water_source_id

    lang_key = "swa" if pastoralist.preferred_language == "swahili" else "eng"
    # 'Where am I?' in words: nearest named landmark to the herder's location.
    try:
        from app.services.map_renderer import _herder_place_label

        here_place = _herder_place_label(lon, lat)
        place_bit = (f" Wewe uko karibu na {here_place}."
                     if here_place and pastoralist.preferred_language == "swahili"
                     else f" You are near {here_place}." if here_place else "")
    except Exception:  # noqa: BLE001
        place_bit = ""
    url = (f"{settings.app_public_base_url.rstrip('/')}/map/{water_source_id}.png"
           f"?lat={lat}&lon={lon}&species={species}&pasture=1&lang={lang_key}"
           f"&confirm={confirmed_id}&v=7")

    # Concrete, herder-friendly caption: where they are, water direction +
    # distance, and pasture direction + distance (when the COG is available).
    ws = next((w for w in water_sources.list_water_sources() if w.id == water_source_id), None)
    water_bit = pasture_bit = ""
    if ws:
        try:
            w_bearing, w_dist = map_renderer.water_guidance(lat, lon, ws.lat, ws.lon)
            w_dir = map_renderer.compass_swa(w_bearing)
            w_txt = f"{w_dist:.1f} km" if w_dist >= 1 else f"{w_dist * 1000:.0f} m"
            if pastoralist.preferred_language == "swahili":
                water_bit = f" Maji: {w_dir}, ~{w_txt}."
            else:
                water_bit = f" Water: {w_dir}, ~{w_txt}."
        except Exception:  # noqa: BLE001
            log.exception("water guidance failed")
        try:
            p_guid = map_renderer.pasture_guidance(water_source_id, lon, lat)
            if p_guid:
                p_dir = map_renderer.compass_swa(p_guid[0])
                p_txt = f"{p_guid[1]:.1f} km" if p_guid[1] >= 1 else f"{p_guid[1] * 1000:.0f} m"
                if pastoralist.preferred_language == "swahili":
                    pasture_bit = f" Malisho bora: {p_dir}, ~{p_txt} (mshale kijani)."
                else:
                    pasture_bit = f" Best pasture: {p_dir}, ~{p_txt} (green arrow)."
        except Exception:  # noqa: BLE001
            log.exception("pasture guidance failed")
    whatsapp_client.send_image_bytes_url(
        phone,
        url,
        caption={
            "swahili": (f"Ramani yako{(' - ' + (ws.label if ws else '')) if ws else ''}."
                        f" Bluu = WEWE HAPA, nyekundu = maji yako."
                        f"{place_bit}{water_bit}{pasture_bit}"
                        f" Majina ya miji na mto yameandikwa kwenye ramani. "
                        f"Duara za maji: buluu=mto, chungwa=kisima (borehole), "
                        f"teal=kisima cha kuchimba, kijani=chemchemi, rangi ya maji=bwawa."),
            "english": (f"Your map{(' - ' + (ws.label if ws else '')) if ws else ''}."
                        f" Blue = YOU ARE HERE, red = your water."
                        f"{place_bit}{water_bit}{pasture_bit}"
                        f" Towns and rivers are labelled on the map. "
                        f"Water markers: blue=river, orange=borehole, teal=well, "
                        f"green=spring, cyan=pan."),
        }[pastoralist.preferred_language],
    )


def _handle_pin_request(phone: str, pastoralist) -> None:
    """Register the herder's last shared location as a new water point.

    The location is first VALIDATED against known water sources (duplicate?
    near a known point?) so we don't burn GEE compute on a wrong pin. The
    herder then confirms the water type before anything is created, and the
    build pipeline starts automatically (build tracker + scheduled builder).
    """
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

    result = water_validation.validate_pin(lon, lat)
    if result.is_duplicate:
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Eneo hili tayari limeandikishwa kwenye mfumo wetu (chanso cha "
                           "maji kinachojulikana). Tuma eneo lako (location) ili uone taarifa zake, "
                           "au tuma 'map' kuona ramani yake.",
                "english": "This location is already registered in our system as a known "
                           "water source. Send your location to see its info, or send 'map' "
                           "to view its map.",
            }[pastoralist.preferred_language],
        )
        return

    if result.has_nearby_source:
        nearby_note = {
            "swahili": f"Kuna chanzo cha maji kinachojulikana umbali wa {result.distance_to_nearest_m/1000:.1f} km "
                       "kutoka hapa. Tafadhali hakikisha unapima mahali pale maji yalipo hasa.",
            "english": f"There is a known water source about {result.distance_to_nearest_m/1000:.1f} km "
                       "from here. Please make sure you are pointing at the exact water spot.",
        }[pastoralist.preferred_language]
    else:
        nearby_note = {
            "swahili": "Hatuna chanzo cha maji kinachojulikana karibu na eneo hili.",
            "english": "We don't have a known water source near this location.",
        }[pastoralist.preferred_language]

    conversation.set_state(phone, "pin.confirm", {"lon": lon, "lat": lat})
    whatsapp_client.send_quick_reply_buttons(
        phone,
        f"{nearby_note}\n\n"
        + {
            "swahili": "Je, eneo hili ni chanzo cha maji hasa? Chagua aina yake:",
            "english": "Is this really a water source? Choose its type:",
        }[pastoralist.preferred_language],
        [
            ("type:borehole", "Kisima (borehole)"),
            ("type:well", "Kisima cha kuchimba"),
            ("type:river", "Mto"),
        ],
    )


def _finish_pin_registration(phone: str, pastoralist, water_type: str, name: str | None = None) -> None:
    """Create the validated water source, its species rings, and start the build."""
    loc = get_last_location(phone)
    if loc is None:
        conversation.clear_state(phone)
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Tuma eneo lako (location) kwanza.",
                "english": "Send your location first.",
            }[pastoralist.preferred_language],
        )
        return
    lon, lat = loc

    # Re-validate to be safe against a stale pin.
    result = water_validation.validate_pin(lon, lat)
    if result.is_duplicate:
        conversation.clear_state(phone)
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Eneo hili limeandikishwa tayari. Tuma 'map' kuona ramani yake.",
                "english": "This location is already registered. Send 'map' to see it.",
            }[pastoralist.preferred_language],
        )
        return

    # Confidence from validation: near a known source = moderate; otherwise low
    # until a herder confirms it in the field.
    confidence = 0.6 if result.has_nearby_source else 0.45
    try:
        ws = water_sources.create_water_source(
            lon=lon, lat=lat, source_type="ground_truth",
            source_ref=f"whatsapp:{water_type}:{phone}", name=name,
            water_type=water_type, confidence=confidence,
        )
    except Exception:  # noqa: BLE001
        log.exception(f"Failed to register water point for {phone}")
        conversation.clear_state(phone)
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Samahani, hatukuweza kuongeza eneo hili. Jaribu tena baadaye.",
                "english": "Sorry, we could not register this area. Please try again later.",
            }[pastoralist.preferred_language],
        )
        return

    build_tracker.start_build(ws.id, phone, pastoralist.preferred_language)
    # The newly pinned point IS where their animals drink — remember it so the
    # system personalises their maps/advisories from now on.
    try:
        set_water_source(phone, ws.id)
        pastoralist.water_source_id = ws.id
    except Exception:  # noqa: BLE001
        log.exception("failed to set pinned point as confirmed water source (non-fatal)")
    conversation.clear_state(phone)
    _send_reply(
        phone,
        pastoralist,
        {
            "swahili": "Asante! Eneo lako limeandikishwa kama chanzo cha maji na "
                       "tumeanza kujenga taarifa zake. Tutakutumia maendeleo ya hatua kwa hatua — subiri kidogo.",
            "english": "Thank you! Your location is now registered as a water source and "
                       "we have started building its info. We will send progress updates — hang on.",
        }[pastoralist.preferred_language],
        voice=pastoralist.voice_replies,
    )


def _handle_status_request(phone: str, pastoralist) -> None:
    """Reply with the current build progress of the herder's last pinned point."""
    build = build_tracker.get_build_for_phone(phone)
    if build is None:
        _send_reply(
            phone,
            pastoralist,
            {
                "swahili": "Hujapokea chanzo kipya cha maji bado. Tuma eneo lako "
                           "(location) kisha 'PIN' ili tujenge chanzo chako.",
                "english": "You have not registered a new water point yet. Send your "
                           "location, then 'PIN' to build one.",
            }[pastoralist.preferred_language],
            voice=pastoralist.voice_replies,
        )
        return
    if build.status == "pending" or build.status == "running":
        text = {
            "swahili": f"Chanzo chako kiko hatua ya {build.progress}% — {build.stage or 'inaandaliwa'}.",
            "english": f"Your water point is at {build.progress}% — {build.stage or 'being prepared'}.",
        }[pastoralist.preferred_language]
    elif build.status == "done":
        text = {
            "swahili": "Chanzo chako kimekamilika! Tuma 'map' uone ramani yake.",
            "english": "Your water point is ready! Send 'map' to see it.",
        }[pastoralist.preferred_language]
    else:
        text = {
            "swahili": "Chanzo chako kilikwama kwa bahati mbaya. Tutaendelea kujaribu — "
                       "tumie 'status' baadaye au wasiliana nasi.",
            "english": "Your water point hit an issue. We will keep retrying — "
                       "send 'status' later or contact us.",
        }[pastoralist.preferred_language]
    _send_reply(phone, pastoralist, text, voice=pastoralist.voice_replies)


# =============================================================================
# Guided conversation flows (onboarding / weight / pin)
# =============================================================================


def _handle_active_flow(phone: str, pastoralist, text: str | None) -> bool:
    """Resume an in-progress guided flow. Returns True if the message was
    consumed by a flow.

    Flows are NOT traps: any message asking for the menu/help (or naming a
    different service) clears the flow state so the herder can always escape
    and use another service.
    """
    state, data = conversation.get_state(phone)
    if not state:
        return False

    t = (text or "").strip().lower()

    # Universal escape hatches: menu/help clears the flow and shows the menu.
    if any(k in t for k in MENU_KEYWORDS):
        conversation.clear_state(phone)
        _show_menu(phone, pastoralist)
        return True
    if t in ("done", "isha", "stop", "kumaliza", "cancel", "ghairi", "futa"):
        conversation.clear_state(phone)
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Sawa, nimeacha mchakato huu. Tuma 'menu' kuona huduma zote.",
                "english": "Okay, I left that process. Send 'menu' to see all services.",
            }[pastoralist.preferred_language],
        )
        return True

    # If the herder names another service mid-flow, jump to it (don't trap them).
    if state.startswith("weight.") or state.startswith("pin.") or state.startswith("onboarding."):
        if any(k in t for k in WEIGHT_KEYWORDS) and not state.startswith("weight.species"):
            conversation.clear_state(phone)
            _start_weight_flow(phone, pastoralist)
            return True
        if any(k in t for k in MAP_KEYWORDS) and not state.startswith("pin."):
            conversation.clear_state(phone)
            _handle_map_request(phone, pastoralist)
            return True
        if any(k in t for k in STATUS_KEYWORDS):
            conversation.clear_state(phone)
            _handle_status_request(phone, pastoralist)
            return True

    if state.startswith("onboarding."):
        _handle_onboarding_step(phone, pastoralist, text)
        return True
    if state.startswith("weight."):
        _handle_weight_step(phone, pastoralist, state, data, text)
        return True
    if state.startswith("pin."):
        _handle_pin_step(phone, pastoralist, state, data, text)
        return True
    return False


def _show_menu(phone: str, pastoralist) -> None:
    """Send the services menu so a herder always knows what they can do."""
    whatsapp_client.send_text(phone, MENU_MSG[pastoralist.preferred_language])


# --- onboarding --------------------------------------------------------------

def _start_onboarding(phone: str, pastoralist, text: str, voice: bool = False) -> None:
    conversation.set_state(phone, "onboarding.name", {})
    whatsapp_client.send_text(phone, registration.ASK_NAME[pastoralist.preferred_language])


def _handle_onboarding_step(phone: str, pastoralist, text: str | None) -> None:
    state, data = conversation.get_state(phone)
    lang = pastoralist.preferred_language
    t = (text or "").strip().lower()

    if t in ("stop", "isha", "cancel", "ghairi"):
        conversation.clear_state(phone)
        whatsapp_client.send_text(phone, registration.WELCOME.format(name="").strip())
        return

    if state == "onboarding.water":
        # The herder is confirming which water point their animals drink from.
        # If the reply isn't a valid choice, RE-ASK (never reply silently).
        if not _handle_confirm_water_reply(phone, pastoralist, text):
            whatsapp_client.send_text(phone, ASK_CONFIRM_WATER_RETRY[lang])
        return

    if state == "onboarding.name":
        if not registration.is_valid_name(text or ""):
            whatsapp_client.send_text(
                phone,
                {
                    "swahili": "Samahani, tafadhali andika jina lako halisi (herufi pekee). "
                               "Jina lako ni nani?",
                    "english": "Sorry, please type your real name (letters only). "
                               "What is your name?",
                }[lang],
            )
            return
        name = text.strip().title()
        registration.set_name(phone, name)
        data["name"] = name
        data["composition"] = {}
        conversation.set_state(phone, "onboarding.language", data)
        whatsapp_client.send_text(phone, registration.ASK_LANGUAGE[lang].format(name=name))

    elif state == "onboarding.language":
        for language, keys in LANGUAGE_KEYWORDS.items():
            if any(k in t for k in keys):
                registration.set_language(phone, language)
                pastoralist.preferred_language = language
                data["language"] = language
                break
        else:
            whatsapp_client.send_text(phone, registration.ASK_LANGUAGE[lang].format(name=data.get("name", "")))
            return
        conversation.set_state(phone, "onboarding.animals", data)
        whatsapp_client.send_quick_reply_buttons(
            phone, registration.ASK_ANIMALS[pastoralist.preferred_language], registration.ANIMAL_BUTTONS
        )

    elif state == "onboarding.animals":
        species = registration.detect_species(t)
        if species is None:
            whatsapp_client.send_text(phone, registration.ASK_ANIMALS[pastoralist.preferred_language])
            return
        data["species"] = species
        conversation.set_state(phone, "onboarding.count", data)
        whatsapp_client.send_text(phone, registration.ASK_COUNT[pastoralist.preferred_language])

    elif state == "onboarding.more_type":
        species = registration.detect_species(t)
        if species is None:
            whatsapp_client.send_text(phone, registration.ASK_MORE_TYPE[pastoralist.preferred_language])
            return
        data["species"] = species
        conversation.set_state(phone, "onboarding.count", data)
        whatsapp_client.send_text(phone, registration.ASK_COUNT[pastoralist.preferred_language])

    elif state == "onboarding.more":
        if any(k in t for k in ("yes", "ndiyo", "ndio", "ndiyo!", "sawa", "naam", "yes_more")):
            conversation.set_state(phone, "onboarding.more_type", data)
            whatsapp_client.send_quick_reply_buttons(
                phone, registration.ASK_MORE_TYPE[pastoralist.preferred_language], registration.ANIMAL_BUTTONS
            )
            return
        if any(k in t for k in ("no", "hapana", "no_more", "kumaliza", "la", "siyo")):
            # No more animals -> finish onboarding.
            _finish_onboarding(phone, pastoralist, data)
            return
        whatsapp_client.send_text(phone, registration.ASK_OTHER_ANIMALS[pastoralist.preferred_language])
        whatsapp_client.send_quick_reply_buttons(
            phone,
            registration.ASK_OTHER_ANIMALS[pastoralist.preferred_language],
            [("yes_more", "Ndiyo, nyingine"), ("no_more", "Hapana, kumaliza")],
        )
        return

    elif state == "onboarding.count":
        count = _parse_int(t)
        if count is None or count < 1:
            whatsapp_client.send_text(phone, registration.ASK_COUNT[pastoralist.preferred_language])
            return
        species = data.get("species", "cattle")
        composition: dict = dict(data.get("composition") or {})
        composition[species] = composition.get(species, 0) + count
        data["composition"] = composition
        data["primary_species"] = data.get("primary_species") or species

        # Loop: ask whether there are other animal types (pastoralists are mixed).
        label = registration.SPECIES_LABELS.get(species, {}).get(lang, species)
        conversation.set_state(phone, "onboarding.more", data)
        whatsapp_client.send_text(
            phone,
            registration.ADDED_ANIMAL[lang].format(species_label=label, count=count),
        )
        whatsapp_client.send_quick_reply_buttons(
            phone,
            registration.ASK_OTHER_ANIMALS[pastoralist.preferred_language],
            [("yes_more", "Ndiyo, nyingine"), ("no_more", "Hapana, kumaliza")],
        )


def _finish_onboarding(phone: str, pastoralist, data: dict) -> None:
    composition: dict = data.get("composition") or {}
    if not composition:
        composition = {data.get("primary_species", "cattle"): 1}
    primary = data.get("primary_species") or next(iter(composition))
    upsert_pastoralist(phone, species=primary)
    registration.complete_onboarding(phone, composition)
    conversation.clear_state(phone)

    name = data.get("name", "")
    whatsapp_client.send_text(
        phone, registration.ONBOARDING_DONE[pastoralist.preferred_language].format(name=name)
    )
    # Step 2 of registration: ask the herder to CONFIRM which water point their
    # animals drink from (named options + a numbered map). This lets the system
    # remember them and personalise every map/advisory afterwards.
    if get_last_location(phone):
        _ask_confirm_water(phone, pastoralist)
    else:
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Tuma eneo lako (location) ili nikupatie taarifa za maji na malisho.",
                "english": "Send your location so I can give you water and pasture info.",
            }[pastoralist.preferred_language],
        )


def _ask_confirm_water(phone: str, pastoralist) -> bool:
    """Present the nearest NAMED water points (numbered on a map + a WhatsApp
    list) and ask the herder which one their animals drink from.

    Returns False when there's no location to query (callers must reply then)."""
    loc = get_last_location(phone)
    if not loc:
        return False
    lon, lat = loc
    try:
        nearby = water_reach.list_nearby_water_sources(lon, lat, limit=10)
    except Exception:  # noqa: BLE001
        log.exception("nearby water list failed")
        nearby = []
    if not nearby:
        conversation.set_state(phone, "onboarding.water", {"nearby": []})
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Hatuna chanzo cha maji kinachojulikana karibu na eneo lako. "
                           "Tuma eneo lako hasa pale wanyama wako wanakunywa, kisha tuma 'PIN' "
                           "kuliandikisha kipya.",
                "english": "We don't have a known water point near you. Share your location "
                           "exactly where your animals drink, then send 'PIN' to register it new.",
            }[pastoralist.preferred_language],
        )
        return True

    conversation.set_state(phone, "onboarding.water",
                           {"nearby": [n["water_source_id"] for n in nearby],
                            "asked_at": _now_iso()})
    lang = pastoralist.preferred_language
    name = pastoralist.first_name or ""
    # A pastoralist identifies a water point by its LOCAL NAME (e.g. "Oldonyiro
    # borehole") — ward + km alone is not enough. Show name, type, distance AND
    # compass direction so nearby points are distinguishable.
    lines = [f"{i + 1}. {_source_label(n, lang)} — {_water_type_swa(n, lang)}, "
             f"{n['distance_km']:.1f} km {n.get('direction_swa', '')}"
             for i, n in enumerate(nearby)]
    whatsapp_client.send_text(
        phone,
        ASK_CONFIRM_WATER[lang].format(name=name, n=len(nearby), list="\n".join(lines)),
    )
    # Numbered map: nearest source's rings + numbered markers 1..N, so the map
    # "tells instantly" which number matches which water point.
    _send_confirmation_map(phone, pastoralist, nearby, lon, lat)
    # Interactive WhatsApp list (native picker) — same ids, no numbers needed.
    try:
        whatsapp_client.send_interactive_list(
            phone,
            ASK_CONFIRM_WATER_RETRY[lang],
            "Chagua chanzo",
            [("wp:" + n["water_source_id"],
              f"{_source_label(n, lang)} ({n['distance_km']:.1f} km {n.get('direction_swa', '')})")
             for n in nearby]
            + [("wp:none", "Hakipo kwenye orodha" if lang == "swahili" else "Not in the list")],
            footer=ASK_CONFIRM_WATER_RETRY[lang][:60],
        )
    except Exception:  # noqa: BLE001
        log.exception("interactive list failed (falling back to numbered reply)")
    return True


def _send_confirmation_map(phone: str, pastoralist, nearby: list[dict],
                           lon: float, lat: float) -> None:
    """Render + send the numbered confirmation map (nearest source's rings +
    numbered water-point markers)."""
    settings = get_settings()
    if not settings.app_public_base_url:
        return
    nearest = nearby[0]
    numbered = ",".join(n["water_source_id"] for n in nearby[:10])
    species = pastoralist.primary_species or "camel"
    lang_key = "swa" if pastoralist.preferred_language == "swahili" else "eng"
    url = (f"{settings.app_public_base_url.rstrip('/')}/map/{nearest['water_source_id']}.png"
           f"?lat={lat}&lon={lon}&species={species}&pasture=1&lang={lang_key}"
           f"&numbered={numbered}&v=7")
    try:
        whatsapp_client.send_image_bytes_url(
            phone, url,
            caption={
                "swahili": "Ramani ya chanzo cha karibu zaidi. Tuma namba ya chanzo chako cha maji "
                           "au chagua kutoka kwenye orodha.",
                "english": "Map of the nearest water source. Send the number of your water "
                           "point or pick it from the list.",
            }[pastoralist.preferred_language],
        )
    except Exception:  # noqa: BLE001
        log.exception("confirmation map send failed (non-fatal)")


def _flow_stale(data: dict, hours: int = 24) -> bool:
    """True when the flow state data is older than `hours` (self-heal stuck
    conversations from a previous session)."""
    asked = data.get("asked_at")
    if not asked:
        return False
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(asked).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() > hours * 3600
    except Exception:  # noqa: BLE001
        return False


def _now_iso() -> str:
    """UTC timestamp used for flow-state staleness (`asked_at`)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _handle_confirm_water_reply(phone: str, pastoralist, text: str | None) -> bool:
    """Handle the herder's reply to the water-point confirmation. Returns True
    when handled (flow continues or completes), False to fall through.

    Escape hatches so the herder is NEVER trapped in this flow:
      'orodha'/'list'/'tena'  -> re-show a FRESH list + map (new nearby query)
      'menu'/'cancel' etc     -> cleared by _handle_active_flow's escape hatch
      'hakuna'/'none'         -> clear + guide them to PIN their own water point
    """
    t = (text or "").strip().lower()
    _, data = conversation.get_state(phone)
    nearby_ids: list = data.get("nearby") or []
    # Self-heal: a day-old list should never trap the herder — re-ask fresh.
    if _flow_stale(data) and not t.startswith("wp:"):
        _ask_confirm_water(phone, pastoralist)
        return True
    if not data.get("asked_at") and not t.startswith("wp:"):
        # State set before we recorded asked_at — fall back to the DB row age.
        age = conversation.state_age_seconds(phone)
        if age is not None and age > 24 * 3600:
            _ask_confirm_water(phone, pastoralist)
            return True
    if t.startswith("wp:"):
        ws_id = t[3:]
        if ws_id == "none":
            conversation.clear_state(phone)
            whatsapp_client.send_text(phone, WATER_CONFIRM_SKIP[pastoralist.preferred_language])
            return True
        if ws_id in nearby_ids:
            _confirm_water_source(phone, pastoralist, ws_id)
            return True
        whatsapp_client.send_text(phone, ASK_CONFIRM_WATER_RETRY[pastoralist.preferred_language])
        return True

    idx = _parse_int(t)
    if idx is not None and 1 <= idx <= len(nearby_ids):
        _confirm_water_source(phone, pastoralist, nearby_ids[idx - 1])
        return True

    # 'show the list again' — re-fetch a FRESH nearby list (e.g. stale/old data
    # from a previous day) and re-send the numbered list + map + picker.
    if any(k in t for k in ("orodha", "list", "tena", "onyesha", "update", "sasisha",
                            "fresh", "njia", "chaguo", "options")):
        if not _ask_confirm_water(phone, pastoralist):
            whatsapp_client.send_text(phone, ASK_CONFIRM_WATER_RETRY[pastoralist.preferred_language])
        return True

    if any(k in t for k in ("none", "hakuna", "sipati", "sio", "siyo", "la", "hapana",
                            "not", "cancel", "ghairi", "skip", "ruka", "acha", "futa")):
        conversation.clear_state(phone)
        whatsapp_client.send_text(phone, WATER_CONFIRM_SKIP[pastoralist.preferred_language])
        return True
    return False


def _confirm_water_source(phone: str, pastoralist, ws_id: str) -> None:
    """Remember the confirmed water point and send its personalised map."""
    try:
        set_water_source(phone, ws_id)
        pastoralist.water_source_id = ws_id
    except Exception:  # noqa: BLE001
        log.exception("failed to save confirmed water source (non-fatal)")
    conversation.clear_state(phone)
    # Refresh the herder record so the personalised map uses their species.
    fresh = get_pastoralist(phone)
    if fresh is not None:
        pastoralist = fresh
    ws = next((w for w in water_sources.list_water_sources() if w.id == ws_id), None)
    name = ws.label if ws else "Maji"
    whatsapp_client.send_text(
        phone, WATER_CONFIRMED[pastoralist.preferred_language].format(name=name)
    )
    _handle_map_request(phone, pastoralist)


def _parse_int(text: str | None) -> int | None:
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


# --- weight flow -------------------------------------------------------------

def _start_weight_flow(phone: str, pastoralist) -> None:
    conversation.set_state(phone, "weight.species", {})
    whatsapp_client.send_text(phone, WEIGHT_MSG[pastoralist.preferred_language])
    whatsapp_client.send_quick_reply_buttons(phone, WEIGHT_MSG[pastoralist.preferred_language],
                                             WEIGHT_ANIMAL_BUTTONS)


def _handle_weight_step(phone: str, pastoralist, state: str, data: dict, text: str | None) -> None:
    lang = pastoralist.preferred_language
    t = (text or "").strip().lower()

    if any(k in t for k in DONE_KEYWORDS):
        conversation.clear_state(phone)
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Sawa! Kama unahitaji kupima tena, tuma 'uzito' wakati wowote.",
                "english": "Okay! If you want to measure again, send 'weight' anytime.",
            }[lang],
        )
        return

    if state == "weight.species":
        species = _weight_species_from_text(t)
        if species is None:
            whatsapp_client.send_text(phone, WEIGHT_MSG[lang])
            return
        data["species"] = species
        conversation.set_state(phone, "weight.age", data)
        whatsapp_client.send_quick_reply_buttons(phone, ASK_AGE[lang], WEIGHT_AGE_BUTTONS)

    elif state == "weight.age":
        age = None
        for a, keys in AGE_KEYWORDS.items():
            if any(k in t for k in keys):
                age = a
                break
        if age is None:
            whatsapp_client.send_text(phone, ASK_AGE[lang])
            return
        data["age"] = age
        conversation.set_state(phone, "weight.girth", data)
        guide = weight_service.measurement_guide(lang)
        whatsapp_client.send_text(phone, ASK_GIRTH[lang].format(guide=guide))

    elif state == "weight.girth":
        girth = _parse_float(text)
        species = data.get("species", "cattle")
        if girth is None:
            whatsapp_client.send_text(phone, GIRTH_INVALID[lang].format(error="si namba"))
            return
        ok, err = weight_service.validate_girth(species, girth)
        if not ok:
            whatsapp_client.send_text(phone, GIRTH_INVALID[lang].format(error=err or ""))
            return
        est = weight_service.estimate_weight(species, girth, data.get("age", "adult"))
        try:
            weight_service.record_weight(
                pastoralist.id, species, girth, est.weight_kg,
                age_class=data.get("age"), sex=None,
            )
        except Exception:  # noqa: BLE001
            log.exception("failed to save weight record")
        med = weight_service.medication_note(lang)
        whatsapp_client.send_text(
            phone,
            WEIGHT_RESULT[lang].format(weight=est.weight_kg, girth=girth, medication=med),
        )
        data["last_girth"] = girth
        conversation.set_state(phone, "weight.idle", data)

    elif state == "weight.idle":
        if any(k in t for k in WEIGHT_KEYWORDS):
            conversation.set_state(phone, "weight.species", {})
            whatsapp_client.send_text(phone, WEIGHT_MSG[lang])
            whatsapp_client.send_quick_reply_buttons(phone, WEIGHT_MSG[lang], WEIGHT_ANIMAL_BUTTONS)
            return
        if any(k in t for k in HERD_KEYWORDS):
            conversation.set_state(phone, "weight.herd_count", {**data, "samples": []})
            whatsapp_client.send_text(phone, ASK_HERD_COUNT[lang])
            return
        whatsapp_client.send_text(phone, WEIGHT_MSG[lang])
        whatsapp_client.send_quick_reply_buttons(phone, WEIGHT_MSG[lang], WEIGHT_ANIMAL_BUTTONS)
        conversation.set_state(phone, "weight.species", {})

    elif state == "weight.herd_count":
        count = _parse_int(text)
        if count is None or count < 1:
            whatsapp_client.send_text(phone, ASK_HERD_COUNT[lang])
            return
        data["herd_count"] = count
        data["samples"] = []
        conversation.set_state(phone, "weight.sample", data)
        whatsapp_client.send_text(phone, ASK_SAMPLE[lang].format(sample=3))

    elif state == "weight.sample":
        species = data.get("species", "cattle")
        samples: list[float] = list(data.get("samples") or [])
        numbers = _parse_many_floats(text)
        for n in numbers:
            ok, _ = weight_service.validate_girth(species, n)
            if ok:
                samples.append(n)
        if not numbers:
            whatsapp_client.send_text(phone, ASK_SAMPLE[lang].format(sample=3))
            return
        data["samples"] = samples
        if len(samples) >= 3:
            _finish_herd_estimate(phone, pastoralist, data)
            return
        conversation.set_state(phone, "weight.sample", data)
        whatsapp_client.send_text(phone, SAMPLE_PARTIAL[lang].format(count=len(samples)))


def _finish_herd_estimate(phone: str, pastoralist, data: dict) -> None:
    lang = pastoralist.preferred_language
    species = data.get("species", "cattle")
    herd_count = int(data.get("herd_count", 1))
    samples: list[float] = list(data.get("samples") or [])
    if not samples:
        conversation.clear_state(phone)
        whatsapp_client.send_text(phone, ASK_HERD_COUNT[lang])
        return
    age = data.get("age", "adult")
    herd = weight_service.estimate_herd(species, herd_count, samples, age_class=age)
    try:
        weight_service.record_herd_estimate(
            pastoralist.id, species, herd.herd_count, herd.sample_size,
            herd.sample_mean_kg, herd.estimated_total_kg,
            herd.low_estimate_kg, herd.high_estimate_kg,
        )
    except Exception:  # noqa: BLE001
        log.exception("failed to save herd estimate")
    med = weight_service.medication_note(lang)
    label = _species_label(species, lang)
    whatsapp_client.send_text(
        phone,
        HERD_RESULT[lang].format(
            species=label, count=herd.herd_count, sample=herd.sample_size,
            mean=herd.sample_mean_kg, total=herd.estimated_total_kg,
            low=herd.low_estimate_kg, high=herd.high_estimate_kg, medication=med,
        ),
    )
    conversation.clear_state(phone)


def _weight_species_from_text(t: str) -> str | None:
    for key, aliases in {
        "cattle": ["cattle", "cow", "ng'ombe", "ngombe"],
        "goat": ["goat", "mbuzi"],
        "sheep": ["sheep", "kondoo"],
        "camel": ["camel", "ngamia", "gaala"],
    }.items():
        if any(a in t for a in aliases):
            return key
    if "weight:" in t:
        return t.split("weight:", 1)[1].strip()
    return None


def _parse_float(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = text.strip().replace(",", ".")
    digits = [ch for ch in cleaned if ch.isdigit() or ch == "."]
    if not digits or digits.count(".") > 1:
        return None
    try:
        return float("".join(digits))
    except ValueError:
        return None


def _parse_many_floats(text: str | None) -> list[float]:
    if not text:
        return []
    out = []
    for token in text.replace(",", " ").split():
        val = _parse_float(token)
        if val is not None:
            out.append(val)
    return out


def _species_label(species: str, lang: str) -> str:
    labels = {
        "cattle": {"swahili": "ng'ombe", "english": "cattle"},
        "goat": {"swahili": "mbuzi", "english": "goats"},
        "sheep": {"swahili": "kondoo", "english": "sheep"},
        "camel": {"swahili": "ngamia", "english": "camels"},
    }
    return labels.get(species, {}).get(lang, species)


# --- pin flow ----------------------------------------------------------------

def _handle_pin_step(phone: str, pastoralist, state: str, data: dict, text: str | None) -> None:
    lang = pastoralist.preferred_language
    t = (text or "").strip().lower()

    if state == "pin.confirm":
        water_type = None
        for key in water_validation.WATER_TYPES:
            if f"type:{key}" in t:
                water_type = key
                break
        if water_type is None:
            if any(w in t for w in ["borehole", "kisima", "bore"]):
                water_type = "borehole"
            elif any(w in t for w in ["well", "kuchimba"]):
                water_type = "well"
            elif any(w in t for w in ["river", "mto"]):
                water_type = "river"
            elif any(w in t for w in ["pan", "bwawa", "dam"]):
                water_type = "pan"
            elif any(w in t for w in ["spring", "chemchemi"]):
                water_type = "spring"
        if water_type is None:
            whatsapp_client.send_text(
                phone,
                {
                    "swahili": "Chagua aina ya chanzo cha maji: borehole, kisima cha kuchimba, mto, bwawa, au chemchemi.",
                    "english": "Choose the water type: borehole, well, river, pan, or spring.",
                }[lang],
            )
            return
        # Ask the herder to NAME it — a pastoralist identifies a water point by
        # its local name ("Oldonyiro borehole"), not a ward. We store the name
        # so they can recognise it in every future list/map.
        conversation.set_state(phone, "pin.name", {**data, "water_type": water_type})
        whatsapp_client.send_text(
            phone,
            {
                "swahili": "Chaguo lako limepokelewa. Jina la chanzo hiki ni nini? "
                           "(k.m. 'Kisima cha Oldonyiro', 'Mto Ewaso') — au tuma 'ruka' "
                           "kama hujui jina.",
                "english": "Got it. What is this water point called? "
                           "(e.g. 'Oldonyiro borehole', 'Ewaso river') — or send 'skip' "
                           "if you don't know a name.",
            }[lang],
        )

    elif state == "pin.name":
        name = None
        if not any(k in t for k in ("ruka", "skip", "sijui", "la", "hapana", "none")):
            candidate = (text or "").strip().title()
            # Accept a real name only (2+ letters, no digits-only, not a command).
            if len(candidate) >= 2 and not any(ch.isdigit() for ch in candidate):
                name = candidate[:60]
        _finish_pin_registration(phone, pastoralist, data.get("water_type", "well"), name)
