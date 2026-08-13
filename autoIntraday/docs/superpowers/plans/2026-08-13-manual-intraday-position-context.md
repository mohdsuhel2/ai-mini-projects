# Manual Intraday Position Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tell the skill everything decision-relevant about a position it is analysing — live price, unrealised P&L, and any exits already resting at the broker — and give top-5 the current book so it does not suggest what you already hold.

**Architecture:** `broker_positions` gains an `ltp` column filled at fetch time. `manual_engine` grows a pure `position_pnl` plus three pure prompt-builders, and `run_symbol`/`run_top5` take the extra context as optional arguments so existing callers keep working. `analysis_job` supplies resting exits and the book.

**Tech Stack:** Python 3, sqlite3, Streamlit, pytest. Spec: `docs/superpowers/specs/2026-08-13-manual-intraday-position-context-design.md`.

## Global Constraints

- Run tests with: `cd /Users/mohdsuhel/ai-mini-projects/autoIntraday && .venv/bin/python -m pytest tests/<file> -q`.
- `broker_positions` EXISTS in the live database, so `ltp` needs an `ALTER TABLE` migration. `CREATE TABLE IF NOT EXISTS` silently no-ops on an existing table — the trap that nearly wiped the trade ledger earlier.
- Groww reports a SHORT as a negative quantity. `(ltp - avg) * quantity` is correct for both sides; do not branch on side for the rupee figure.
- Unknown stays unknown: a missing ltp, average or quantity yields `None`, never `0`.
- New engine arguments are OPTIONAL with existing behaviour as the default — `tests/test_manual_engine.py` calls `run_symbol(symbol, position=...)` and `run_top5()` and must keep passing.
- Keep the wording `no open position` in the flat-symbol branch: `test_flat_symbol_says_no_position` asserts it.
- Do NOT `git add`: `groww_client.py`, `swing_job.py`, `observe_job.py`, `tests/test_groww_client.py`, `tests/test_observe.py`, `tests/test_swing_economics.py`, `tests/test_swing_job.py`.

---

### Task 1: Store — `ltp` on the position snapshot

**Files:** Modify `store.py`; test `tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_broker_positions_store_the_live_price():
    store = Store(":memory:")
    store.replace_broker_positions([{"symbol": "KEI", "quantity": 40, "avg_price": 1830.5,
                                     "product": "MIS", "ltp": 1868.0}])
    assert store.get_broker_positions()[0]["ltp"] == 1868.0


def test_broker_positions_tolerate_a_missing_price():
    store = Store(":memory:")
    store.replace_broker_positions([{"symbol": "KEI", "quantity": 40, "avg_price": 1830.5,
                                     "product": "MIS"}])
    assert store.get_broker_positions()[0]["ltp"] is None


def test_ltp_column_is_migrated_onto_an_older_database(tmp_path):
    """broker_positions already exists in the live DB, so CREATE TABLE IF NOT EXISTS would
    silently skip the new column — only an ALTER TABLE adds it."""
    import sqlite3
    db = str(tmp_path / "old.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE broker_positions (symbol TEXT PRIMARY KEY, quantity INTEGER, "
                "avg_price REAL, product TEXT, fetched_at TEXT NOT NULL)")
    con.commit()
    con.close()
    store = Store(db)
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(broker_positions)")}
    assert "ltp" in cols
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -q -k "live_price or missing_a_price or ltp_column"`

- [ ] **Step 3: Implement**

In `_SCHEMA`, add `ltp REAL,` to `broker_positions` after `product TEXT,`.

In the migration block (beside the `swing_eta_days` migrations):

```python
        bpcols = {r["name"] for r in self._conn.execute("PRAGMA table_info(broker_positions)")}
        if bpcols and "ltp" not in bpcols:
            self._conn.execute("ALTER TABLE broker_positions ADD COLUMN ltp REAL")
```

In `replace_broker_positions`, store it:

```python
            self._conn.execute(
                "INSERT INTO broker_positions (symbol, quantity, avg_price, product, ltp, "
                "fetched_at) VALUES (?,?,?,?,?,?)",
                (p["symbol"], p.get("quantity"), p.get("avg_price"), p.get("product"),
                 p.get("ltp"), now))
```

and in `get_broker_positions` select it:

```python
            "SELECT symbol, quantity, avg_price, product, ltp, fetched_at "
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_store.py -q` → all PASS
- [ ] **Step 5: Commit** `git add store.py tests/test_store.py && git commit -m "feat(manual-intraday): live price on the position snapshot"`

---

### Task 2: Engine — P&L and richer prompts

**Files:** Modify `manual_engine.py`; test `tests/test_manual_engine.py`

**Interfaces:** `position_pnl(position) -> dict|None` (`pnl`, `pct`, `side`, `value`); `held_line(position) -> str`; `resting_line(resting) -> str`; `book_summary(positions) -> str`; `run_symbol(symbol, position=None, resting=None)`; `run_top5(positions=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_manual_engine.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail** → `ImportError: cannot import name 'position_pnl'`

- [ ] **Step 3: Implement**

Add to `manual_engine.py` before the `ManualSkillEngine` class:

```python
def position_pnl(position) -> dict | None:
    """Unrealised P&L on a held intraday position, or None when the price, average or size is
    unknown — never a fabricated zero.

    Groww reports a SHORT as a negative quantity, so `(ltp - avg) * quantity` is right for both
    sides with no branching: a short whose price fell multiplies two negatives into a profit.
    The percentage is sign-corrected the same way, so that short reads as a gain, not a loss."""
    if not position:
        return None
    try:
        qty = int(position.get("quantity") or 0)
        avg = float(position.get("avg_price") or 0)
        ltp = float(position.get("ltp") or 0)
    except (TypeError, ValueError):
        return None
    if qty == 0 or avg <= 0 or ltp <= 0:
        return None
    sign = 1 if qty > 0 else -1
    return {"pnl": (ltp - avg) * qty,
            "pct": (ltp - avg) / avg * 100.0 * sign,
            "side": "LONG" if qty > 0 else "SHORT",
            "value": abs(qty) * ltp}


def held_line(position) -> str:
    """What the operator holds, and what it is currently worth to them."""
    if not position:
        return ""
    qty = position.get("quantity")
    avg = position.get("avg_price")
    txt = (f"You currently HOLD this: {qty} shares @ avg ₹{avg} "
           f"({position.get('product') or 'MIS'}).")
    p = position_pnl(position)
    if p:
        txt += (f"\nLive price ₹{position.get('ltp')} — unrealised {p['pnl']:+,.0f} "
                f"({p['pct']:+.2f}%) on ₹{p['value']:,.0f} of exposure.")
    return txt


def resting_line(resting) -> str:
    """Exits already live at the broker for this symbol. Without this the skill quotes fresh
    levels as though the position were unprotected, and nothing tells the operator that acting
    on them REPLACES what is already resting."""
    if not resting:
        return ""
    stop, target = resting.get("stop"), resting.get("target")
    if stop is None and target is None:
        return ""
    return (f"You ALREADY have exits resting at Groww from an earlier order: "
            f"stop ₹{stop}, target ₹{target} on {resting.get('quantity')} shares. "
            f"New levels REPLACE these, they do not add to them.")


def book_summary(positions) -> str:
    """The whole open book, for the screening mode — so a pick you already hold is called out
    rather than presented as a fresh idea."""
    rows = [p for p in (positions or []) if p.get("symbol")]
    if not rows:
        return ""
    bits = []
    for p in rows:
        pnl = position_pnl(p)
        bits.append(f"{p['symbol']} {p.get('quantity')} @ {p.get('avg_price')}"
                    + (f" ({pnl['pct']:+.2f}%)" if pnl else ""))
    return ("Positions you already hold: " + ", ".join(bits)
            + ". Say so plainly if a pick is something already held.")
```

Then replace `run_symbol` and `run_top5`:

```python
    def run_symbol(self, symbol: str, position: dict | None = None,
                   resting: dict | None = None) -> dict:
        """Full analysis of ONE symbol, with everything decision-relevant about an existing
        position: size, average, live price, unrealised P&L, and any exits already resting."""
        parts = [f"Analyze {symbol.upper()} now."]
        if position:
            parts.append(held_line(position))
            rest = resting_line(resting)
            if rest:
                parts.append(rest)
        else:
            parts.append("You have no open position in this stock — judge it as a fresh entry.")
        raw, obj = self._call("one", ONE_SCHEMA, "\n".join(parts))
        return dict(_parse_item(obj, symbol), raw=raw)

    def run_top5(self, positions=None) -> list[dict]:
        """The skill's own screen — it picks the names. One call, up to MAX_PICKS items."""
        parts = [f"Run your screening mode and give me your top {MAX_PICKS} candidates right "
                 "now, best first."]
        book = book_summary(positions)
        if book:
            parts.append(book)
        raw, obj = self._call("top5", TOP5_SCHEMA, "\n".join(parts))
        picks = obj.get("picks")
        if not isinstance(picks, list):
            raise ManualEngineError(f"reply missing 'picks' list: {str(obj)[:200]!r}")
        return [dict(_parse_item(p), raw=raw) for p in picks]
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_manual_engine.py -q` → all PASS
- [ ] **Step 5: Commit** `git add manual_engine.py tests/test_manual_engine.py && git commit -m "feat(manual-intraday): position P&L and resting exits in the prompt"`

---

### Task 3: Job — supply the resting exits and the book

**Files:** Modify `analysis_job.py`; test `tests/test_manual_order_job.py` is unrelated — extend `tests/test_analysis_job.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analysis_job.py`:

```python
def test_resting_armed_exits_are_passed_for_that_symbol():
    store, rid = _run_with(["KEI", "BSE"])
    o = store.create_manual_order(symbol="KEI", side="LONG", quantity=13,
                                  entry_type="MARKET", entry_price=100.0, stop=95.0,
                                  target=120.0, mode="paper")
    store.update_manual_order(o, status="ARMED", oco_order_id="OCO1")

    class E(_Engine):
        def __init__(self):
            super().__init__()
            self.resting = {}

        def run_symbol(self, symbol, position=None, resting=None):
            self.resting[symbol] = resting
            return dict(_item(symbol), raw="{}")
    eng = E()
    run_analysis(store, eng, rid)
    assert eng.resting["KEI"]["stop"] == 95.0 and eng.resting["KEI"]["target"] == 120.0
    assert eng.resting["BSE"] is None


def test_only_armed_orders_count_as_resting():
    """A rejected or closed order rests nothing at the broker."""
    store, rid = _run_with(["KEI"])
    for status in ("REJECTED", "CLOSED", "ENTRY_PENDING"):
        o = store.create_manual_order(symbol="KEI", side="LONG", quantity=1,
                                      entry_type="MARKET", entry_price=100.0, stop=95.0,
                                      target=120.0, mode="paper")
        store.update_manual_order(o, status=status)

    class E(_Engine):
        def __init__(self):
            super().__init__()
            self.resting = {}

        def run_symbol(self, symbol, position=None, resting=None):
            self.resting[symbol] = resting
            return dict(_item(symbol), raw="{}")
    eng = E()
    run_analysis(store, eng, rid)
    assert eng.resting["KEI"] is None


def test_top5_receives_the_open_book():
    store = Store(":memory:")
    store.replace_broker_positions([{"symbol": "KEI", "quantity": 40, "avg_price": 100.0,
                                     "product": "MIS", "ltp": 110.0}])
    rid = store.start_analysis_run("intraday-breakout", "top5")

    class E:
        def __init__(self):
            self.seen = None

        def run_top5(self, positions=None):
            self.seen = positions
            return []
    eng = E()
    run_top5(store, eng, rid)
    assert [p["symbol"] for p in eng.seen] == ["KEI"]
```

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement**

In `analysis_job.run_analysis`, after `by_symbol` is built:

```python
    # Exits already resting at the broker, newest first — only an ARMED order actually has a
    # live OCO; a rejected or closed one rests nothing.
    resting = {}
    for o in store.get_manual_orders(limit=200):
        if o["status"] == "ARMED" and o["symbol"] not in resting:
            resting[o["symbol"]] = o
```

and change the call:

```python
            item = engine.run_symbol(symbol, position=by_symbol.get(symbol),
                                     resting=resting.get(symbol))
```

In `run_top5`, pass the book:

```python
        picks = engine.run_top5(positions=store.get_broker_positions())
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_analysis_job.py -q` → all PASS
- [ ] **Step 5: Commit** `git add analysis_job.py tests/test_analysis_job.py && git commit -m "feat(manual-intraday): pass resting exits and the open book to the skill"`

---

### Task 4: Dashboard — fetch the price, show it

**Files:** Modify `dashboard.py`; test `tests/test_dashboard_manual_intraday.py`

- [ ] **Step 1: Write the failing test**

```python
def test_position_rows_show_price_and_pnl():
    rows = dashboard._position_table_rows([
        {"symbol": "KEI", "quantity": 40, "avg_price": 100.0, "product": "MIS", "ltp": 110.0},
        {"symbol": "BSE", "quantity": 10, "avg_price": 99.0, "product": "MIS", "ltp": None}])
    assert rows[0]["LTP"] == 110.0 and rows[0]["P&L"] == 400.0
    assert rows[0]["P&L %"] == 10.0
    assert rows[1]["LTP"] is None and rows[1]["P&L"] is None   # unknown, not zero
    assert rows[0]["Analyze"] is False and rows[0]["Symbol"] == "KEI"
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement**

Add beside `_selected_symbols`:

```python
def _position_table_rows(positions) -> list[dict]:
    """The Positions table, carrying the same P&L the skill is told about. Unknown price ->
    blank cells, never zero."""
    from manual_engine import position_pnl
    rows = []
    for p in (positions or []):
        pnl = position_pnl(p)
        rows.append({"Analyze": False, "Symbol": p.get("symbol"),
                     "Qty": p.get("quantity"), "Avg": p.get("avg_price"),
                     "LTP": p.get("ltp"),
                     "P&L": round(pnl["pnl"], 2) if pnl else None,
                     "P&L %": round(pnl["pct"], 2) if pnl else None,
                     "Product": p.get("product")})
    return rows
```

In `_mi_positions_tab`, replace the inline `table = [...]` comprehension with
`table = _position_table_rows(positions)` and extend the editor's `disabled` list to
`["Symbol", "Qty", "Avg", "LTP", "P&L", "P&L %", "Product"]`.

In `_refresh_positions_from_groww`, fetch quotes alongside:

```python
def _refresh_positions_from_groww() -> None:
    """Fetch the intraday (MIS) position book and persist the snapshot, with live prices so the
    page and the skill both see the same P&L. Delivery holdings are the Swing page's job."""
    from settings import load_settings
    from groww_client import GrowwClient
    load_settings().apply_to_environ()
    client = GrowwClient(mode="live")
    client.authenticate()
    positions = client.get_positions()
    try:
        ltp = client.get_ltp([p["symbol"] for p in positions]) if positions else {}
    except Exception:                                               # noqa: BLE001
        ltp = {}          # a quote failure degrades to a plain refresh; price is enrichment
    for p in positions:
        p["ltp"] = ltp.get(p["symbol"])
    _db(lambda s: s.replace_broker_positions(positions))
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/ -q` → all PASS
- [ ] **Step 5: Render the Positions tab under AppTest against a TEMP database**, seeding a position with an ltp, and assert no exception and that the editor renders.
- [ ] **Step 6: Commit** `git add dashboard.py tests/test_dashboard_manual_intraday.py && git commit -m "feat(manual-intraday): live price and P&L on the positions table"`
