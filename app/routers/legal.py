"""Simple legal pages (privacy policy / terms of service / data deletion) served
by the app itself, so the Meta WhatsApp app setup can point at real URLs
(Meta validates these when you publish the app).

These are short, honest documents — the service is a pastoral grazing advisory
delivered over WhatsApp, and the text below matches what the app actually does.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legal"])

# Meta's URL validator probes pages (some check with HEAD); make every legal
# page respond to both GET and HEAD so it never looks like a dead link.
_LEGAL_METHODS = ["GET", "HEAD"]

_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Arda Link</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;
line-height:1.55;color:#1f2937}}
h1{{color:#0f172a}} h2{{color:#0f172a;margin-top:28px}}
a{{color:#2563eb}} footer{{margin-top:40px;color:#6b7280;font-size:.85em}}
</style></head><body>
"""

_FOOT = """<footer>Arda Link — Piosphere Grazing Advisory &middot; Contact: ardalinkai@gmail.com
</footer></body></html>"""


@router.api_route("/privacy", methods=_LEGAL_METHODS, response_class=HTMLResponse)
def privacy_policy() -> str:
    body = """
<h1>Privacy Policy</h1>
<p><em>Last updated: August 2026</em></p>
<p>Arda Link ("we", "our") operates a pastoral grazing advisory service delivered
over WhatsApp. This policy explains what we collect, why, and your rights.</p>

<h2>What we collect</h2>
<ul>
  <li><strong>WhatsApp phone number</strong> — used to reply to you in the chat.</li>
  <li><strong>Location you choose to share</strong> — used to find the nearest
      water point and grazing conditions around you.</li>
  <li><strong>Livestock type</strong> (cattle, shoats, or camels) — used to give
      species-appropriate advice.</li>
  <li><strong>Language preference</strong> (Swahili or English).</li>
  <li><strong>Voice notes you send</strong> — transcribed to text by Azure Speech
      (Microsoft) to understand your request; the audio is not retained.</li>
  <li><strong>Optional feedback reports</strong> you send about water or pasture
      conditions — used to improve our advice.</li>
</ul>

<h2>How we use it</h2>
<p>We use this data only to provide and improve the advisory service: matching you
to nearby water, showing grazing conditions, and responding to your messages.
We do <strong>not</strong> sell or share your data with third parties for marketing.</p>

<h2>Where it is stored</h2>
<p>Data is stored in a PostgreSQL database (Supabase) and imagery in object storage
(Cloudflare R2). Messages are delivered through Meta's WhatsApp Business Platform,
which processes them according to Meta's own policies.</p>

<h2>Retention &amp; deletion</h2>
<p>We keep your profile while you use the service. To have your data deleted, reply
<strong>DELETE</strong> to the Arda Link WhatsApp number, or email
ardalinkai@gmail.com. Deletion is completed within 30 days.</p>

<h2>Contact</h2>
<p>Questions: ardalinkai@gmail.com</p>
"""
    return _HEAD.format(title="Privacy Policy") + body + _FOOT


@router.api_route("/terms", methods=_LEGAL_METHODS, response_class=HTMLResponse)
def terms_of_service() -> str:
    body = """
<h1>Terms of Service</h1>
<p><em>Last updated: August 2026</em></p>
<p>By using Arda Link you agree to these terms.</p>

<h2>The service</h2>
<p>Arda Link provides location-based information about nearby water points and
satellite-derived grazing conditions (vegetation indices such as NDVI, SATVI and VCI)
for cattle, shoats, and camels, delivered over WhatsApp.</p>

<h2>What the advice is not</h2>
<ul>
  <li>It is <strong>not</strong> veterinary, medical, or legal advice.</li>
  <li>Vegetation indices are <strong>estimates</strong> derived from satellite
      imagery and may not reflect conditions on the ground.</li>
  <li>Water points may be seasonal, unreliable, or dry. Always confirm conditions
      locally before relying on any information.</li>
</ul>

<h2>Your responsibilities</h2>
<p>You provide your location and livestock type voluntarily. You are responsible for
your decisions about livestock movement, water, and grazing. Never risk your or your
animals' safety on the basis of this service alone.</p>

<h2>Changes</h2>
<p>We may update these terms as the service evolves. Continued use after changes
means you accept the updated terms.</p>

<h2>Contact</h2>
<p>ardalinkai@gmail.com</p>
"""
    return _HEAD.format(title="Terms of Service") + body + _FOOT


@router.api_route("/data-deletion", methods=_LEGAL_METHODS, response_class=HTMLResponse)
def data_deletion() -> str:
    body = """
<h1>User Data Deletion Instructions</h1>
<p>Arda Link only holds the data you provide through WhatsApp (phone number,
shared location, livestock type, language preference, and any feedback you send).</p>
<p>To request deletion of your data:</p>
<ol>
  <li>Open your WhatsApp chat with Arda Link and reply <strong>DELETE</strong>, or</li>
  <li>Email <strong>ardalinkai@gmail.com</strong> from the email linked to your
      phone number (if available) and include the WhatsApp phone number.</li>
</ol>
<p>We will remove your profile and associated data within 30 days of a verified
request and confirm completion by WhatsApp or email.</p>
"""
    return _HEAD.format(title="Data Deletion") + body + _FOOT
