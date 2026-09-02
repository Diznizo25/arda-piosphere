"""Test the production WhatsApp webhook with a signed payload.

Builds a fake inbound text message, signs it with the app secret (HMAC-SHA256,
x-hub-signature-256), POSTs to the production webhook, and reports the response.
A 200 means message processing completed without crashing; then the only place
left to check is Meta's delivery to our callback (subscription/token).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    secret = os.getenv("WHATSAPP_APP_SECRET", "")
    if not secret:
        print("WHATSAPP_APP_SECRET not set in .env — cannot sign")
        sys.exit(1)
    base = "https://arda-piosphere.onrender.com"
    body = json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{
            "id": os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "test"),
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "test"},
                    "messages": [{
                        "from": sys.argv[2] if len(sys.argv) > 2 else "+254700000001",
                        "id": "wamid.test123",
                        "timestamp": "1700000000",
                        "type": "text",
                        "text": {"body": sys.argv[1] if len(sys.argv) > 1 else "menu"},
                    }],
                },
                "field": "messages",
            }],
        }],
    }).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = httpx.post(base + "/webhooks/whatsapp", content=body,
                   headers={"Content-Type": "application/json",
                            "X-Hub-Signature-256": sig},
                   timeout=90)
    print("webhook:", r.status_code, r.text[:300])


if __name__ == "__main__":
    main()
