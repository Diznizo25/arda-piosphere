"""Debug/ops endpoints for the team — not for public use.

- /dev/transcribe lets us test voice notes (including accented speech) without
  going through WhatsApp: POST raw audio bytes and get the transcript back.
  Guarded by X-Debug-Key == WHATSAPP_VERIFY_TOKEN.
- /dev/notify lets the GitHub Actions water-point builder push stage progress to
  a herder (text + rendered progress-bar image). Guarded by
  X-Build-Key == sha256(DATABASE_URL), a secret both the web service and the
  builder job already know, so no extra credential setup is required.
- /dev/chat runs the grounded chat layer (app/services/chat.py) on a question and
  returns what a herder WOULD get, without sending anything to WhatsApp. Same
  X-Debug-Key guard as /dev/transcribe. This is how to sanity-check wording,
  guardrails and the model's behaviour in production.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import get_settings
from app.services import build_progress, speech, whatsapp_client

log = logging.getLogger(__name__)
router = APIRouter(prefix="/dev", tags=["dev"])


@router.post("/transcribe")
async def transcribe(request: Request, x_debug_key: str = Header(default="")) -> dict:
    """Transcribe raw audio bytes (OGG/Opus or WAV) for accent testing.

    Example:
      curl -X POST -H "X-Debug-Key: <WHATSAPP_VERIFY_TOKEN>" \\
           --data-binary @my_voice_note.ogg \\
           "https://arda-piosphere.onrender.com/dev/transcribe?language=swahili"
    """
    settings = get_settings()
    if not x_debug_key or x_debug_key != settings.whatsapp_verify_token:
        raise HTTPException(status_code=401, detail="Invalid debug key")

    body = await request.body()
    if not body:
        return {"transcript": "", "error": "empty body"}

    language = request.query_params.get("language", "swahili")
    result = speech.transcribe_voice_note(body, language)
    if result is None:
        return {"transcript": "", "error": "transcription failed"}

    return {
        "transcript": result.text,
        "language": result.language,
        "confidence": round(result.confidence, 3),
        "duration_s": round(result.duration_s, 1),
        "too_long": result.duration_s > speech.MAX_DURATION_S,
    }


@router.post("/chat")
async def chat_probe(request: Request, x_debug_key: str = Header(default="")) -> dict:
    """Ask the grounded chat layer exactly what a herder would get, WITHOUT sending
    anything to WhatsApp. Guarded by X-Debug-Key == WHATSAPP_VERIFY_TOKEN.

    Body: {text, phone?, language?, species?, lat?, lon?}
      - phone: look up a real herder (their water point/language/species are used);
        omit it to probe as an anonymous herder.
      - lat/lon: probe the advisory facts at explicit coordinates.

    Returns the answer plus whether the language model was actually used (a false
    `used_llm` means the deterministic path answered — worth knowing when judging
    a reply).
    """
    settings = get_settings()
    if not x_debug_key or x_debug_key != settings.whatsapp_verify_token:
        raise HTTPException(status_code=401, detail="Invalid debug key")

    payload = await request.json()
    text = (payload.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "text is required"}

    from app.services import ai, chat, pastoralists

    phone = payload.get("phone") or "+254000000000"
    herder = None
    if payload.get("phone"):
        try:
            herder = pastoralists.get_pastoralist(phone)
        except Exception:  # noqa: BLE001
            log.exception("chat probe: herder lookup failed")
    if herder is None:
        herder = _StubHerder(
            phone=phone,
            language=payload.get("language", "swahili"),
            species=payload.get("species", "cattle"),
        )

    lat, lon = payload.get("lat"), payload.get("lon")
    sections = chat.intent_sections(text)

    def gather(h, question, secs):
        return chat.gather_facts(h, question, secs, lat=lat, lon=lon)

    used = {"llm": False}

    def llm(system: str, facts_json: str, question: str):
        used["llm"] = True
        return ai.grounded_answer(system, facts_json, question)

    answer = chat.answer(herder, text, herder.preferred_language,
                         gather=gather, llm=llm)
    return {
        "ok": True,
        "answer": answer,
        "would_show_menu": answer is None,
        "sections": sections,
        "used_llm": used["llm"],
        "disease_guardrail": chat.is_disease_question(text),
    }


@router.post("/landmark")
async def landmark_probe(request: Request, x_debug_key: str = Header(default="")) -> dict:
    """Resolve a place named in free text, exactly as the WhatsApp intake would.

    Guarded by X-Debug-Key == WHATSAPP_VERIFY_TOKEN. Useful for checking a spelling
    herders actually use before trusting it in the field.

    Body: {text}
    Returns matched / ambiguous / none, with candidates and how each scored.
    """
    settings = get_settings()
    if not x_debug_key or x_debug_key != settings.whatsapp_verify_token:
        raise HTTPException(status_code=401, detail="Invalid debug key")

    payload = await request.json()
    text = (payload.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "text is required"}

    from app.services import landmarks

    res = landmarks.resolve(text, include_water_points=payload.get("water_points", True))
    return {
        "ok": True,
        "status": res.status,
        "reason": res.reason,
        "tokens": landmarks.tokens(text),
        "best": res.best.as_dict() if res.best else None,
        "candidates": [m.as_dict() for m in res.candidates],
        "prompt": (landmarks.describe(res.candidates, "swahili")
                   if res.status == "ambiguous" else None),
    }


class _StubHerder:
    """Minimal stand-in for an anonymous probe (matches the Pastoralist fields the
    chat layer reads, so no database row is needed)."""

    def __init__(self, phone: str, language: str, species: str) -> None:
        self.phone_number = phone
        self.preferred_language = language if language in ("swahili", "english") else "swahili"
        self.primary_species = species
        self.water_source_id = None
        self.water_interval = "daily"


def _valid_build_key(provided: str) -> bool:
    settings = get_settings()
    expected = hashlib.sha256(settings.database_url.encode()).hexdigest()
    return bool(provided) and hmac.compare_digest(provided, expected)


@router.post("/notify")
async def notify(request: Request, x_build_key: str = Header(default="")) -> dict:
    """Send a build-progress update to a herder (called by the builder job).

    Body JSON: {phone, text, progress?, stage?, water_source_id?, done?}
    - always sends `text`
    - when `progress` is present, also sends the rendered progress-bar image
    - when `done` is true (or progress==100), also sends the ring-map image
    """
    if not _valid_build_key(x_build_key):
        raise HTTPException(status_code=401, detail="Invalid build key")

    payload = await request.json()
    phone = payload.get("phone")
    text = payload.get("text")
    if not phone or not text:
        return {"ok": False, "error": "phone and text are required"}

    language = payload.get("language", "swahili")
    try:
        whatsapp_client.send_text(phone, text)

        progress = payload.get("progress")
        if progress is not None:
            stage = payload.get("stage", "")
            png = build_progress.render_progress_bar(int(progress), stage, language)
            media_id = whatsapp_client.upload_media(png, mime_type="image/png")
            if media_id:
                whatsapp_client.send_image(phone, media_id, caption=text)

        if payload.get("done") or progress == 100:
            water_source_id = payload.get("water_source_id")
            if water_source_id:
                settings = get_settings()
                url = (
                    f"{settings.app_public_base_url.rstrip('/')}"
                    f"/map/{water_source_id}.png"
                )
                caption = (
                    {"swahili": "Ramani ya chanzo chako kipya cha maji.",
                     "english": "Map of your new water point."}[language]
                )
                whatsapp_client.send_image_bytes_url(phone, url, caption=caption)
    except Exception:  # noqa: BLE001
        log.exception("Failed to send build notification to %s", phone)
        return {"ok": False, "error": "notification send failed"}

    return {"ok": True}
