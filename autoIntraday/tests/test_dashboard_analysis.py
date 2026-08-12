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
    assert 'url_path="analysis"' in src and "_analysis_page" in src
