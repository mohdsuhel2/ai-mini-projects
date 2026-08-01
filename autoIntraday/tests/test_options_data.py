"""Option-chain derivatives — expiry maths, PCR/max-pain arithmetic, graceful unavailability."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

from options_data import fetch, last_thursday, next_monthly_expiry, summarise

_CHAIN = {"option_chain": [
    {"strike_price": 90, "CE": {"open_interest": 100, "volume": 3},
     "PE": {"open_interest": 700, "volume": 30}},
    {"strike_price": 100, "CE": {"open_interest": 500, "volume": 10},
     "PE": {"open_interest": 100, "volume": 5}},
    {"strike_price": 110, "CE": {"open_interest": 900, "volume": 20},
     "PE": {"open_interest": 50, "volume": 2}},
]}


def test_last_thursday_is_the_nse_monthly_expiry():
    assert last_thursday(2026, 1) == date(2026, 1, 29)
    assert last_thursday(2026, 7) == date(2026, 7, 30)
    assert last_thursday(2026, 8) == date(2026, 8, 27)
    assert last_thursday(2026, 12) == date(2026, 12, 31)   # December wraps the year


def test_next_monthly_expiry_rolls_once_this_months_has_passed():
    assert next_monthly_expiry(date(2026, 8, 1)) == "2026-08-27"
    assert next_monthly_expiry(date(2026, 8, 27)) == "2026-08-27"   # expiry day itself still counts
    assert next_monthly_expiry(date(2026, 8, 28)) == "2026-09-24"
    assert next_monthly_expiry(date(2026, 12, 31)) == "2026-12-31"


def test_pcr_and_oi_walls():
    s = summarise(_CHAIN)
    assert s["available"] is True and s["strikes_analysed"] == 3
    assert s["total_call_oi"] == 1500 and s["total_put_oi"] == 850
    assert s["pcr_oi"] == 0.567                     # calls dominate -> bearish tilt
    assert s["call_wall"] == 110 and s["put_wall"] == 90


def test_max_pain_is_the_strike_of_least_total_payout():
    s = summarise(_CHAIN)
    assert s["max_pain"] == 100


def test_max_pain_below_spot_is_flagged_bearish():
    """Max pain below spot pulls price down into expiry; above it, the reverse."""
    assert summarise(_CHAIN, spot=105.0)["bearish_tilt"] is True
    assert summarise(_CHAIN, spot=105.0)["max_pain_vs_spot_pct"] == -4.76
    assert summarise(_CHAIN, spot=95.0)["bearish_tilt"] is False


def test_unrecognised_or_empty_chain_reports_unavailable_rather_than_zeros():
    """Zero OI and unknown OI are different signals. Never emit zeros for missing data — the
    scan's options dimension must be nulled and renormalised, not scored as bearish."""
    for bad in ({}, {"option_chain": []}, {"junk": 1}):
        out = summarise(bad)
        assert out["available"] is False and "note" in out
        assert "total_call_oi" not in out


def test_strikes_with_no_open_interest_are_ignored():
    chain = {"option_chain": [{"strike_price": 100, "CE": {"open_interest": 0},
                              "PE": {"open_interest": 0}}]}
    assert summarise(chain)["available"] is False


def test_fetch_never_raises_when_the_broker_is_unreachable():
    """One missing name must not abort a whole scan."""
    class _Dead:
        def get_quote(self, s): raise RuntimeError("gateway down")
        def get_option_chain(self, u, e): raise RuntimeError("gateway 404: Not Found")

    out = fetch("RELIANCE", "2026-08-27", client=_Dead())
    assert out["available"] is False
    assert out["symbol"] == "RELIANCE" and out["expiry"] == "2026-08-27"
    assert "404" in out["note"]


def test_fetch_summarises_through_an_injected_client():
    class _OK:
        def get_quote(self, s): return {"ltp": 105.0}
        def get_option_chain(self, u, e): return _CHAIN

    out = fetch("RELIANCE", "2026-08-27", client=_OK())
    assert out["available"] is True and out["spot"] == 105.0
    assert out["max_pain"] == 100 and out["bearish_tilt"] is True
