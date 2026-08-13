"""Manual Intraday's broker layer: the arithmetic and the fat-finger guards are pure and
tested here; the I/O is exercised against a stub client."""
import pytest

from manual_broker import (ManualBroker, ManualBrokerError, entry_txn, exit_txn, is_filled,
                           is_rejected, order_economics, qty_for_capital, side_from_verdict,
                           validate_bracket)


# ---- pure helpers ---------------------------------------------------------------------

def test_entry_and_exit_transactions_per_side():
    assert entry_txn("LONG") == "BUY" and exit_txn("LONG") == "SELL"
    assert entry_txn("SHORT") == "SELL" and exit_txn("SHORT") == "BUY"


def test_unknown_side_raises():
    with pytest.raises(ManualBrokerError):
        entry_txn("SIDEWAYS")


@pytest.mark.parametrize("verdict", ["BUY NOW", "BUY ON PULLBACK", "IGNITION_BUY", "ADD",
                                     "MARKET EXCEPTION LONG"])
def test_buy_verdicts_map_to_long(verdict):
    assert side_from_verdict(verdict) == "LONG"


@pytest.mark.parametrize("verdict", ["SHORT NOW", "SHORT ON BREAKDOWN"])
def test_short_verdicts_map_to_short(verdict):
    assert side_from_verdict(verdict) == "SHORT"


@pytest.mark.parametrize("verdict", ["SELL NOW", "EXIT", "REDUCE", "HOLD", "WAIT", "NO TRADE",
                                     "", None])
def test_ambiguous_verdicts_map_to_nothing(verdict):
    """SELL/EXIT/REDUCE mean 'close the long you hold', NOT 'open a short'. Guessing here
    would open a brand-new short position on an exit instruction."""
    assert side_from_verdict(verdict) is None


def test_qty_for_capital_floors():
    assert qty_for_capital(30000, 1842.0) == 16          # 16.28 -> 16
    assert qty_for_capital(1000, 1842.0) == 0            # cannot afford one share


def test_qty_for_capital_handles_missing_price():
    assert qty_for_capital(30000, None) == 0
    assert qty_for_capital(30000, 0) == 0


def test_economics_for_a_long():
    e = order_economics("LONG", 10, entry=100.0, stop=95.0, target=120.0)
    assert e["exposure"] == 1000.0
    assert e["risk"] == 50.0                              # (100-95)*10
    assert e["reward"] == 200.0                           # (120-100)*10
    assert e["rr"] == pytest.approx(4.0)


def test_economics_for_a_short():
    e = order_economics("SHORT", 10, entry=100.0, stop=105.0, target=80.0)
    assert e["risk"] == 50.0 and e["reward"] == 200.0
    assert e["rr"] == pytest.approx(4.0)


def test_economics_missing_legs_are_none_not_zero():
    e = order_economics("LONG", 10, entry=100.0, stop=None, target=None)
    assert e["exposure"] == 1000.0
    assert e["risk"] is None and e["reward"] is None and e["rr"] is None


def test_validate_accepts_a_sane_long_and_short():
    validate_bracket("LONG", entry=100.0, stop=95.0, target=120.0, quantity=10)
    validate_bracket("SHORT", entry=100.0, stop=105.0, target=80.0, quantity=10)


def test_validate_rejects_inverted_long():
    """A long whose stop sits ABOVE entry fires the instant it arms."""
    with pytest.raises(ManualBrokerError, match="stop"):
        validate_bracket("LONG", entry=100.0, stop=105.0, target=120.0, quantity=10)
    with pytest.raises(ManualBrokerError, match="target"):
        validate_bracket("LONG", entry=100.0, stop=95.0, target=90.0, quantity=10)


def test_validate_rejects_inverted_short():
    with pytest.raises(ManualBrokerError, match="stop"):
        validate_bracket("SHORT", entry=100.0, stop=95.0, target=80.0, quantity=10)
    with pytest.raises(ManualBrokerError, match="target"):
        validate_bracket("SHORT", entry=100.0, stop=105.0, target=120.0, quantity=10)


def test_validate_rejects_nonpositive_quantity_and_entry():
    with pytest.raises(ManualBrokerError, match="quantity"):
        validate_bracket("LONG", entry=100.0, stop=95.0, target=120.0, quantity=0)
    with pytest.raises(ManualBrokerError, match="entry"):
        validate_bracket("LONG", entry=0, stop=95.0, target=120.0, quantity=10)


def test_status_predicates():
    assert is_filled("EXECUTED") and is_filled("complete")
    assert is_rejected("REJECTED") and is_rejected("cancelled")
    assert not is_filled("PENDING") and not is_rejected("PENDING")


# ---- broker wrapper against a stub client ----------------------------------------------

class _Client:
    def __init__(self, status="COMPLETE"):
        self.mode = "paper"
        self.calls = []
        self._status = status

    def place_order(self, **kw):
        self.calls.append(("place_order", kw))
        return {"order_id": "OID1", "status": self._status, "price": kw.get("price")}

    def place_oco_order(self, symbol, entry, target, stop_loss):
        self.calls.append(("place_oco", {"symbol": symbol, "entry": entry, "target": target,
                                         "stop_loss": stop_loss}))
        return {"order_id": "OCO1", "status": "ACTIVE"}

    def modify_oco_order(self, order_id, target, stop_loss):
        self.calls.append(("modify_oco", {"order_id": order_id, "target": target,
                                          "stop_loss": stop_loss}))
        return {"order_id": order_id, "status": "MODIFIED"}

    def cancel_order(self, order_id):
        self.calls.append(("cancel", {"order_id": order_id}))
        return {"order_id": order_id, "status": "CANCELLED"}

    def get_order_status(self, order_id):
        return {"order_id": order_id, "status": self._status, "reason": None}


def test_place_entry_sends_buy_for_a_long():
    c = _Client()
    ManualBroker(client=c).place_entry("KEI", "LONG", 10, "MARKET", None)
    name, kw = c.calls[0]
    assert name == "place_order"
    assert kw["transaction_type"] == "BUY" and kw["order_type"] == "MARKET"
    assert kw["quantity"] == 10 and kw["product"] == "MIS" and kw["symbol"] == "KEI"


def test_place_entry_sends_sell_and_a_limit_price_for_a_short():
    c = _Client()
    ManualBroker(client=c).place_entry("KEI", "SHORT", 5, "LIMIT", 1842.0)
    _, kw = c.calls[0]
    assert kw["transaction_type"] == "SELL" and kw["order_type"] == "LIMIT"
    assert kw["price"] == 1842.0


def test_arm_oco_uses_the_exit_transaction_and_both_legs():
    c = _Client()
    ManualBroker(client=c).arm_oco("KEI", "LONG", 10, target=1905.0, stop=1808.0)
    name, kw = c.calls[0]
    assert name == "place_oco"
    assert kw["entry"]["transaction_type"] == "SELL"      # SELL closes a long
    assert kw["entry"]["quantity"] == 10
    assert kw["target"]["trigger_price"] == 1905.0 and kw["target"]["price"] == 1905.0
    assert kw["stop_loss"]["trigger_price"] == 1808.0


def test_arm_oco_validates_before_sending():
    c = _Client()
    with pytest.raises(ManualBrokerError):
        ManualBroker(client=c).arm_oco("KEI", "LONG", 10, target=1700.0, stop=1808.0)
    assert c.calls == []                                  # nothing was sent


def test_square_off_cancels_the_oco_before_the_market_exit():
    c = _Client()
    ManualBroker(client=c).square_off("KEI", "LONG", 10, oco_order_id="OCO1")
    assert [n for n, _ in c.calls] == ["cancel", "place_order"]
    assert c.calls[1][1]["transaction_type"] == "SELL"
    assert c.calls[1][1]["order_type"] == "MARKET"


def test_square_off_aborts_if_the_oco_cancel_fails():
    """If the OCO survives, a market exit would flatten the position and then let the stop
    fire into a brand-new REVERSE position. Refuse rather than risk that."""
    class Bad(_Client):
        def cancel_order(self, order_id):
            raise RuntimeError("cancel rejected")
    c = Bad()
    with pytest.raises(ManualBrokerError, match="check the broker"):
        ManualBroker(client=c).square_off("KEI", "LONG", 10, oco_order_id="OCO1")
    assert [n for n, _ in c.calls] == []                  # no market exit was sent


def test_square_off_without_an_oco_just_exits():
    c = _Client()
    ManualBroker(client=c).square_off("KEI", "SHORT", 4, oco_order_id=None)
    assert [n for n, _ in c.calls] == ["place_order"]
    assert c.calls[0][1]["transaction_type"] == "BUY"


def test_rejected_entry_raises_with_the_reason():
    c = _Client(status="REJECTED")
    with pytest.raises(ManualBrokerError, match="REJECTED"):
        ManualBroker(client=c).place_entry("KEI", "LONG", 10, "MARKET", None)


def test_mode_defaults_to_paper():
    assert ManualBroker().mode == "paper"


# ---- netting the broker's position legs -------------------------------------------------

from manual_broker import net_positions


def test_multiple_legs_of_one_symbol_are_netted():
    """Groww returns one row PER LEG, not per symbol. Inserting them verbatim into a
    symbol-keyed table raised 'UNIQUE constraint failed: broker_positions.symbol'."""
    out = net_positions([
        {"symbol": "KEI", "quantity": 10, "avg_price": 100.0, "product": "MIS"},
        {"symbol": "KEI", "quantity": 5, "avg_price": 102.0, "product": "MIS"}])
    assert len(out) == 1
    assert out[0]["symbol"] == "KEI" and out[0]["quantity"] == 15


def test_delivery_rows_are_excluded():
    """CNC rows are the delivery portfolio — the Swing page's job, not this page's, and a
    CNC row colliding with an MIS one for the same stock is what triggered the crash."""
    out = net_positions([
        {"symbol": "KEI", "quantity": 10, "avg_price": 100.0, "product": "MIS"},
        {"symbol": "KEI", "quantity": 40, "avg_price": 90.0, "product": "CNC"},
        {"symbol": "AARTIIND", "quantity": 100, "avg_price": 500.0, "product": "CNC"}])
    assert [r["symbol"] for r in out] == ["KEI"]
    assert out[0]["quantity"] == 10          # the CNC 40 is not added in


def test_legs_that_net_to_flat_are_dropped():
    out = net_positions([
        {"symbol": "KEI", "quantity": 10, "avg_price": 100.0, "product": "MIS"},
        {"symbol": "KEI", "quantity": -10, "avg_price": 105.0, "product": "MIS"}])
    assert out == []


def test_a_net_short_survives_as_a_negative_quantity():
    out = net_positions([
        {"symbol": "MCX", "quantity": 4, "avg_price": 100.0, "product": "MIS"},
        {"symbol": "MCX", "quantity": -10, "avg_price": 100.0, "product": "MIS"}])
    assert out[0]["quantity"] == -6


def test_missing_product_is_treated_as_mis():
    out = net_positions([{"symbol": "KEI", "quantity": 10, "avg_price": 100.0}])
    assert [r["symbol"] for r in out] == ["KEI"]


def test_rows_without_a_symbol_are_skipped():
    out = net_positions([{"quantity": 10, "avg_price": 100.0, "product": "MIS"},
                         {"symbol": "KEI", "quantity": 1, "avg_price": 100.0,
                          "product": "MIS"}])
    assert [r["symbol"] for r in out] == ["KEI"]


def test_ltp_rides_through_when_present():
    out = net_positions([{"symbol": "KEI", "quantity": 10, "avg_price": 100.0,
                          "product": "MIS", "ltp": 110.0}])
    assert out[0]["ltp"] == 110.0


def test_empty_input_is_empty_output():
    assert net_positions([]) == [] and net_positions(None) == []
