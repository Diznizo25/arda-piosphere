"""Thin wrapper around the WhatsApp Business Cloud API (Meta Graph API)."""
from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

GRAPH_API_VERSION = "v20.0"


def _base_url() -> str:
    settings = get_settings()
    return f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings.whatsapp_phone_number_id}/messages"


def _headers() -> dict:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }


def send_text(to: str, body: str) -> None:
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    _post(payload)


def send_image_bytes_url(to: str, image_url: str, caption: str | None = None) -> None:
    """Send an image already reachable at a public URL (e.g. the generated
    map image, uploaded to R2/CDN first)."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"link": image_url, **({"caption": caption} if caption else {})},
    }
    _post(payload)


def send_image(to: str, media_id: str, caption: str | None = None) -> None:
    """Send an image by referencing an uploaded media id (e.g. the progress-bar
    PNG rendered by app/services/build_progress.py)."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"id": media_id, **({"caption": caption} if caption else {})},
    }
    _post(payload)


def upload_media(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str | None:
    """Upload an audio file to WhatsApp, returning its media id (fail-open).

    The media id is valid for 30 days and is referenced by send_audio(). Audio
    must be OGG/Opus mono (per WhatsApp's supported-media list) — which is
    exactly what synthesize_speech() produces.
    """
    settings = get_settings()
    try:
        resp = httpx.post(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/"
            f"{settings.whatsapp_phone_number_id}/media",
            headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
            data={"messaging_product": "whatsapp", "type": mime_type},
            files={"file": ("reply.ogg", audio_bytes, mime_type)},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["id"]
    except Exception:  # noqa: BLE001
        log.exception("WhatsApp media upload failed")
        return None


def send_audio(to: str, media_id: str) -> None:
    """Send an audio message by referencing an uploaded media id."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "audio",
        "audio": {"id": media_id},
    }
    _post(payload)


def send_quick_reply_buttons(to: str, body: str, buttons: list[tuple[str, str]]) -> None:
    """buttons: list of (id, title) pairs, max 3 per WhatsApp's interactive
    reply-button limits."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": bid, "title": title}}
                    for bid, title in buttons[:3]
                ]
            },
        },
    }
    _post(payload)


def send_interactive_list(to: str, body: str, button_text: str, rows: list[tuple[str, str]],
                          footer: str | None = None, title: str | None = None) -> None:
    """WhatsApp interactive LIST message (up to 10 rows) — used for the
    "which water point do your animals drink from?" picker. rows: (id, title),
    titles are truncated to ~24 chars and each row can carry a description
    (we pass ward names + distances as row titles)."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": title or ""},
            "body": {"text": body},
            "action": {
                "button": button_text,
                "sections": [
                    {
                        "title": title or "Chagua",
                        "rows": [
                            {"id": rid, "title": rtitle[:24]}
                            for rid, rtitle in rows[:10]
                        ],
                    }
                ],
            },
        },
    }
    if footer:
        payload["interactive"]["footer"] = {"text": footer}
    _post(payload)


def download_media(media_id: str) -> bytes | None:
    """Download a WhatsApp media object (e.g. a voice note) as raw bytes.

    Two-step Graph API call: resolve the media id to a temporary download URL
    (GET /{media-id} — the current documented pattern; the older
    /{phone-number-id}/media/{media-id} path returns "Unknown path components"),
    then fetch that URL with the same bearer token. Returns None on any failure
    so callers can fail open.
    """
    settings = get_settings()
    auth = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    try:
        info_resp = httpx.get(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}",
            headers=auth,
            timeout=15,
        )
        info_resp.raise_for_status()
        url = info_resp.json()["url"]
        media_resp = httpx.get(url, headers=auth, timeout=60)
        media_resp.raise_for_status()
        return media_resp.content
    except Exception:  # noqa: BLE001
        log.exception(f"Failed to download WhatsApp media {media_id}")
        return None


def _post(payload: dict) -> None:
    try:
        resp = httpx.post(_base_url(), json=payload, headers=_headers(), timeout=15)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        log.error(f"WhatsApp send failed: {e.response.status_code} {e.response.text}")
        raise
