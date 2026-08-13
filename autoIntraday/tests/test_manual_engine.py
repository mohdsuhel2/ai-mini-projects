"""The generic manual skill runner: one schema every installed skill can fill, the full
report preserved, levels nullable, and no broker client anywhere near it."""
import json

import pytest

from manual_engine import ONE_SCHEMA, TOP5_SCHEMA, ManualEngineError, ManualSkillEngine


def _item(symbol="KEI", verdict="BUY NOW", **kw):
    base = {"symbol": symbol, "report": "## Step 0\nVIX 12.4, gate open.", "verdict": verdict,
            "conviction": 78, "entry": 1842.0, "stop": 1808.0, "target1": 1905.0,
            "target2": 1960.0, "target3": None, "risk_reward": 1.9,
            "summary": "Reclaimed VWAP on 2.4x RVOL"}
    base.update(kw)
    return base


def _envelope(payload: dict) -> str:
    return json.dumps({"type": "result", "is_error": False, "result": json.dumps(payload)})


@pytest.fixture
def skills_dir(tmp_path):
    d = tmp_path / "intraday-analyst-2"
    d.mkdir()
    (d / "SKILL.md").write_text("# INTRADAY ANALYST 2 SKILL body\n")
    return str(tmp_path)


def _engine(runner, skills_dir, **kw):
    return ManualSkillEngine("intraday-analyst-2", runner=runner, skills_dir=skills_dir, **kw)


def test_run_symbol_returns_item_with_report_and_levels(skills_dir):
    eng = _engine(lambda argv, text: (0, _envelope(_item()), ""), skills_dir)
    out = eng.run_symbol("KEI")
    assert out["symbol"] == "KEI" and out["verdict"] == "BUY NOW"
    assert out["entry"] == 1842.0 and out["target3"] is None
    assert out["report"].startswith("## Step 0")


def test_run_symbol_keeps_full_raw_stdout(skills_dir):
    env = _envelope(_item())
    eng = _engine(lambda argv, text: (0, env, ""), skills_dir)
    assert eng.run_symbol("KEI")["raw"] == env


def test_levels_may_all_be_null(skills_dir):
    """The overnight scanner gives a verdict with no levels — valid, not an error."""
    bare = _item(conviction=None, entry=None, stop=None, target1=None, target2=None,
                 target3=None, risk_reward=None)
    eng = _engine(lambda argv, text: (0, _envelope(bare), ""), skills_dir)
    out = eng.run_symbol("KEI")
    assert out["entry"] is None and out["conviction"] is None
    assert out["verdict"] == "BUY NOW"


def test_missing_report_raises(skills_dir):
    bad = _item()
    del bad["report"]
    eng = _engine(lambda argv, text: (0, _envelope(bad), ""), skills_dir)
    with pytest.raises(ManualEngineError):
        eng.run_symbol("KEI")


def test_missing_verdict_raises(skills_dir):
    bad = _item(verdict="")
    eng = _engine(lambda argv, text: (0, _envelope(bad), ""), skills_dir)
    with pytest.raises(ManualEngineError):
        eng.run_symbol("KEI")


def test_nonzero_exit_raises(skills_dir):
    eng = _engine(lambda argv, text: (2, "", "boom"), skills_dir)
    with pytest.raises(ManualEngineError):
        eng.run_symbol("KEI")


def test_missing_skill_file_raises(tmp_path):
    eng = ManualSkillEngine("nope", runner=lambda a, t: (0, "{}", ""),
                            skills_dir=str(tmp_path))
    with pytest.raises(ManualEngineError):
        eng.run_symbol("KEI")


def test_run_top5_returns_picks(skills_dir):
    payload = {"picks": [_item("KEI"), _item("PNGSREVA", verdict="WAIT")]}
    eng = _engine(lambda argv, text: (0, _envelope(payload), ""), skills_dir)
    out = eng.run_top5()
    assert [p["symbol"] for p in out] == ["KEI", "PNGSREVA"]
    assert out[1]["verdict"] == "WAIT"
    assert all(p["raw"] for p in out)


def test_run_top5_empty_is_valid(skills_dir):
    eng = _engine(lambda argv, text: (0, _envelope({"picks": []}), ""), skills_dir)
    assert eng.run_top5() == []


def test_run_top5_missing_picks_raises(skills_dir):
    eng = _engine(lambda argv, text: (0, _envelope({"nope": 1}), ""), skills_dir)
    with pytest.raises(ManualEngineError):
        eng.run_top5()


def test_position_context_reaches_the_model(skills_dir):
    seen = {}

    def runner(argv, text):
        seen["msg"] = text
        return 0, _envelope(_item()), ""
    eng = _engine(runner, skills_dir)
    eng.run_symbol("KEI", position={"quantity": 40, "avg_price": 1830.5, "product": "MIS"})
    assert "40" in seen["msg"] and "1830.5" in seen["msg"] and "MIS" in seen["msg"]


def test_flat_symbol_says_no_position(skills_dir):
    seen = {}

    def runner(argv, text):
        seen["msg"] = text
        return 0, _envelope(_item()), ""
    _engine(runner, skills_dir).run_symbol("KEI")
    assert "no open position" in seen["msg"].lower()


def test_skill_body_is_the_system_prompt(skills_dir):
    seen = {}

    def runner(argv, text):
        seen["argv"] = argv
        return 0, _envelope(_item()), ""
    _engine(runner, skills_dir).run_symbol("KEI")
    sysprompt = seen["argv"][seen["argv"].index("--append-system-prompt") + 1]
    assert "INTRADAY ANALYST 2 SKILL body" in sysprompt
    assert "MANUAL ANALYSIS MODE" in sysprompt


def test_allowlist_covers_data_tools_and_web_search(skills_dir):
    seen = {}

    def runner(argv, text):
        seen["argv"] = argv
        return 0, _envelope(_item()), ""
    _engine(runner, skills_dir).run_symbol("KEI")
    tools = seen["argv"][seen["argv"].index("--allowedTools") + 1]
    assert "WebSearch" in tools
    assert "StockAnalayze" in tools and "Bash(" in tools


def test_web_search_can_be_disabled(skills_dir):
    seen = {}

    def runner(argv, text):
        seen["argv"] = argv
        return 0, _envelope(_item()), ""
    _engine(runner, skills_dir, use_web_search=False).run_symbol("KEI")
    tools = seen["argv"][seen["argv"].index("--allowedTools") + 1]
    assert "WebSearch" not in tools


def test_schemas_are_wired():
    assert ONE_SCHEMA["properties"]["report"]["type"] == "string"
    assert TOP5_SCHEMA["properties"]["picks"]["maxItems"] == 5
    assert ONE_SCHEMA["properties"]["entry"]["type"] == ["number", "null"]


def test_module_imports_no_broker_client():
    """Structural safety: this runner has no code path to an order."""
    import manual_engine
    src = open(manual_engine.__file__, encoding="utf-8").read()
    assert "groww_client" not in src and "GrowwClient" not in src


from manual_engine import book_summary, held_line, position_pnl, resting_line


def _pos(**kw):
    base = {"symbol": "KEI", "quantity": 40, "avg_price": 1830.5, "product": "MIS",
            "ltp": 1868.0}
    base.update(kw)
    return base


def test_pnl_on_a_long_in_profit():
    p = position_pnl(_pos())
    assert p["side"] == "LONG"
    assert p["pnl"] == pytest.approx((1868.0 - 1830.5) * 40)
    assert p["pct"] == pytest.approx((1868.0 - 1830.5) / 1830.5 * 100)


def test_pnl_on_a_long_in_loss_is_negative():
    p = position_pnl(_pos(ltp=1800.0))
    assert p["pnl"] < 0 and p["pct"] < 0


def test_pnl_on_a_short_that_fell_is_a_gain():
    """Groww reports a short as a NEGATIVE quantity; a falling price must read as profit."""
    p = position_pnl(_pos(quantity=-10, avg_price=100.0, ltp=90.0))
    assert p["side"] == "SHORT"
    assert p["pnl"] == pytest.approx(100.0)
    assert p["pct"] == pytest.approx(10.0)


def test_pnl_on_a_short_that_rose_is_a_loss():
    p = position_pnl(_pos(quantity=-10, avg_price=100.0, ltp=110.0))
    assert p["pnl"] == pytest.approx(-100.0) and p["pct"] == pytest.approx(-10.0)


@pytest.mark.parametrize("missing", [{"ltp": None}, {"avg_price": None}, {"quantity": None},
                                     {"quantity": 0}, {"avg_price": 0}])
def test_pnl_is_none_when_anything_is_missing(missing):
    assert position_pnl(_pos(**missing)) is None


def test_pnl_of_nothing_is_none():
    assert position_pnl(None) is None


def test_held_line_carries_quantity_average_price_and_pnl():
    line = held_line(_pos())
    assert "40" in line and "1830.5" in line and "1868" in line
    assert "%" in line and ("+" in line)


def test_held_line_without_a_price_still_states_the_holding():
    line = held_line(_pos(ltp=None))
    assert "40" in line and "1830.5" in line
    assert "unrealis" not in line.lower()      # no P&L claimed without a price


def test_resting_line_warns_that_new_levels_replace_it():
    line = resting_line({"stop": 1808.0, "target": 1905.0, "quantity": 13})
    assert "1808" in line and "1905" in line and "13" in line
    assert "replace" in line.lower()


def test_resting_line_is_empty_without_an_armed_order():
    assert resting_line(None) == ""
    assert resting_line({}) == ""


def test_book_summary_lists_holdings():
    s = book_summary([_pos(), _pos(symbol="BSE", quantity=10, avg_price=99.0, ltp=96.0)])
    assert "KEI" in s and "BSE" in s and "already held" in s.lower()


def test_book_summary_is_empty_for_an_empty_book():
    assert book_summary([]) == "" and book_summary(None) == ""


def test_run_symbol_sends_the_pnl_and_resting_exits(skills_dir):
    seen = {}

    def runner(argv, text):
        seen["msg"] = text
        return 0, _envelope(_item()), ""
    _engine(runner, skills_dir).run_symbol(
        "KEI", position=_pos(), resting={"stop": 1808.0, "target": 1905.0, "quantity": 13})
    assert "1868" in seen["msg"] and "%" in seen["msg"]
    assert "1905" in seen["msg"] and "replace" in seen["msg"].lower()


def test_run_top5_sends_the_book(skills_dir):
    seen = {}

    def runner(argv, text):
        seen["msg"] = text
        return 0, _envelope({"picks": []}), ""
    _engine(runner, skills_dir).run_top5(positions=[_pos()])
    assert "KEI" in seen["msg"] and "already held" in seen["msg"].lower()


def test_run_top5_without_a_book_is_unchanged(skills_dir):
    seen = {}

    def runner(argv, text):
        seen["msg"] = text
        return 0, _envelope({"picks": []}), ""
    _engine(runner, skills_dir).run_top5()
    assert "already held" not in seen["msg"].lower()
