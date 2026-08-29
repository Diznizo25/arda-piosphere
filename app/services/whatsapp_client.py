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


def download_media(media_id: str) -> bytes | None:
    """Download a WhatsApp media object (e.g. a voice note) as raw bytes.

    Two-step Graph API call: resolve the media id to a temporary download URL,
    then fetch that URL with the same bearer token. Returns None on any failure
    so callers can fail open.
    """
    settings = get_settings()
    auth = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    try:
        info_resp = httpx.get(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/"
            f"{settings.whatsapp_phone_number_id}/media/{media_id}",
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
