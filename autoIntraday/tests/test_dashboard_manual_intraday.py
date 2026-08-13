"""The Analyze page's pure helpers: which rows got ticked, which levels a skill actually
produced, and how a result renders as a card."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Importing dashboard runs load_settings(), which loads .env (Groww creds) into os.environ —
# and that leaks into tests that assert credentials are ABSENT. Import, then restore.
_env_before = dict(os.environ)
import dashboard  # noqa: E402

os.environ.clear()
os.environ.update(_env_before)

R = {"symbol": "KEI", "status": "DONE", "verdict": "BUY NOW", "conviction": 78,
     "entry": 1842.0, "stop": 1808.0, "target1": 1905.0, "target2": 1960.0, "target3": None,
     "risk_reward": 1.9, "summary": "Reclaimed VWAP on 2.4x RVOL",
     "report": "## Step 0\nGate open.", "raw_json": '{"cost": 0.14}', "error": None}


def test_selected_symbols_picks_only_ticked_rows():
    rows = [{"Analyze": True, "Symbol": "KEI"}, {"Analyze": False, "Symbol": "TCS"},
            {"Analyze": True, "Symbol": "PNGSREVA"}]
    assert dashboard._selected_symbols(rows) == ["KEI", "PNGSREVA"]


def test_selected_symbols_empty_when_nothing_ticked():
    assert dashboard._selected_symbols([{"Analyze": False, "Symbol": "KEI"}]) == []


def test_selected_symbols_tolerates_missing_key():
    assert dashboard._selected_symbols([{"Symbol": "KEI"}]) == []


def test_levels_omit_what_the_skill_did_not_give():
    labels = [lbl for lbl, _ in dashboard._analysis_levels(R)]
    assert "Entry" in labels and "T2" in labels
    assert "T3" not in labels          # null target3 is unknown, not a row of dashes


def test_levels_empty_for_a_scanner_with_no_prices():
    bare = dict(R, entry=None, stop=None, target1=None, target2=None, target3=None,
                risk_reward=None)
    assert dashboard._analysis_levels(bare) == []


def test_card_shows_verdict_conviction_and_summary():
    h = dashboard._analysis_card(R)
    assert "BUY NOW" in h and "78" in h and "Reclaimed VWAP" in h
    assert "1,842" in h or "1842" in h


def test_card_escapes_hostile_text():
    h = dashboard._analysis_card(dict(R, summary="<script>alert(1)</script>"))
    assert "<script>" not in h


def test_card_for_an_errored_row_reports_the_error():
    h = dashboard._analysis_card(dict(R, status="ERROR", verdict=None,
                                      error="tool timeout"))
    assert "tool timeout" in h


def test_page_is_registered_in_navigation():
    src = open(dashboard.__file__, encoding="utf-8").read()
    assert 'url_path="manual-intraday"' in src and "_manual_intraday_page" in src
    assert 'title="Manual Intraday"' in src


# ---- order ticket -----------------------------------------------------------------------

def test_ticket_defaults_come_from_the_result():
    d = dashboard._ticket_defaults(R, capital=30000.0)
    assert d["side"] == "LONG"                 # from the BUY NOW verdict
    assert d["entry"] == 1842.0 and d["stop"] == 1808.0
    assert d["quantity"] == 16                 # floor(30000 / 1842)


def test_ticket_target_comes_from_target1():
    d = dashboard._ticket_defaults(R, capital=30000.0)
    assert d["target"] == R["target1"]


def test_ticket_defaults_leave_side_blank_on_an_exit_verdict():
    """EXIT means close what you hold, not open a short — the operator must choose."""
    d = dashboard._ticket_defaults(dict(R, verdict="EXIT"), capital=30000.0)
    assert d["side"] is None


def test_ticket_defaults_survive_a_result_with_no_levels():
    bare = dict(R, entry=None, stop=None, target1=None)
    d = dashboard._ticket_defaults(bare, capital=30000.0)
    assert d["entry"] is None and d["quantity"] == 0


# ---- page structure helpers -------------------------------------------------------------

import pytest


@pytest.mark.parametrize("verdict", ["BUY NOW", "BUY ON PULLBACK", "IGNITION_BUY", "ADD",
                                     "MARKET EXCEPTION LONG"])
def test_entry_verdicts_tone_good(verdict):
    assert dashboard._verdict_tone(verdict) == "good"


@pytest.mark.parametrize("verdict", ["SHORT NOW", "SELL NOW", "EXIT", "REDUCE"])
def test_exit_and_short_verdicts_tone_bad(verdict):
    """EXIT/REDUCE are red because they instruct you to get OUT, not to stand still."""
    assert dashboard._verdict_tone(verdict) == "bad"


@pytest.mark.parametrize("verdict", ["WAIT", "HOLD", "NO TRADE", "", None])
def test_standstill_verdicts_tone_flat(verdict):
    assert dashboard._verdict_tone(verdict) == "flat"


def test_card_carries_the_tone_class():
    assert "ai-tone-good" in dashboard._analysis_card(R)
    assert "ai-tone-bad" in dashboard._analysis_card(dict(R, verdict="SHORT NOW"))
    assert "ai-tone-flat" in dashboard._analysis_card(dict(R, verdict="WAIT"))


def test_ticket_opens_only_for_an_actionable_entry():
    assert dashboard._ticket_open_default("BUY NOW") is True
    assert dashboard._ticket_open_default("SHORT NOW") is True
    assert dashboard._ticket_open_default("WAIT") is False
    assert dashboard._ticket_open_default("EXIT") is False


_RUN = {"id": 3, "status": "RUNNING", "skill_id": "intraday-analyst-2", "mode": "symbols"}
_PROG = {"done": 3, "total": 5, "errors": 1, "pending": 1, "analyzing": 1}


def _o(status, oid=1):
    return {"id": oid, "status": status, "symbol": "KEI"}


def test_strip_facts_summarises_positions_and_run():
    f = dashboard._strip_facts([{"symbol": "KEI"}, {"symbol": "BSE"}], "2026-08-12T04:00:00Z",
                               _RUN, _PROG, [])
    assert f["positions"] == 2 and f["fetched_at"] == "2026-08-12T04:00:00Z"
    assert f["run_status"] == "RUNNING" and f["run_skill"] == "intraday-analyst-2"
    assert f["done"] == 3 and f["total"] == 5 and f["errors"] == 1


def test_strip_facts_with_no_run_at_all():
    f = dashboard._strip_facts([], None, None, {"done": 0, "total": 0, "errors": 0}, [])
    assert f["positions"] == 0 and f["run_status"] is None and f["run_skill"] is None
    assert f["attention"] == 0


def test_strip_facts_counts_unprotected_and_errored():
    """FILLED means the entry filled but the OCO did NOT arm — a live, unprotected position."""
    orders = [_o("ARMED", 1), _o("FILLED", 2), _o("ERROR", 3), _o("FILLED", 4)]
    f = dashboard._strip_facts([], None, None, _PROG, orders)
    assert len(f["unprotected"]) == 2 and len(f["errored"]) == 1
    assert f["attention"] == 3


def test_strip_facts_does_not_flag_rejected_or_closed():
    """A REJECTED order never reached the market and a CLOSED one is done — neither is urgent."""
    orders = [_o("REJECTED", 1), _o("CLOSED", 2), _o("ARMED", 3), _o("ENTRY_PENDING", 4)]
    f = dashboard._strip_facts([], None, None, _PROG, orders)
    assert f["attention"] == 0


def test_orders_for_display_pins_attention_first():
    orders = [_o("ARMED", 1), _o("CLOSED", 2), _o("FILLED", 3), _o("REJECTED", 4),
              _o("ERROR", 5)]
    out = dashboard._orders_for_display(orders)
    assert [o["id"] for o in out[:2]] == [3, 5]          # FILLED and ERROR pinned
    assert [o["id"] for o in out[2:]] == [1, 2, 4]       # rest keep their order


def test_orders_for_display_loses_nothing():
    orders = [_o(s, i) for i, s in enumerate(
        ["ARMED", "FILLED", "CLOSED", "ERROR", "REJECTED", "ENTRY_PENDING", "PLACING"])]
    out = dashboard._orders_for_display(orders)
    assert sorted(o["id"] for o in out) == sorted(o["id"] for o in orders)
    assert len(out) == len(orders)


def test_orders_for_display_handles_empty():
    assert dashboard._orders_for_display([]) == []


def test_page_uses_url_persisted_tabs():
    src = open(dashboard.__file__, encoding="utf-8").read()
    assert '_url_tabs("mi"' in src
    for label in ('"Positions"', '"Results"', '"Orders"', '"Settings"'):
        assert label in src


def test_mode_pill_distinguishes_live_from_paper():
    assert "ai-mode-live" in dashboard._mode_pill("live")
    assert "LIVE" in dashboard._mode_pill("live")
    assert "ai-mode-paper" in dashboard._mode_pill("paper")


from datetime import datetime, timedelta, timezone


def test_fmt_duration_scales_its_units():
    assert dashboard._fmt_duration(45) == "45s"
    assert dashboard._fmt_duration(100) == "1m40s"
    assert dashboard._fmt_duration(700) == "11m"
    assert dashboard._fmt_duration(3900) == "1h 5m"
    assert dashboard._fmt_duration(-5) == "0s"


def test_age_seconds_and_its_unknowns():
    now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    assert dashboard._age_seconds("2026-08-13T09:58:00+00:00", now) == 120
    assert dashboard._age_seconds(None, now) is None
    assert dashboard._age_seconds("not a date", now) is None


def test_pid_alive_for_this_process_and_a_dead_one():
    import os
    assert dashboard._pid_alive(os.getpid()) is True
    assert dashboard._pid_alive(None) is False
    assert dashboard._pid_alive(0) is False
    assert dashboard._pid_alive("nonsense") is False
    assert dashboard._pid_alive(999999) is False


_NOW = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


def _run(status="RUNNING", mode="symbols", started="2026-08-13T09:50:00+00:00"):
    return {"id": 1, "status": status, "mode": mode, "skill_id": "intraday-analyst-2",
            "started_at": started, "pid": 123}


def test_run_health_ok_when_progressing():
    fresh = "2026-08-13T09:59:00+00:00"
    assert dashboard._run_health(_run(), fresh, _NOW, alive=True) == "ok"


def test_run_health_stalled_when_nothing_finished_recently():
    old = "2026-08-13T09:50:00+00:00"          # 10 minutes ago
    assert dashboard._run_health(_run(), old, _NOW, alive=True) == "stalled"


def test_run_health_dead_when_the_process_is_gone():
    assert dashboard._run_health(_run(), "2026-08-13T09:59:00+00:00", _NOW,
                                 alive=False) == "dead"


def test_run_health_ok_for_a_finished_run():
    assert dashboard._run_health(_run(status="SUCCESS"), None, _NOW, alive=False) == "ok"
    assert dashboard._run_health(None, None, _NOW, alive=False) == "ok"


def test_run_health_falls_back_to_started_at_before_anything_finishes():
    """A run with no completions yet is judged from when it started, not called stalled."""
    just_started = _run(started="2026-08-13T09:59:00+00:00")
    assert dashboard._run_health(just_started, None, _NOW, alive=True) == "ok"


def test_progress_text_for_symbols_has_counts_and_time():
    t = dashboard._run_progress_text(_run(), {"done": 3, "total": 5, "errors": 0}, 360)
    assert "3/5" in t and "6m" in t and "left" in t


def test_progress_text_mentions_errors_when_present():
    t = dashboard._run_progress_text(_run(), {"done": 3, "total": 5, "errors": 1}, 360)
    assert "1 errors" in t or "1 error" in t


def test_progress_text_for_top5_has_no_meaningless_fraction():
    t = dashboard._run_progress_text(_run(mode="top5"), {"done": 0, "total": 0, "errors": 0},
                                     100)
    assert "single call" in t and "0/0" not in t and "1m40s" in t


def test_progress_text_drops_the_estimate_once_everything_is_done():
    t = dashboard._run_progress_text(_run(), {"done": 5, "total": 5, "errors": 0}, 600)
    assert "left" not in t


def test_friendly_error_recognises_a_groww_auth_failure():
    head, raw = dashboard._friendly_error(RuntimeError("401 Unauthorized: invalid api key"))
    assert "credential" in head.lower() or ".env" in head
    assert "401" in raw


def test_friendly_error_recognises_margin_and_network():
    head, _ = dashboard._friendly_error(RuntimeError("insufficient margin for this order"))
    assert "margin" in head.lower()
    head, _ = dashboard._friendly_error(RuntimeError("Connection timed out"))
    assert "reach" in head.lower() or "network" in head.lower()


def test_friendly_error_passes_a_manual_broker_message_through():
    from manual_broker import ManualBrokerError
    msg = "could not cancel the resting OCO OCO1 — check the broker."
    head, raw = dashboard._friendly_error(ManualBrokerError(msg))
    assert head == msg and raw == msg          # already written for a human


def test_friendly_error_falls_back_without_losing_the_raw_text():
    head, raw = dashboard._friendly_error(ValueError("weird internal thing"))
    assert head and "weird internal thing" in raw


def test_inflight_orders_are_the_ones_still_working():
    orders = [_o("PLACING", 1), _o("ENTRY_PENDING", 2), _o("ARMED", 3), _o("FILLED", 4),
              _o("CLOSED", 5)]
    assert [o["id"] for o in dashboard._inflight_orders(orders)] == [1, 2]
    assert dashboard._inflight_orders([]) == []


def test_every_order_status_has_an_explanation():
    for status in ("PLACING", "ENTRY_PENDING", "FILLED", "ARMED", "REJECTED", "CLOSED",
                   "ERROR"):
        assert dashboard._MANUAL_STATUS_HELP[status].strip()
