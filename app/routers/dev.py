"""Debug/ops endpoints for the team — not for public use.

- /dev/transcribe lets us test voice notes (including accented speech) without
  going through WhatsApp: POST raw audio bytes and get the transcript back.
  Guarded by X-Debug-Key == WHATSAPP_VERIFY_TOKEN.
- /dev/notify lets the GitHub Actions water-point builder push stage progress to
  a herder (text + rendered progress-bar image). Guarded by
  X-Build-Key == sha256(DATABASE_URL), a secret both the web service and the
  builder job already know, so no extra credential setup is required.
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
