"""
Alert policy — the rules that decide whether a real phone buzzes.

An alerting system that is wrong, or merely noisy, is worse than none: it
teaches herders to ignore the channel, and the channel is the whole product.
Every suppression below exists because of a specific way that goes wrong, and
these tests are what stop the rules eroding.

`decide()` is pure, so all of this runs with no database and no network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import alerts
from app.services.alerts import (
    ALERT_COOLDOWN_DAYS,
    FREEFORM_WINDOW_HOURS,
    MAX_ALERTS_PER_HERDER_PER_DAY,
    compose_message,
    decide,
    event_key_for,
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
KEY = event_key_for("ws-1", "broken")


def row(**over) -> dict:
    """A herder who relies on a broken borehole and messaged an hour ago."""
    base = {
        "id": "herder-1",
        "phone_number": "+254700000001",
        "preferred_language": "swahili",
        "name": "Oldonyiro borehole",
        "water_type": "borehole",
        "status": "broken",
        "status_reports": 2,
        "status_updated_at": NOW - timedelta(hours=2),
        "last_inbound_at": NOW - timedelta(hours=1),
        "alerts_today": 0,
        "last_same_event": None,
    }
    return {**base, **over}


@pytest.fixture(autouse=True)
def _alerts_on(monkeypatch):
    """Alerts are OFF by default in production. Switch on for these tests."""
    monkeypatch.setenv("ALERTS_ENABLED", "1")


# --- the happy path ----------------------------------------------------------

def test_a_herder_who_relies_on_a_broken_point_is_told():
    d = decide(row(), event_key=KEY, reporter_id=None, now=NOW)
    assert d.status == "sent"
    assert d.channel == "freeform"
    assert "Oldonyiro borehole" in d.message


# --- suppressions, in the order they are applied -----------------------------

def test_the_reporter_is_never_told_their_own_report():
    """Echoing someone's own report back at them is always wrong, whatever the
    rest of the policy says — so this check comes first."""
    d = decide(row(), event_key=KEY, reporter_id="herder-1", now=NOW)
    assert d.status == "suppressed_self"


def test_off_by_default(monkeypatch):
    """The kill switch is an env var so it can be thrown without a deploy."""
    monkeypatch.delenv("ALERTS_ENABLED", raising=False)
    assert alerts.alerts_enabled() is False
    assert decide(row(), event_key=KEY, reporter_id=None, now=NOW).status == "suppressed_disabled"


def test_dry_run_works_even_with_the_kill_switch_off(monkeypatch):
    """The whole point of dry run is reviewing the blast radius BEFORE enabling."""
    monkeypatch.delenv("ALERTS_ENABLED", raising=False)
    d = decide(row(), event_key=KEY, reporter_id=None, now=NOW, dry_run=True)
    assert d.status == "dry_run"
    assert d.message is not None


def test_the_same_episode_is_not_repeated_inside_the_cooldown():
    recent = row(last_same_event=NOW - timedelta(days=ALERT_COOLDOWN_DAYS - 1))
    assert decide(recent, event_key=KEY, reporter_id=None, now=NOW).status == "suppressed_dedup"


def test_the_same_point_failing_again_later_is_a_new_event():
    """A pan refills and a pump gets repaired. Months later it breaking again is
    news, not a repeat."""
    old = row(last_same_event=NOW - timedelta(days=ALERT_COOLDOWN_DAYS + 1))
    assert decide(old, event_key=KEY, reporter_id=None, now=NOW).status == "sent"


def test_a_herder_is_not_buried():
    busy = row(alerts_today=MAX_ALERTS_PER_HERDER_PER_DAY)
    assert decide(busy, event_key=KEY, reporter_id=None, now=NOW).status == "suppressed_rate"


def test_outside_the_whatsapp_window_a_template_is_required():
    """WhatsApp only permits free-form within 24h of the herder's last message.
    We record the miss rather than dropping it silently — that count is the
    argument for submitting templates."""
    stale = row(last_inbound_at=NOW - timedelta(hours=FREEFORM_WINDOW_HOURS + 1))
    d = decide(stale, event_key=KEY, reporter_id=None, now=NOW)
    assert d.status == "suppressed_window"
    assert d.detail["needs"] == "template"


def test_a_herder_who_never_messaged_is_out_of_window():
    d = decide(row(last_inbound_at=None), event_key=KEY, reporter_id=None, now=NOW)
    assert d.status == "suppressed_window"


# --- honesty about evidence --------------------------------------------------

def test_a_single_report_is_sent_as_unconfirmed():
    """water_status.py: 'a single report from one herder is evidence, not truth'.
    We still send it — speed matters — but we never dress it up as confirmed."""
    d = decide(row(status_reports=1), event_key=KEY, reporter_id=None, now=NOW)
    assert d.status == "sent"
    assert d.detail["confirmed"] is False
    assert "haijathibitishwa" in d.message          # "not yet confirmed"


def test_corroborated_reports_say_so():
    d = decide(row(status_reports=3), event_key=KEY, reporter_id=None, now=NOW)
    assert d.detail["confirmed"] is True
    assert "Imethibitishwa" in d.message


# --- message content ---------------------------------------------------------

@pytest.mark.parametrize("language,must_contain", [
    ("swahili", "Usipeleke mifugo huko"),      # do not take your animals there
    ("english", "Do not take your animals"),
])
def test_every_alert_says_what_to_do(language, must_contain):
    msg = compose_message(language, "Oldonyiro borehole", "broken",
                          confirmed=True, reports=2)
    assert must_contain in msg


def test_an_unnamed_point_still_reads_sensibly():
    """A herder identifies water by local name; when we have none, fall back to
    the type rather than printing a uuid."""
    msg = compose_message("swahili", "kisima", "dry", confirmed=True, reports=2)
    assert "kisima" in msg
    assert "None" not in msg


def test_the_alert_offers_a_way_forward():
    """Being told your water is dead without an alternative is not help."""
    assert "maji" in compose_message("swahili", "x", "dry", True, 2)
    assert "water" in compose_message("english", "x", "dry", True, 2)


# --- what is alertable -------------------------------------------------------

def test_only_unusable_statuses_interrupt_anyone():
    assert "dry" in alerts.ALERTABLE_STATUSES
    assert "broken" in alerts.ALERTABLE_STATUSES
    assert "not_found" in alerts.ALERTABLE_STATUSES
    # A point that is real and sometimes dry is not news.
    assert "intermittent" not in alerts.ALERTABLE_STATUSES
    assert "functional" not in alerts.ALERTABLE_STATUSES


def test_good_news_does_not_trigger_a_broadcast():
    """notify_water_status_change short-circuits before touching the database."""
    assert alerts.notify_water_status_change("ws-1", "functional") == []
    assert alerts.notify_water_status_change("ws-1", "flowing") == []


def test_event_key_identifies_the_episode_not_the_moment():
    assert event_key_for("ws-1", "broken") == event_key_for("ws-1", "broken")
    assert event_key_for("ws-1", "broken") != event_key_for("ws-1", "dry")
    assert event_key_for("ws-1", "broken") != event_key_for("ws-2", "broken")
