"""Azure Speech-to-Text for WhatsApp voice notes (fail-open).

WhatsApp delivers voice notes as OGG/Opus. The Azure Speech short-form REST
endpoint accepts OGG/Opus bytes directly (verified empirically — same
transcript as an equivalent WAV), so no ffmpeg conversion is needed.

Accent handling: Kenyan pastoralists speak Swahili, English, or a mix. We run
recognition in the herder's preferred language AND the other language, then
return the higher-confidence transcript — the wrong-language pass scores very
low (0.1-0.2) on accented speech, while the right locale stays high (0.9).
"""
from __future__ import annotations

import logging
import re
import struct
from dataclasses import dataclass

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

TIMEOUT_S = 45.0

# Azure Speech locale per herder language preference.
LOCALES = {"swahili": "sw-KE", "english": "en-KE"}

# Fallback for the English pass when the Kenyan-English model is not enabled on
# this Speech resource (en-KE needs the HD/standard feature); en-US is a safe
# generic fallback.
LOCALES_FALLBACK = {"english": "en-US"}

OGG_CONTENT_TYPE = "audio/ogg; codecs=opus"
OPUS_SAMPLE_RATE = 48000
# Azure short-form REST recognition supports up to 60 s of audio.
MAX_DURATION_S = 60.0

_LANG_TO_LOCALE: dict[str, str] = {}


def _locales_for(language: str) -> list[str]:
    """Locales to try, in order: the herder's language first, then the other
    language, with a generic fallback. The confidence pick decides the winner,
    so ordering here only affects which pass runs first."""
    primary = LOCALES.get(language, "sw-KE")  # sw-KE or en-KE
    if primary == "en-KE":
        return [primary, LOCALES_FALLBACK["english"], LOCALES["swahili"]]
    return [primary, LOCALES["english"], LOCALES_FALLBACK["english"]]


@dataclass
class Transcription:
    text: str
    language: str  # Azure locale actually used for the winning pass
    confidence: float
    duration_s: float


def ogg_duration_seconds(data: bytes) -> float:
    """Duration of an OGG/Opus stream from its page granule positions.

    The last OGG page's granule position is the total number of PCM samples at
    the Opus 48 kHz clock. Returns 0.0 when the container can't be parsed.
    """
    if len(data) < 28 or data[:4] != b"OggS":
        return 0.0
    granule = 0
    pos = 0
    while pos + 27 <= len(data):
        if data[pos:pos + 4] != b"OggS":
            pos += 1
            continue
        granule = struct.unpack("<Q", data[pos + 6:pos + 14])[0]
        nsegs = data[pos + 26]
        pos += 27 + nsegs
    return granule / OPUS_SAMPLE_RATE if granule else 0.0


def _clean(text: str) -> str:
    """Normalize the transcript so keyword matching in the text handler works
    (strip the no-break space Azure emits, collapse runs of punctuation)."""
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _stt_pass(audio: bytes, locale: str) -> tuple[str, float] | None:
    settings = get_settings()
    if not settings.azure_speech_key or not settings.azure_speech_region:
        return None
    url = (
        f"https://{settings.azure_speech_region}.stt.speech.microsoft.com"
        "/speech/recognition/conversation/cognitiveservices/v1"
    )
    try:
        resp = httpx.post(
            url,
            params={"language": locale, "format": "detailed", "profanity": "raw"},
            content=audio,
            headers={
                "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
                "Content-Type": OGG_CONTENT_TYPE,
                "Accept": "application/json;text/xml",
            },
            timeout=TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001
        log.exception(f"Azure STT request failed (locale={locale}) - skipping pass")
        return None
    if resp.status_code != 200:
        log.warning("Azure STT %s -> %s: %.160s", locale, resp.status_code, resp.text)
        return None
    data = resp.json()
    nbest = data.get("NBest") or []
    if not nbest:
        return None
    text = (data.get("DisplayText") or nbest[0].get("Display") or "").strip()
    try:
        confidence = float(nbest[0].get("Confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if not text:
        return None
    return text, confidence


def transcribe_voice_note(audio: bytes, language: str = "swahili") -> Transcription | None:
    """Transcribe a WhatsApp voice note, trying the herder's language first.

    Returns None when every pass fails (missing key, timeout, non-200, empty).
    """
    duration = ogg_duration_seconds(audio)
    if duration > MAX_DURATION_S:
        log.warning("Voice note too long: %.1fs (>%ss)", duration, MAX_DURATION_S)
        return Transcription(text="", language="", confidence=0.0, duration_s=duration)

    best: tuple[float, str, str] | None = None
    for locale in _locales_for(language):
        result = _stt_pass(audio, locale)
        if result is None:
            continue
        text, confidence = result
        if best is None or confidence > best[0]:
            best = (confidence, text, locale)

    if best is None:
        return None

    confidence, text, locale = best
    log.info("Voice note transcribed [%s] conf=%.2f dur=%.1fs: %r",
             locale, confidence, duration, text)
    return Transcription(text=_clean(text), language=locale,
                         confidence=confidence, duration_s=duration)
