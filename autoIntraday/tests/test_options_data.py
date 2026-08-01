"""Option-chain derivatives — expiry maths, PCR/max-pain arithmetic, graceful unavailability."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from options_data import fetch, summarise

# Groww's real shape, verified live 2026-08-01: strikes is a DICT keyed by strike, and the
# underlying price rides along as underlying_ltp.
_CHAIN = {"underlying_ltp": 105.0, "strikes": {
    "90": {"CE": {"open_interest": 100, "volume": 3}, "PE": {"open_interest": 700, "volume": 30}},
    "100": {"CE": {"open_interest": 500, "volume": 10}, "PE": {"open_interest": 100, "volume": 5}},
    "110": {"CE": {"open_interest": 900, "volume": 20}, "PE": {"open_interest": 50, "volume": 2}},
}}

# The list form is also accepted so a payload change does not break this outright.
_CHAIN_LIST = {"option_chain": [
    {"strike_price": 90, "CE": {"open_interest": 100}, "PE": {"open_interest": 700}},
    {"strike_price": 100, "CE": {"open_interest": 500}, "PE": {"open_interest": 100}},
    {"strike_price": 110, "CE": {"open_interest": 900}, "PE": {"open_interest": 50}},
]}


def test_expiry_comes_from_the_instrument_master_not_a_computed_rule():
    """The last-Thursday rule is WRONG for 2026 — NSE moved the F&O expiry day, so RELIANCE's
    August expiry is Tuesday the 25th, not Thursday the 27th. A guessed date returns an EMPTY
    chain rather than an error, which reads as 'no open interest'."""
    from instrument_master import next_option_expiry, option_expiries
    exps = option_expiries("RELIANCE")
    assert exps and all(e[:2] == "20" for e in exps)
    assert next_option_expiry("RELIANCE", "1900-01-01") == exps[0]
    assert option_expiries("NOT_AN_FNO_NAME_XYZ") == []
    assert next_option_expiry("NOT_AN_FNO_NAME_XYZ") is None


def test_both_dict_and_list_chain_shapes_parse():
    assert summarise(_CHAIN)["strikes_analysed"] == 3
    assert summarise(_CHAIN_LIST)["strikes_analysed"] == 3
    assert summarise(_CHAIN)["max_pain"] == summarise(_CHAIN_LIST)["max_pain"]


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
    scan's options dimension must be nulled and renormalised, not scored as bearish. The live
    gateway returned exactly {"underlying_ltp": ..., "strikes": {}} for a wrong expiry."""
    for bad in ({}, {"option_chain": []}, {"junk": 1}, {"underlying_ltp": 1307.8, "strikes": {}}):
        out = summarise(bad)
        assert out["available"] is False and "note" in out
        assert "total_call_oi" not in out


def test_strikes_with_no_open_interest_are_ignored():
    chain = {"strikes": {"100": {"CE": {"open_interest": 0}, "PE": {"open_interest": 0}}}}
    assert summarise(chain)["available"] is False


def test_spot_is_taken_from_the_chain_itself():
    """underlying_ltp rides along with the chain, so no second quote call is needed — and a quote
    can fail out of hours while the chain still returns, leaving bearish_tilt uncomputed."""
    class _NoQuote:
        def get_quote(self, s): raise RuntimeError("market closed")
        def get_option_chain(self, u, e): return _CHAIN

    out = fetch("RELIANCE", "2026-08-25", client=_NoQuote())
    assert out["spot"] == 105.0 and out["bearish_tilt"] is True


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
