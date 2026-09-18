"""Water-status logic: one-tap replies, staleness, and guidance gating.

Ported from scripts/test_water_status.py. This is the safety gate that stops
the system sending a herd to water another herder has reported dead.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import water_reach as wr
from app.services import water_status as ws

NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)


def days_ago(n: float) -> datetime:
    return NOW - timedelta(days=n)


# --- one-tap replies ---------------------------------------------------------

@pytest.mark.parametrize("digit,intent", [
    ("1", "water_available"), (" 2 ", "water_dry"), ("3", "water_not_found"),
    ("4", "water_broken"), ("5", "water_intermittent"),
])
def test_digit_quick_replies(digit, intent):
    assert ws.intent_for_digit(digit) == intent


def test_a_herd_size_is_not_a_water_report():
    """A herder answering "12" about herd size must not silently mark water dry."""
    assert ws.intent_for_digit("12") is None
    assert ws.intent_for_digit("maji yapo") is None


# --- report -> status --------------------------------------------------------

@pytest.mark.parametrize("report,status", [
    ("water_dry", ws.STATUS_DRY),
    ("water_available", ws.STATUS_FUNCTIONAL),
    ("water_broken", ws.STATUS_BROKEN),
    ("water_not_found", ws.STATUS_NOT_FOUND),
])
def test_report_maps_to_status(report, status):
    assert ws.status_for_report(report) == status


def test_pasture_reports_do_not_set_a_water_status():
    assert ws.status_for_report("pasture_good") is None


# --- gating ------------------------------------------------------------------

@pytest.mark.parametrize("status,when", [
    (ws.STATUS_DRY, days_ago(2)),
    (ws.STATUS_BROKEN, days_ago(30)),
    (ws.STATUS_NOT_FOUND, days_ago(59)),
])
def test_recently_bad_points_are_not_recommended(status, when):
    assert not ws.usable_for_guidance(status, when, now=NOW)


def test_a_bad_point_returns_after_the_staleness_window():
    """A pan refills and a pump gets repaired. Disappearing water is worse
    than unverified water."""
    assert ws.usable_for_guidance(ws.STATUS_DRY, days_ago(61), now=NOW)


def test_good_and_unknown_points_stay_usable():
    assert ws.usable_for_guidance(ws.STATUS_FUNCTIONAL, days_ago(1), now=NOW)
    assert ws.usable_for_guidance(ws.STATUS_UNKNOWN, None, now=NOW)


# --- "confirm before you go" flag --------------------------------------------

def test_needs_check_flags():
    assert ws.needs_check(ws.STATUS_DRY, days_ago(5), now=NOW)
    assert not ws.needs_check(ws.STATUS_FUNCTIONAL, days_ago(5), now=NOW)
    assert ws.needs_check(ws.STATUS_FUNCTIONAL, days_ago(90), now=NOW)


def test_never_confirmed_always_warns():
    assert ws.needs_check(ws.STATUS_UNKNOWN, None, now=NOW)


def test_a_recent_herder_confirmation_counts_without_a_status():
    assert ws.needs_check(ws.STATUS_UNKNOWN, None, days_ago(3), now=NOW) is False


# --- wording -----------------------------------------------------------------

def test_status_wording_both_languages():
    assert "imekauka" in ws.status_label(ws.STATUS_DRY, "swa")
    assert "dry" in ws.status_label(ws.STATUS_DRY, "eng")
    q = ws.check_question("swa")
    assert "1 maji yapo" in q and "imekauka" in q
    assert "Asante" in ws.thanks_for_report("water_dry", "swa")
    assert "Recorded" in ws.thanks_for_report("water_dry", "eng")


# --- the gate must actually be in the SQL ------------------------------------

@pytest.mark.parametrize("name", ["NEAREST_REACHABLE_SQL", "NEAREST_REACHABLE_DIST_SQL"])
def test_guidance_sql_carries_the_status_gate(name):
    """Guard against the gate being refactored out of the query that matters."""
    sql = getattr(wr, name)
    assert "ws.status in ('dry','broken','not_found')" in sql
    assert "%(stale_days)s" in sql
    assert "status_updated_at" in sql


def test_nearby_list_exposes_status():
    assert "status" in wr.NEARBY_SQL and "status_updated_at" in wr.NEARBY_SQL
