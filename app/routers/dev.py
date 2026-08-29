"""Debug/ops endpoints for the team — not for public use.

The transcription endpoint lets us test voice notes (including accented
speech) without going through WhatsApp: POST raw audio bytes and get the
transcript back. Guarded by X-Debug-Key == WHATSAPP_VERIFY_TOKEN.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import get_settings
from app.services import speech

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
