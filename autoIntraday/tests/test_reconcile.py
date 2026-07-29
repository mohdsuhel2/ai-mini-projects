"""Ledger reconcile diff — DB open positions vs what Groww actually holds."""
import types

from scripts.reconcile_ledgers import reconcile_view


def _pos(pid, symbol, strategy_id, status="OPEN", side="LONG", quantity=10):
    return types.SimpleNamespace(id=pid, symbol=symbol, strategy_id=strategy_id,
                                 status=status, side=side, quantity=quantity)


def test_real_phantom_unknown_split():
    db = [_pos(1, "AAA", "v3"), _pos(2, "BBB", "v3")]
    broker = [{"symbol": "AAA", "quantity": 10}, {"symbol": "CCC", "quantity": 5}]
    v = reconcile_view(db, broker, "v3")
    assert [p.symbol for p in v["real"]] == ["AAA"]        # held -> real
    assert [p.symbol for p in v["phantom"]] == ["BBB"]     # not held -> phantom
    assert v["unknown"] == [("CCC", 5)]                    # held but no DB row


def test_duplicate_symbol_across_ledgers_collapses_to_one_real():
    db = [_pos(1, "MON", "v1"), _pos(2, "MON", "v2"), _pos(3, "MON", "v3")]
    broker = [{"symbol": "MON", "quantity": 100}]
    v = reconcile_view(db, broker, "v3")
    assert len(v["real"]) == 1 and v["real"][0].strategy_id == "v3"   # active ledger preferred
    assert sorted(p.id for p in v["phantom"]) == [1, 2]              # duplicates -> phantom
    assert v["unknown"] == []


def test_case_insensitive_symbol_match():
    v = reconcile_view([_pos(1, "aaa", "v3")], [{"symbol": "AAA", "quantity": 10}], "v3")
    assert len(v["real"]) == 1 and not v["phantom"]


def test_broker_flat_symbol_is_phantom():
    # a broker row that nets to zero is not "held" -> the DB row is phantom, not unknown
    v = reconcile_view([_pos(1, "AAA", "v3")], [{"symbol": "AAA", "quantity": 0}], "v3")
    assert [p.symbol for p in v["phantom"]] == ["AAA"] and not v["real"] and not v["unknown"]
