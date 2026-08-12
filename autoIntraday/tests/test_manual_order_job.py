"""The detached manual-order runner: entry -> fill -> native OCO, and the failure paths that
must never leave a filled position unprotected without saying so."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manual_broker import ManualBrokerError
from manual_order_job import run_order
from store import Store


class _Broker:
    """Stub with the ManualBroker surface the job uses."""

    def __init__(self, entry_status="COMPLETE", statuses=None, oco_error=None,
                 entry_error=None):
        self.entry_status, self.oco_error, self.entry_error = entry_status, oco_error, entry_error
        self.statuses = list(statuses or [])
        self.armed = []

    def place_entry(self, symbol, side, quantity, entry_type, price):
        if self.entry_error:
            raise ManualBrokerError(self.entry_error)
        return {"order_id": "OID1", "status": self.entry_status, "price": price}

    def order_status(self, order_id):
        nxt = self.statuses.pop(0) if self.statuses else self.entry_status
        return {"order_id": order_id, "status": nxt, "reason": "no margin"}

    def arm_oco(self, symbol, side, quantity, target, stop):
        if self.oco_error:
            raise ManualBrokerError(self.oco_error)
        self.armed.append((symbol, side, quantity, target, stop))
        return {"order_id": "OCO1", "status": "ACTIVE"}


def _order(store, **kw):
    base = dict(symbol="KEI", side="LONG", quantity=10, entry_type="MARKET",
                entry_price=1842.0, stop=1808.0, target=1905.0, mode="paper")
    base.update(kw)
    return store.create_manual_order(**base)


def test_market_entry_fills_and_arms_the_oco():
    store = Store(":memory:")
    oid = _order(store)
    b = _Broker()
    assert run_order(store, b, oid, sleep=lambda s: None) == "ARMED"
    row = store.get_manual_order(oid)
    assert row["status"] == "ARMED" and row["entry_order_id"] == "OID1"
    assert row["oco_order_id"] == "OCO1" and row["filled_at"]
    assert b.armed == [("KEI", "LONG", 10, 1905.0, 1808.0)]


def test_rejected_entry_records_the_reason_and_arms_nothing():
    store = Store(":memory:")
    oid = _order(store)
    b = _Broker(entry_error="entry REJECTED for KEI: no margin")
    assert run_order(store, b, oid, sleep=lambda s: None) == "REJECTED"
    row = store.get_manual_order(oid)
    assert row["status"] == "REJECTED" and "no margin" in row["error"]
    assert row["oco_order_id"] is None
    assert b.armed == []


def test_entry_rejected_while_polling_records_the_reason():
    store = Store(":memory:")
    oid = _order(store, entry_type="LIMIT")
    b = _Broker(entry_status="PENDING", statuses=["PENDING", "REJECTED"])
    assert run_order(store, b, oid, sleep=lambda s: None) == "REJECTED"
    assert store.get_manual_order(oid)["status"] == "REJECTED"
    assert b.armed == []


def test_limit_still_pending_at_the_deadline_stays_pending():
    store = Store(":memory:")
    oid = _order(store, entry_type="LIMIT")
    b = _Broker(entry_status="PENDING", statuses=["PENDING"] * 50)
    out = run_order(store, b, oid, poll_s=10, deadline_s=25, sleep=lambda s: None)
    assert out == "ENTRY_PENDING"
    row = store.get_manual_order(oid)
    assert row["status"] == "ENTRY_PENDING" and row["oco_order_id"] is None
    assert b.armed == []


def test_oco_failure_after_a_fill_is_recorded_without_losing_the_fill():
    """The position IS open. Saying so loudly matters more than the OCO failing."""
    store = Store(":memory:")
    oid = _order(store)
    b = _Broker(oco_error="smart order rejected")
    assert run_order(store, b, oid, sleep=lambda s: None) == "FILLED"
    row = store.get_manual_order(oid)
    assert row["status"] == "FILLED"          # filled, NOT armed
    assert "smart order rejected" in row["error"]
    assert row["oco_order_id"] is None and row["filled_at"]


def test_an_instant_fill_takes_its_price_from_the_placement_response():
    """An entry that comes back already COMPLETE is never polled, so the only price on offer
    is the one the placement reported (paper mode fills at the requested price)."""
    store = Store(":memory:")
    oid = _order(store)
    run_order(store, _Broker(), oid, sleep=lambda s: None)
    assert store.get_manual_order(oid)["fill_price"] == 1842.0


def test_a_resting_entry_takes_its_fill_price_from_the_poll():
    store = Store(":memory:")
    oid = _order(store, entry_type="LIMIT")

    class B(_Broker):
        def place_entry(self, symbol, side, quantity, entry_type, price):
            return {"order_id": "OID1", "status": "PENDING", "price": None}

        def order_status(self, order_id):
            return {"order_id": order_id, "status": "COMPLETE", "price": 1843.25}
    run_order(store, B(), oid, sleep=lambda s: None)
    assert store.get_manual_order(oid)["fill_price"] == 1843.25


def test_an_unknown_fill_price_stays_none_rather_than_being_invented():
    """A live MARKET entry reports no price. Unknown must stay unknown — a fabricated fill
    price would silently corrupt every P&L number downstream."""
    store = Store(":memory:")
    oid = _order(store)

    class B(_Broker):
        def place_entry(self, symbol, side, quantity, entry_type, price):
            return {"order_id": "OID1", "status": "COMPLETE", "price": None}
    run_order(store, B(), oid, sleep=lambda s: None)
    assert store.get_manual_order(oid)["fill_price"] is None


def test_unknown_order_id_returns_error():
    assert run_order(Store(":memory:"), _Broker(), 999, sleep=lambda s: None) == "ERROR"
