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

# --- Text-to-speech (voice replies) ---
TTS_VOICES = {"swahili": "sw-KE-RafikiNeural", "english": "en-US-JennyNeural"}
TTS_OUTPUT_FORMAT = "ogg-24khz-16bit-mono-opus"  # OGG/Opus mono — WhatsApp-compatible

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


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


# Emoji/pictographs and bullet glyphs: WhatsApp shows them, a voice must not read
# them. "<" and ">" also cover flag/keycap emoji sequences.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF\uFE0F\u200d]",
    flags=re.UNICODE,
)
_BULLET_RE = re.compile(r"^\s*[•·\-\u2013\u2014\*\u25CF\u25AA]+\s*", flags=re.MULTILINE)

# Spoken forms. Units and symbols are the difference between "four point three k m"
# and a sentence a herder understands; "%"/"~" have no spoken form at all.
# NOTE the numeral group captures the WHOLE number: a pattern like (\d)\s*km matches
# only the last digit and turns "4.3 km" into "4.kilomita 3".
_NUM = r"(\d+(?:[.,]\d+)?)"
_SPEECH_UNIT_SW = [
    (re.compile(_NUM + r"\s*km\b"), r"\1 kilomita"),
    (re.compile(_NUM + r"\s*mm\b"), r"\1 milimita"),
    (re.compile(_NUM + r"\s*L\b"), r"\1 lita"),
    (re.compile(_NUM + r"\s*%"), r"asilimia \1"),
    (re.compile(_NUM + r"\s*[-\u2013]\s*" + _NUM), r"\1 hadi \2"),  # 30-45 -> 30 hadi 45
    (re.compile(r"\s*~\s*"), " takriban "),
]
_SPEECH_UNIT_EN = [
    (re.compile(_NUM + r"\s*km\b"), r"\1 kilometres"),
    (re.compile(_NUM + r"\s*mm\b"), r"\1 millimetres"),
    (re.compile(_NUM + r"\s*L\b"), r"\1 litres"),
    (re.compile(_NUM + r"\s*%"), r"\1 percent"),
    (re.compile(_NUM + r"\s*[-\u2013]\s*" + _NUM), r"\1 to \2"),
    (re.compile(r"\s*~\s*"), " about "),
]
# Latin abbreviations a Swahili voice reads as letters.
_SPEECH_WORDS_SW = [(re.compile(r"\bVCI\b"), "hali ya mimea"),
                    (re.compile(r"\bID\b"), "namba"),
                    (re.compile(r"\bCOG_READ_ERROR[^.]*\.?"), "")]


def speech_segments(text: str, language: str = "swahili") -> list[str]:
    """The written reply split into spoken segments (one per idea).

    Segments are the caller's chance to insert pauses: a voice note that reads four
    short segments with brief silences is far easier to follow than one continuous
    stream, which is exactly what herders complained about.
    """
    if not text:
        return []
    out = _EMOJI_RE.sub(" ", text)
    out = out.replace("*", "").replace("`", "")
    out = _BULLET_RE.sub("", out)
    raw_lines = [ln.strip() for ln in out.splitlines()]

    segments: list[str] = []
    for line in raw_lines:
        if not line:
            continue
        # Drop technical parentheticals (codes) but inline human ones: the
        # uncertainty markers are part of the advice, so "(makadirio)" becomes
        # "makadirio" rather than disappearing from audio only.
        line = re.sub(r"\(\s*COG_READ_ERROR[^)]*\)", "", line)
        line = re.sub(r"\(\s*([^)]{1,40})\)", r", \1,", line)
        line = re.sub(r"\s+([.,])", r"\1", line)
        line = re.sub(r",\s*,", ",", line)
        line = re.sub(r",+\s*([.!?])", r"\1", line)     # tidy ",." left by inlining
        line = re.sub(r"\s{2,}", " ", line).strip(" ,")
        if not line:
            continue
        # A line is a sentence: make sure it ends like one.
        if line[-1] not in ".!?":
            line += "."
        line = re.sub(r"([.!?])\1+", r"\1", line)
        segments.append(line)
    if not segments:
        return []

    rules = _SPEECH_UNIT_SW if language == "swahili" else _SPEECH_UNIT_EN
    cleaned: list[str] = []
    for segment in segments:
        for pattern, repl in rules:
            segment = pattern.sub(repl, segment)
        if language == "swahili":
            for pattern, repl in _SPEECH_WORDS_SW:
                segment = pattern.sub(repl, segment)
        cleaned.append(re.sub(r"\s{2,}", " ", segment).strip(" ,"))
    return [s for s in cleaned if s]


def speech_text(text: str, language: str = "swahili") -> str:
    """Turn a written reply into something a person would SAY aloud.

    Why this exists: a herder reported that voice notes were hard to follow. The
    causes were mechanical — emoji and bullet glyphs being read out, "km"/"mm"/"~"
    spoken as letters, ASCII line joining sentences together, and the old code
    deleting parentheticals (which silently removed "(makadirio)" from audio only).

    What it does:
      * drops emoji, bullets and markdown (never content),
      * expands units, ranges and symbols into spoken words,
      * keeps LINE STRUCTURE as sentence boundaries, adding a full stop where a line
        ended without one, so the voice does not run two ideas together,
      * collapses whitespace and stray punctuation.
    """
    return " ".join(speech_segments(text, language))


def synthesize_speech(text: str, language: str = "swahili") -> bytes | None:
    """Synthesize a short reply to OGG/Opus via Azure Neural TTS (fail-open).

    The returned bytes are ready to upload to WhatsApp as an audio message.
    Returns None on any failure so callers fall back to text.
    """
    settings = get_settings()
    if not settings.azure_speech_key or not settings.azure_speech_region:
        return None
    segments = speech_segments(text, language)
    if not segments:
        return None
    voice = TTS_VOICES.get(language, "sw-KE-RafikiNeural")
    xml_lang = "sw-KE" if voice.startswith("sw-") else "en-US"
    # Short pauses BETWEEN segments: the advisory is several ideas, and reading them
    # as one breathless stream is what made voice notes hard to follow.
    body = "<break time='300ms'/>".join(_xml_escape(s) for s in segments)
    ssml = (
        f"<speak version='1.0' xml:lang='{xml_lang}'>"
        f"<voice name='{voice}'>{body}</voice></speak>"
    )
    try:
        resp = httpx.post(
            f"https://{settings.azure_speech_region}.tts.speech.microsoft.com"
            "/cognitiveservices/v1",
            content=ssml,
            headers={
                "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": TTS_OUTPUT_FORMAT,
                "User-Agent": "ardalink-piosphere",
            },
            timeout=45,
        )
    except Exception:  # noqa: BLE001
        log.exception("Azure TTS request failed - falling back to text")
        return None
    if resp.status_code != 200:
        log.warning("Azure TTS returned %s: %.160s", resp.status_code, resp.text)
        return None
    return resp.content or None


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
