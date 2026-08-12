# Manual Analysis Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new "Analyze" page that fetches the live Groww intraday position book, lets you checkbox-select stocks and a skill, runs that skill on each in a detached job, and shows the full report (Raw) alongside a parsed card (Formatted) — plus a one-call "top 5" mode where the skill picks its own names.

**Architecture:** A generic `manual_engine.py` embeds any installed `SKILL.md` and enforces one skill-agnostic schema (`report` markdown + nullable levels). The dashboard creates the run row and seeds symbols, then launches `analysis_job.py` detached; the page polls the DB and renders progress and results. Storage rides in the main store beside `swing_runs`/`swing_verdicts`.

**Tech Stack:** Python 3, sqlite3, Streamlit (`st.data_editor`, `st.fragment`, `st.tabs`), the `claude` CLI in headless `-p` mode, pytest. Spec: `docs/superpowers/specs/2026-08-12-manual-analysis-page-design.md`.

## Global Constraints

- Run tests with: `cd /Users/mohdsuhel/ai-mini-projects/autoIntraday && .venv/bin/python -m pytest tests/<file> -q` (there is no bare `python` on this machine).
- `manual_engine.py` MUST NOT import a broker client — the no-orders property is structural, exactly as documented in `observe.py`.
- Levels (`entry`, `stop`, `target1..3`, `risk_reward`, `conviction`) are nullable everywhere. A skill omitting them is a valid answer, never a parse error. Unknown is never zero.
- Positions come from `get_positions()` (intraday/MIS) only — never `get_holdings()`.
- All new tables are additive; migrations follow the `price_at_analysis` / `swing_eta_days` pattern in `store.py`.
- `_STOCKANALYZE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze"` — the same constant `skill_screen_engine.py` and `swing_engine.py` already use.
- Do NOT `git add` these files, which carry unrelated uncommitted work: `groww_client.py`, `swing_job.py`, `observe_job.py`, `tests/test_groww_client.py`, `tests/test_observe.py`, `tests/test_swing_economics.py`, `tests/test_swing_job.py`. Stage only the files each task names.

---

### Task 1: `manual_engine.py` — the generic skill runner

**Files:**
- Create: `manual_engine.py`
- Test: `tests/test_manual_engine.py`

**Interfaces:**
- Produces:
  - `ITEM_PROPS: dict` — the shared JSON-schema properties for one analysis item.
  - `ONE_SCHEMA: dict`, `TOP5_SCHEMA: dict` — the two enforced schemas.
  - `ManualEngineError(DecisionEngineError)`.
  - `ManualSkillEngine(skill_id, runner=..., model=MODEL, claude_bin=None, skills_dir=..., use_web_search=True)` with `.run_symbol(symbol, position=None) -> dict` and `.run_top5() -> list[dict]`.
  - Each returned item is a dict with keys `symbol, report, verdict, conviction, entry, stop, target1, target2, target3, risk_reward, summary, raw` — where `raw` is the CLI's full stdout. Tasks 3 and 4 consume exactly these keys.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_manual_engine.py`:

```python
"""The generic manual skill runner: one schema every installed skill can fill, the full
report preserved, levels nullable, and no broker client anywhere near it."""
import json

import pytest

from manual_engine import (ONE_SCHEMA, TOP5_SCHEMA, ManualEngineError, ManualSkillEngine)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_manual_engine.py -q`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'manual_engine'`

- [ ] **Step 3: Implement**

Create `manual_engine.py`:

```python
"""Manual analysis runner — one headless `claude -p` per request that embeds ANY installed
SKILL.md and returns that skill's FULL narrative plus the actionable levels, for a human
reading the answer directly. Backs the dashboard's Analyze page.

Why generic rather than per-skill: skills are discovered from ~/.claude/skills at runtime
(see observe.available_skills), so a skill installed tomorrow must work here with no code
change. One schema every skill can fill — a free-text `verdict` in the skill's own
vocabulary, a markdown `report`, and levels that are nullable throughout because a scanner
legitimately produces none.

Like observe.py, this module never imports a broker client. The safety property is
structural, not a flag: there is no code path here that can place, modify or cancel an
order."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable

from claude_cli_engine import _result_text
from decision_engine import MODEL, DecisionEngineError

_STOCKANALYZE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze"
SKILLS_DIR = os.path.expanduser("~/.claude/skills")
# A full agentic run: the skill's data tool, sometimes a screener, plus web search and
# reasoning. Observed live at ~2 min; this is generous headroom, not an expectation. One
# value covers both modes — the top-5 screen is a single call of the same shape.
TIMEOUT_S = 1200
MAX_PICKS = 5

_NUM = {"type": ["number", "null"]}
ITEM_PROPS = {
    "symbol": {"type": "string"},
    "report": {"type": "string"},
    "verdict": {"type": "string"},
    "conviction": {"type": ["integer", "null"]},
    "entry": _NUM, "stop": _NUM,
    "target1": _NUM, "target2": _NUM, "target3": _NUM,
    "risk_reward": _NUM,
    "summary": {"type": "string"},
}
_ITEM = {"type": "object", "additionalProperties": False,
         "properties": ITEM_PROPS, "required": list(ITEM_PROPS)}
ONE_SCHEMA = dict(_ITEM)
TOP5_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"picks": {"type": "array", "maxItems": MAX_PICKS, "items": _ITEM}},
    "required": ["picks"],
}


class ManualEngineError(DecisionEngineError):
    """A manual skill run failed: missing SKILL.md, CLI error/timeout, or an unusable reply."""


def _default_runner(argv: list[str], input_text: str,
                    timeout: int = TIMEOUT_S) -> tuple[int, str, str]:
    proc = subprocess.run(argv, input=input_text, capture_output=True, text=True,
                          timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def _python_prefix() -> str:
    """The interpreter every skill's data tool runs under. One allowlist entry keyed on this
    prefix covers all of them, so a skill using a script we have never heard of still works."""
    return os.environ.get("SWING_PYTHON", f"{_STOCKANALYZE}/.venv/bin/python")


def _addendum(mode: str) -> str:
    target = ("the ONE stock named in the user message"
              if mode == "one" else
              "the market — pick your own names, exactly as the skill's screening mode does")
    extra = "" if mode == "one" else f"""
Return AT MOST {MAX_PICKS} picks in `picks`, best first. Fewer — or none at all — is a valid
answer when your own bar is not met; do NOT pad the list to reach five.
"""
    return f"""

# MANUAL ANALYSIS MODE (headless, operator-initiated)

A human operator has pointed this skill at {target} and will read your answer directly.
Apply the FULL skill above: run its data tools, follow its decision hierarchy, and use web
search for same-day news.

Return JSON matching the enforced schema:

- `report` — your COMPLETE analysis in markdown, at the depth you would give a person in
  conversation: the steps you ran, what the data showed, what you concluded and why. A human
  reads this, not a parser. Do not compress it to bullet fragments.
- `verdict` — your call in THIS skill's own vocabulary (BUY NOW, WAIT, HOLD, EXIT,
  IGNITION_BUY, whatever this skill natively emits).
- `conviction` — 0-100, or null if this skill does not express one.
- `entry`, `stop`, `target1`, `target2`, `target3`, `risk_reward` — the actionable levels.
  Use null for any level this skill does not produce. NEVER invent one to fill a field.
- `summary` — one line a trader can act on.
- `symbol` — the NSE symbol this item is about.
{extra}
No prose outside the JSON.
"""


def _num(v):
    return None if v is None else float(v)


def _parse_item(raw: dict, fallback_symbol: str = "") -> dict:
    if not isinstance(raw, dict):
        raise ManualEngineError(f"expected an object, got {type(raw).__name__}")
    report = str(raw.get("report") or "").strip()
    verdict = str(raw.get("verdict") or "").strip()
    if not report:
        raise ManualEngineError("reply has no `report` — nothing for the operator to read")
    if not verdict:
        raise ManualEngineError("reply has no `verdict`")
    conv = raw.get("conviction")
    return {"symbol": str(raw.get("symbol") or fallback_symbol).strip().upper(),
            "report": report, "verdict": verdict,
            "conviction": int(conv) if conv is not None else None,
            "entry": _num(raw.get("entry")), "stop": _num(raw.get("stop")),
            "target1": _num(raw.get("target1")), "target2": _num(raw.get("target2")),
            "target3": _num(raw.get("target3")),
            "risk_reward": _num(raw.get("risk_reward")),
            "summary": str(raw.get("summary") or "")}


def _loads(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ManualEngineError(f"no JSON object in reply: {text[:200]!r}")
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            raise ManualEngineError(f"could not parse JSON: {e}") from e


class ManualSkillEngine:
    """Runs ONE installed skill, either on a named symbol or as its own top-5 screen."""

    def __init__(self, skill_id: str,
                 runner: Callable[..., tuple[int, str, str]] = _default_runner,
                 model: str = MODEL, claude_bin: str | None = None,
                 skills_dir: str = SKILLS_DIR, use_web_search: bool = True):
        self.skill_id = skill_id
        self.runner = runner
        self.model = model
        self.claude_bin = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
        self.skills_dir = skills_dir
        self.use_web_search = use_web_search

    def _system(self, mode: str) -> str:
        path = os.path.join(self.skills_dir, self.skill_id, "SKILL.md")
        if not os.path.exists(path):
            raise ManualEngineError(f"skill not found at {path}")
        with open(path, encoding="utf-8") as f:
            return f.read() + _addendum(mode)

    def _allowed_tools(self) -> str:
        tools = [f"Bash({_python_prefix()}:*)"]
        if self.use_web_search:
            tools.append("WebSearch")
        return ",".join(tools)

    def _call(self, mode: str, schema: dict, user_message: str) -> tuple[str, dict]:
        argv = [self.claude_bin, "-p", "--output-format", "json", "--model", self.model,
                "--append-system-prompt", self._system(mode),
                "--json-schema", json.dumps(schema),
                "--allowedTools", self._allowed_tools()]
        try:
            rc, out, err = self.runner(argv, user_message)
        except Exception as e:                                       # noqa: BLE001
            raise ManualEngineError(f"{self.skill_id} call failed: {e}") from e
        if rc != 0:
            raise ManualEngineError(
                f"claude CLI exit {rc} for {self.skill_id}: {err.strip()[:300]}")
        if not out or not out.strip():
            raise ManualEngineError(f"claude CLI returned empty output for {self.skill_id}")
        return out, _loads(_result_text(out))

    def run_symbol(self, symbol: str, position: dict | None = None) -> dict:
        """Full analysis of ONE symbol. `position` (quantity/avg_price/product) is passed to
        the skill as held-position context so its exit logic can engage."""
        if position:
            ctx = (f"You currently HOLD this: {position.get('quantity')} @ avg "
                   f"₹{position.get('avg_price')} ({position.get('product') or 'MIS'}).")
        else:
            ctx = "You have no open position in this stock — judge it as a fresh entry."
        msg = f"Analyze {symbol.upper()} now.\n{ctx}"
        raw, obj = self._call("one", ONE_SCHEMA, msg)
        return dict(_parse_item(obj, symbol), raw=raw)

    def run_top5(self) -> list[dict]:
        """The skill's own screen — it picks the names. One call, up to MAX_PICKS items."""
        msg = (f"Run your screening mode and give me your top {MAX_PICKS} candidates right "
               "now, best first.")
        raw, obj = self._call("top5", TOP5_SCHEMA, msg)
        picks = obj.get("picks")
        if not isinstance(picks, list):
            raise ManualEngineError(f"reply missing 'picks' list: {str(obj)[:200]!r}")
        return [dict(_parse_item(p), raw=raw) for p in picks]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_manual_engine.py -q`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add manual_engine.py tests/test_manual_engine.py
git commit -m "feat(analyze): generic manual skill runner"
```

---

### Task 2: Store — positions snapshot and analysis run tables

**Files:**
- Modify: `store.py` — `_SCHEMA` (after the `holdings` table, ~line 425), migration block (~line 634), new methods after `holdings_fetched_at` (~line 1349)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: engine item dicts from Task 1 (`symbol, report, verdict, conviction, entry, stop, target1..3, risk_reward, summary, raw`).
- Produces, on `Store`:
  - `replace_positions(positions: list[dict]) -> None` / `get_positions() -> list[dict]` / `positions_fetched_at() -> str | None`
  - `start_analysis_run(skill_id: str, mode: str) -> int` (mode is `"symbols"` or `"top5"`)
  - `set_analysis_pid(run_id: int, pid: int) -> None`
  - `seed_analysis_results(run_id: int, symbols: list[str]) -> None`
  - `update_analysis_result(run_id, symbol, status, item=None, raw=None, error=None) -> None`
  - `add_analysis_result(run_id, item, raw) -> None` (top-5: the symbols are unknown until the skill answers)
  - `finish_analysis_run(run_id, status, num_symbols=0, error=None) -> None`
  - `analysis_progress(run_id) -> dict` with keys `total, done, pending, analyzing, errors`
  - `latest_analysis_run() -> dict | None`, `get_analysis_runs(limit=30) -> list[dict]`, `get_analysis_results(run_id) -> list[dict]`
  - Tasks 3 and 4 call exactly these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
# ---- Analyze page: positions snapshot + manual analysis runs ---------------------------

def _item(symbol="KEI", **kw):
    base = {"symbol": symbol, "report": "## Analysis\nfull text", "verdict": "BUY NOW",
            "conviction": 78, "entry": 1842.0, "stop": 1808.0, "target1": 1905.0,
            "target2": None, "target3": None, "risk_reward": 1.9, "summary": "one line"}
    base.update(kw)
    return base


def test_positions_snapshot_roundtrips_and_replaces():
    store = Store(":memory:")
    assert store.get_positions() == []
    assert store.positions_fetched_at() is None
    store.replace_positions([{"symbol": "KEI", "quantity": 40, "avg_price": 1830.5,
                              "product": "MIS"}])
    rows = store.get_positions()
    assert len(rows) == 1 and rows[0]["symbol"] == "KEI" and rows[0]["product"] == "MIS"
    assert store.positions_fetched_at()
    store.replace_positions([{"symbol": "PNGSREVA", "quantity": 10, "avg_price": 99.0,
                              "product": "CNC"}])
    assert [r["symbol"] for r in store.get_positions()] == ["PNGSREVA"]


def test_analysis_run_seeds_and_fills():
    store = Store(":memory:")
    rid = store.start_analysis_run("intraday-analyst-2", "symbols")
    store.seed_analysis_results(rid, ["KEI", "PNGSREVA"])
    assert store.analysis_progress(rid) == {"total": 2, "done": 0, "pending": 2,
                                            "analyzing": 0, "errors": 0}
    store.update_analysis_result(rid, "KEI", "ANALYZING")
    store.update_analysis_result(rid, "KEI", "DONE", item=_item(), raw='{"envelope": 1}')
    store.update_analysis_result(rid, "PNGSREVA", "ERROR", error="tool timeout")
    store.finish_analysis_run(rid, "SUCCESS", num_symbols=2)

    run = store.latest_analysis_run()
    assert run["status"] == "SUCCESS" and run["skill_id"] == "intraday-analyst-2"
    assert run["mode"] == "symbols" and run["finished_at"]
    rows = {r["symbol"]: r for r in store.get_analysis_results(rid)}
    assert rows["KEI"]["verdict"] == "BUY NOW" and rows["KEI"]["entry"] == 1842.0
    assert rows["KEI"]["report"].startswith("## Analysis")
    assert rows["KEI"]["raw_json"] == '{"envelope": 1}' and rows["KEI"]["analyzed_at"]
    assert rows["KEI"]["target2"] is None
    assert rows["PNGSREVA"]["status"] == "ERROR" and rows["PNGSREVA"]["error"] == "tool timeout"
    p = store.analysis_progress(rid)
    assert p["done"] == 2 and p["errors"] == 1 and p["pending"] == 0


def test_add_analysis_result_inserts_for_top5():
    """Top-5 mode learns its symbols only from the reply, so rows are inserted, not seeded."""
    store = Store(":memory:")
    rid = store.start_analysis_run("intraday-breakout", "top5")
    assert store.get_analysis_results(rid) == []
    store.add_analysis_result(rid, _item("CELLO"), '{"e": 1}')
    store.add_analysis_result(rid, _item("OMNI", verdict="WAIT"), '{"e": 2}')
    rows = store.get_analysis_results(rid)
    assert [r["symbol"] for r in rows] == ["CELLO", "OMNI"]
    assert rows[0]["status"] == "DONE" and rows[1]["verdict"] == "WAIT"
    assert store.analysis_progress(rid)["done"] == 2


def test_analysis_run_failure_records_error():
    store = Store(":memory:")
    rid = store.start_analysis_run("swing-analyst", "top5")
    store.finish_analysis_run(rid, "FAILED", error="no groww creds")
    run = store.latest_analysis_run()
    assert run["status"] == "FAILED" and run["error"] == "no groww creds"


def test_analysis_runs_empty_and_newest_first():
    store = Store(":memory:")
    assert store.latest_analysis_run() is None and store.get_analysis_runs() == []
    a = store.start_analysis_run("s1", "symbols")
    b = store.start_analysis_run("s2", "top5")
    assert [r["id"] for r in store.get_analysis_runs()] == [b, a]
    assert store.latest_analysis_run()["id"] == b


def test_analysis_pid_roundtrips():
    store = Store(":memory:")
    rid = store.start_analysis_run("s1", "symbols")
    store.set_analysis_pid(rid, 4242)
    assert store.latest_analysis_run()["pid"] == 4242
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -q -k "positions_snapshot or analysis"`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'replace_positions'`

- [ ] **Step 3: Implement**

In `_SCHEMA`, after the `holdings` table's closing `);` and before `CREATE TABLE IF NOT EXISTS swing_runs`:

```sql
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    quantity INTEGER,
    avg_price REAL,
    product TEXT,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    num_symbols INTEGER,
    error TEXT,
    pid INTEGER
);
CREATE TABLE IF NOT EXISTS analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES analysis_runs(id),
    symbol TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    analyzed_at TEXT,
    verdict TEXT, conviction INTEGER,
    entry REAL, stop REAL, target1 REAL, target2 REAL, target3 REAL, risk_reward REAL,
    summary TEXT, report TEXT, raw_json TEXT, error TEXT
);
```

No `ALTER TABLE` migration is needed: these are whole new tables and `CREATE TABLE IF NOT EXISTS` runs on every open, so an existing DB gains them on next start.

Add these methods to `Store`, immediately after `holdings_fetched_at`:

```python
    # ---- Analyze page: live position snapshot + manual skill runs -------------------------
    def replace_positions(self, positions: list[dict]) -> None:
        """Persist the latest intraday (MIS) position snapshot, replacing the previous one, so
        the Analyze page survives Streamlit's reruns without re-hitting the broker."""
        now = _utc_now()
        self._conn.execute("DELETE FROM positions")
        for p in positions:
            self._conn.execute(
                "INSERT INTO positions (symbol, quantity, avg_price, product, fetched_at) "
                "VALUES (?,?,?,?,?)", (p["symbol"], p.get("quantity"), p.get("avg_price"),
                                       p.get("product"), now))
        self._conn.commit()

    def get_positions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT symbol, quantity, avg_price, product, fetched_at "
            "FROM positions ORDER BY symbol").fetchall()
        return [dict(r) for r in rows]

    def positions_fetched_at(self) -> str | None:
        r = self._conn.execute("SELECT MAX(fetched_at) AS t FROM positions").fetchone()
        return r["t"] if r and r["t"] else None

    def start_analysis_run(self, skill_id: str, mode: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO analysis_runs (started_at, status, skill_id, mode) "
            "VALUES (?, 'RUNNING', ?, ?)", (_utc_now(), skill_id, mode))
        self._conn.commit()
        return int(cur.lastrowid)

    def set_analysis_pid(self, run_id: int, pid: int) -> None:
        self._conn.execute("UPDATE analysis_runs SET pid = ? WHERE id = ?", (pid, run_id))
        self._conn.commit()

    def finish_analysis_run(self, run_id: int, status: str, num_symbols: int = 0,
                            error: str | None = None) -> None:
        self._conn.execute(
            "UPDATE analysis_runs SET finished_at = ?, status = ?, num_symbols = ?, error = ? "
            "WHERE id = ?", (_utc_now(), status, num_symbols, error, run_id))
        self._conn.commit()

    def seed_analysis_results(self, run_id: int, symbols: list[str]) -> None:
        for sym in symbols:
            self._conn.execute(
                "INSERT INTO analysis_results (run_id, symbol, status) VALUES (?,?, 'PENDING')",
                (run_id, sym))
        self._conn.commit()

    _ANALYSIS_COLS = ("verdict", "conviction", "entry", "stop", "target1", "target2",
                      "target3", "risk_reward", "summary", "report")

    def update_analysis_result(self, run_id: int, symbol: str, status: str,
                               item: dict | None = None, raw: str | None = None,
                               error: str | None = None) -> None:
        """Move one seeded row to `status` and, when the analysis is ready, write its fields.
        Terminal states (DONE / ERROR) stamp analyzed_at; ANALYZING leaves the prior stamp."""
        stamp = _utc_now() if status in ("DONE", "ERROR") else None
        if item is None:
            self._conn.execute(
                "UPDATE analysis_results SET status = ?, "
                "analyzed_at = COALESCE(?, analyzed_at), error = COALESCE(?, error) "
                "WHERE run_id = ? AND symbol = ?", (status, stamp, error, run_id, symbol))
        else:
            sets = ", ".join(f"{c} = ?" for c in self._ANALYSIS_COLS)
            vals = [item.get(c) for c in self._ANALYSIS_COLS]
            self._conn.execute(
                f"UPDATE analysis_results SET status = ?, analyzed_at = ?, raw_json = ?, "
                f"error = ?, {sets} WHERE run_id = ? AND symbol = ?",
                [status, stamp, raw, error, *vals, run_id, symbol])
        self._conn.commit()

    def add_analysis_result(self, run_id: int, item: dict, raw: str | None = None) -> None:
        """Insert a finished row whose symbol was not known when the run started — the top-5
        mode learns its names only from the skill's reply."""
        cols = ", ".join(self._ANALYSIS_COLS)
        marks = ", ".join("?" for _ in self._ANALYSIS_COLS)
        self._conn.execute(
            f"INSERT INTO analysis_results (run_id, symbol, status, analyzed_at, raw_json, "
            f"{cols}) VALUES (?,?,'DONE',?,?,{marks})",
            [run_id, item["symbol"], _utc_now(), raw,
             *[item.get(c) for c in self._ANALYSIS_COLS]])
        self._conn.commit()

    def analysis_progress(self, run_id: int) -> dict:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) n FROM analysis_results WHERE run_id = ? GROUP BY status",
            (run_id,)).fetchall()
        by = {r["status"]: r["n"] for r in rows}
        return {"total": sum(by.values()),
                "done": by.get("DONE", 0) + by.get("ERROR", 0),
                "pending": by.get("PENDING", 0), "analyzing": by.get("ANALYZING", 0),
                "errors": by.get("ERROR", 0)}

    def latest_analysis_run(self) -> dict | None:
        r = self._conn.execute(
            "SELECT * FROM analysis_runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def get_analysis_runs(self, limit: int = 30) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM analysis_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_analysis_results(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM analysis_results WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run the store tests**

Run: `.venv/bin/python -m pytest tests/test_store.py -q`
Expected: all PASS (`test_init_creates_all_tables` picks the new tables up automatically)

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "feat(analyze): positions snapshot and analysis run tables"
```

---

### Task 3: `analysis_job.py` — the detached runner

**Files:**
- Create: `analysis_job.py`
- Test: `tests/test_analysis_job.py`

**Interfaces:**
- Consumes: `ManualSkillEngine` (Task 1), the `Store` methods (Task 2).
- Produces:
  - `run_analysis(store, engine, run_id) -> int` — symbols mode: walks the run's PENDING rows.
  - `run_top5(store, engine, run_id) -> int` — one call, inserts each pick.
  - `main()` reading `--run <id>`; the run row and its seeded symbols already exist (the dashboard creates them), so the job needs no other argument and never authenticates to Groww.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analysis_job.py`:

```python
"""The detached Analyze runner: one symbol failing must not cost the rest of the run."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis_job import run_analysis, run_top5
from store import Store


def _item(symbol="KEI", **kw):
    base = {"symbol": symbol, "report": "## r", "verdict": "BUY NOW", "conviction": 70,
            "entry": 10.0, "stop": 9.0, "target1": 12.0, "target2": None, "target3": None,
            "risk_reward": 2.0, "summary": "s"}
    base.update(kw)
    return base


class _Engine:
    """Stub with the ManualSkillEngine surface the job actually uses."""

    def __init__(self, fail_on=(), picks=None):
        self.fail_on, self.picks, self.seen = set(fail_on), picks or [], []

    def run_symbol(self, symbol, position=None):
        self.seen.append((symbol, position))
        if symbol in self.fail_on:
            raise RuntimeError("tool blew up")
        return dict(_item(symbol), raw='{"env": 1}')

    def run_top5(self):
        return [dict(p, raw='{"env": 1}') for p in self.picks]


def _run_with(symbols):
    store = Store(":memory:")
    rid = store.start_analysis_run("intraday-analyst-2", "symbols")
    store.seed_analysis_results(rid, symbols)
    return store, rid


def test_every_symbol_is_analyzed_and_marked_done():
    store, rid = _run_with(["KEI", "PNGSREVA"])
    eng = _Engine()
    run_analysis(store, eng, rid)
    rows = {r["symbol"]: r for r in store.get_analysis_results(rid)}
    assert rows["KEI"]["status"] == "DONE" and rows["PNGSREVA"]["status"] == "DONE"
    assert rows["KEI"]["verdict"] == "BUY NOW" and rows["KEI"]["raw_json"] == '{"env": 1}'
    assert store.latest_analysis_run()["status"] == "SUCCESS"


def test_one_failure_does_not_stop_the_run():
    store, rid = _run_with(["KEI", "BOOM", "PNGSREVA"])
    run_analysis(store, _Engine(fail_on=["BOOM"]), rid)
    rows = {r["symbol"]: r for r in store.get_analysis_results(rid)}
    assert rows["BOOM"]["status"] == "ERROR" and "blew up" in rows["BOOM"]["error"]
    assert rows["KEI"]["status"] == "DONE" and rows["PNGSREVA"]["status"] == "DONE"
    assert store.latest_analysis_run()["status"] == "SUCCESS"
    assert store.analysis_progress(rid)["errors"] == 1


def test_position_context_is_passed_when_the_symbol_is_held():
    store, rid = _run_with(["KEI"])
    store.replace_positions([{"symbol": "KEI", "quantity": 40, "avg_price": 1830.5,
                              "product": "MIS"}])
    eng = _Engine()
    run_analysis(store, eng, rid)
    assert eng.seen[0][1]["quantity"] == 40


def test_flat_symbol_gets_no_position_context():
    store, rid = _run_with(["NOTHELD"])
    eng = _Engine()
    run_analysis(store, eng, rid)
    assert eng.seen[0][1] is None


def test_already_done_rows_are_skipped_on_rerun():
    store, rid = _run_with(["KEI", "PNGSREVA"])
    store.update_analysis_result(rid, "KEI", "DONE", item=_item("KEI"), raw="{}")
    eng = _Engine()
    run_analysis(store, eng, rid)
    assert [s for s, _ in eng.seen] == ["PNGSREVA"]


def test_top5_inserts_each_pick():
    store = Store(":memory:")
    rid = store.start_analysis_run("intraday-breakout", "top5")
    run_top5(store, _Engine(picks=[_item("CELLO"), _item("OMNI")]), rid)
    rows = store.get_analysis_results(rid)
    assert [r["symbol"] for r in rows] == ["CELLO", "OMNI"]
    assert store.latest_analysis_run()["status"] == "SUCCESS"
    assert store.latest_analysis_run()["num_symbols"] == 2


def test_top5_engine_failure_marks_run_failed():
    class Boom:
        def run_top5(self):
            raise RuntimeError("screener down")
    store = Store(":memory:")
    rid = store.start_analysis_run("intraday-breakout", "top5")
    run_top5(store, Boom(), rid)
    run = store.latest_analysis_run()
    assert run["status"] == "FAILED" and "screener down" in run["error"]


def test_top5_empty_is_a_successful_run():
    store = Store(":memory:")
    rid = store.start_analysis_run("intraday-breakout", "top5")
    run_top5(store, _Engine(picks=[]), rid)
    assert store.latest_analysis_run()["status"] == "SUCCESS"
    assert store.get_analysis_results(rid) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_analysis_job.py -q`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'analysis_job'`

- [ ] **Step 3: Implement**

Create `analysis_job.py`:

```python
#!/usr/bin/env python3
"""Manual-analysis job — launched detached by the dashboard's Analyze page. The run row and,
in symbols mode, its PENDING rows already exist (the dashboard creates them so the UI has
something to show the instant you click), so this job needs only the run id.

Fully defensive: a single symbol failing marks just that row ERROR and the run continues —
losing four good analyses because the fifth stock's data tool timed out would be absurd.
Never places orders; never authenticates to Groww.
See docs/superpowers/specs/2026-08-12-manual-analysis-page-design.md."""
from __future__ import annotations

import logging
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("autointraday.analysis")


def _install_quiet_sigterm() -> None:
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))


def run_analysis(store, engine, run_id: int) -> int:
    """Analyze every still-PENDING symbol of `run_id`, one at a time so the UI can show
    progress. Held symbols get their position as context. Never raises."""
    _install_quiet_sigterm()
    store.set_analysis_pid(run_id, os.getpid())
    by_symbol = {p["symbol"]: p for p in store.get_positions()}
    pending = [r["symbol"] for r in store.get_analysis_results(run_id)
               if r["status"] == "PENDING"]
    for symbol in pending:
        store.update_analysis_result(run_id, symbol, "ANALYZING")
        try:
            item = engine.run_symbol(symbol, position=by_symbol.get(symbol))
            store.update_analysis_result(run_id, symbol, "DONE", item=item,
                                         raw=item.get("raw"))
        except Exception as e:                                       # noqa: BLE001
            log.warning("analysis failed for %s: %s", symbol, e)
            store.update_analysis_result(run_id, symbol, "ERROR", error=str(e)[:500])
    total = store.analysis_progress(run_id)["total"]
    store.finish_analysis_run(run_id, "SUCCESS", num_symbols=total)
    log.info("analysis run %d done: %d symbol(s)", run_id, total)
    return run_id


def run_top5(store, engine, run_id: int) -> int:
    """One call in which the skill screens and names its own picks. A failure here has no
    per-symbol granularity to fall back on, so the whole run is marked FAILED."""
    _install_quiet_sigterm()
    store.set_analysis_pid(run_id, os.getpid())
    try:
        picks = engine.run_top5()
    except Exception as e:                                           # noqa: BLE001
        log.exception("top-5 run failed")
        store.finish_analysis_run(run_id, "FAILED", error=str(e)[:500])
        return run_id
    for item in picks:
        store.add_analysis_result(run_id, item, item.get("raw"))
    store.finish_analysis_run(run_id, "SUCCESS", num_symbols=len(picks))
    log.info("top-5 run %d done: %d pick(s)", run_id, len(picks))
    return run_id


def main() -> int:
    from settings import load_settings
    settings = load_settings()
    settings.apply_to_environ()

    from store import Store
    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
    store = Store(settings.db_path)

    run_id = int(sys.argv[sys.argv.index("--run") + 1])
    run = next((r for r in store.get_analysis_runs(limit=200) if r["id"] == run_id), None)
    if run is None:
        log.error("no analysis run %d", run_id)
        return 1

    from manual_engine import ManualSkillEngine
    engine = ManualSkillEngine(run["skill_id"])
    if run["mode"] == "top5":
        run_top5(store, engine, run_id)
    else:
        run_analysis(store, engine, run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_analysis_job.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add analysis_job.py tests/test_analysis_job.py
git commit -m "feat(analyze): detached manual-analysis job"
```

---

### Task 4: The Analyze page

**Files:**
- Modify: `dashboard.py` — CSS block (after the swing table rules, ~line 405), new page section placed immediately before `_skill_lab_page` (~line 1671), nav wiring in `main()` (~line 2229)
- Test: `tests/test_dashboard_analysis.py`

**Interfaces:**
- Consumes: `Store` methods (Task 2), `analysis_job.py` (Task 3), `observe.available_skills`.
- Produces (pure, unit-testable helpers plus Streamlit glue):
  - `_selected_symbols(rows: list[dict]) -> list[str]` — the ticked rows of the editor's output.
  - `_analysis_levels(r: dict) -> list[tuple[str, str]]` — label/value pairs for the levels table, omitting levels the skill left null.
  - `_analysis_card(r: dict) -> str` — the Formatted tab's HTML.
  - `_analysis_page()`, `_analysis_live()`, `_launch_analysis(run_id)`, `_refresh_positions_from_groww()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_analysis.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_analysis.py -q`
Expected: FAIL — `AttributeError: module 'dashboard' has no attribute '_selected_symbols'`

- [ ] **Step 3: Implement the helpers and page**

(a) Add CSS after the `.ai-eta-past` rule:

```css
/* ---- Analyze page: result card ------------------------------------------------------ */
.ai-acard { display: flex; flex-wrap: wrap; gap: .55rem 1.2rem; align-items: center;
  margin: 0 0 .6rem; padding: .6rem .75rem; border-radius: 10px; background: var(--ai-tint);
  border: 1px solid var(--ai-line-soft); font-size: .86rem; }
.ai-averdict { font-weight: 700; letter-spacing: .02em; }
.ai-aconv { opacity: .7; font-size: .8rem; }
.ai-alevels { display: flex; flex-wrap: wrap; gap: .3rem .9rem; flex: 1 1 100%;
  font-variant-numeric: tabular-nums; }
.ai-alevel b { opacity: .6; font-weight: 600; margin-right: .3rem; font-size: .78rem; }
.ai-asummary { flex: 1 1 100%; opacity: .85; font-style: italic; }
.ai-aerr { flex: 1 1 100%; color: #e5484d; }
```

(b) Insert this section immediately before `def _skill_lab_page() -> None:`:

```python
# ---- Analyze: manual, on-demand skill runs against the live position book -----------------
_ANALYSIS_LEVELS = (("entry", "Entry"), ("stop", "Stop"), ("target1", "T1"),
                    ("target2", "T2"), ("target3", "T3"), ("risk_reward", "R:R"))


def _selected_symbols(rows) -> list[str]:
    """The symbols ticked in the position editor, in display order."""
    return [str(r["Symbol"]) for r in (rows or [])
            if r.get("Analyze") and r.get("Symbol")]


def _analysis_levels(r: dict) -> list[tuple[str, str]]:
    """Label/value pairs for the levels the skill ACTUALLY produced. A null level is dropped
    rather than shown as a dash — the scanner genuinely has no entry price, and a row of
    em dashes reads like a bug."""
    out = []
    for key, label in _ANALYSIS_LEVELS:
        v = r.get(key)
        if v is None:
            continue
        out.append((label, f"{v:,.2f}" if key != "risk_reward" else f"{v:g}"))
    return out


def _analysis_card(r: dict) -> str:
    """The Formatted tab: verdict, conviction, the levels that exist, and the one-liner."""
    import html
    bits = [f'<span class="ai-averdict">{html.escape(str(r.get("verdict") or "—"))}</span>']
    if r.get("conviction") is not None:
        bits.append(f'<span class="ai-aconv">conviction {int(r["conviction"])}</span>')
    levels = _analysis_levels(r)
    if levels:
        cells = "".join(f'<span class="ai-alevel"><b>{html.escape(lbl)}</b>{val}</span>'
                        for lbl, val in levels)
        bits.append(f'<div class="ai-alevels">{cells}</div>')
    if r.get("summary"):
        bits.append(f'<div class="ai-asummary">{html.escape(str(r["summary"]))}</div>')
    if r.get("error"):
        bits.append(f'<div class="ai-aerr">⚠ {html.escape(str(r["error"]))}</div>')
    return f'<div class="ai-acard">{"".join(bits)}</div>'


def _refresh_positions_from_groww() -> None:
    """Fetch the intraday (MIS) position book and persist the snapshot. Delivery holdings are
    the Swing page's job and deliberately not fetched here."""
    from settings import load_settings
    from groww_client import GrowwClient
    load_settings().apply_to_environ()
    client = GrowwClient(mode="live")
    client.authenticate()
    _db(lambda s: s.replace_positions(client.get_positions()))


def _launch_analysis(run_id: int) -> None:
    """Fire the analysis as a detached subprocess so the UI never blocks."""
    import subprocess
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    subprocess.Popen([sys.executable, os.path.join(here, "analysis_job.py"),
                      "--run", str(run_id)],
                     cwd=here, env=dict(os.environ), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@st.fragment(run_every=4)
def _analysis_live() -> None:
    """Progress + results for the latest run, auto-refreshing so a running analysis fills in
    without a manual reload."""
    import json as _json
    latest = _db(lambda s: s.latest_analysis_run())
    if latest is None:
        st.caption("No analysis run yet — pick a skill and some stocks above.")
        return
    results = _db(lambda s: s.get_analysis_results(latest["id"]))
    head = (f"Run #{latest['id']} · **{latest['skill_id']}** · "
            f"{'top 5' if latest['mode'] == 'top5' else 'selected positions'} · "
            f"{_fmt_ist_short(latest['started_at']) or ''}")
    st.markdown(head)
    if latest["status"] == "RUNNING":
        prog = _db(lambda s: s.analysis_progress(latest["id"]))
        if prog["total"]:
            st.progress(prog["done"] / prog["total"],
                        text=f"⏳ {prog['done']}/{prog['total']} done"
                             + (f" · {prog['errors']} errors" if prog["errors"] else ""))
        else:
            st.progress(0.0, text="⏳ Asking the skill for its top picks…")
    elif latest["status"] == "FAILED":
        st.error(latest["error"] or "The run failed.")

    for r in results:
        if r["status"] in ("PENDING", "ANALYZING"):
            st.caption(f"{r['symbol']} — {'⏳ analyzing' if r['status'] == 'ANALYZING' else '· waiting'}")
            continue
        label = f"{r['symbol']} — {r['verdict'] or ('⚠ error' if r['status'] == 'ERROR' else '—')}"
        with st.expander(label, expanded=len(results) == 1):
            t_fmt, t_raw = st.tabs(["Formatted", "Raw"])
            with t_fmt:
                st.markdown(_analysis_card(r), unsafe_allow_html=True)
            with t_raw:
                if r["report"]:
                    st.markdown(r["report"])
                if r["raw_json"]:
                    st.caption("Claude CLI envelope")
                    try:
                        st.json(_json.loads(r["raw_json"]))
                    except Exception:                                # noqa: BLE001
                        st.code(r["raw_json"])
                if not r["report"] and not r["raw_json"]:
                    st.caption("No output recorded for this stock.")


def _analysis_page() -> None:
    import pandas as pd
    from observe import available_skills

    st.markdown('<div class="ai-brand">Analyze<em>.</em></div>', unsafe_allow_html=True)
    st.caption("Your live Groww intraday positions, analyzed on demand by whichever skill you "
               "choose — the skill's full reasoning under Raw, its actionable numbers under "
               "Formatted. Analysis only: this page has no code path to an order.")

    positions = _db(lambda s: s.get_positions())
    fetched_at = _db(lambda s: s.positions_fetched_at())
    latest = _db(lambda s: s.latest_analysis_run())
    running = bool(latest and latest["status"] == "RUNNING")

    skills = available_skills()
    if not skills:
        st.error("No skills found in ~/.claude/skills")
        return

    top = st.columns([1.4, 2.2, 2.4], vertical_alignment="center")
    with top[0]:
        if st.button("Fetch positions", use_container_width=True, disabled=running):
            try:
                with st.spinner("Fetching from Groww…"):
                    _refresh_positions_from_groww()
            except Exception as e:                                   # noqa: BLE001
                st.error(f"Could not load positions: {e}")
            st.rerun()
    with top[1]:
        skill = st.selectbox("Skill", skills, key="analysis_skill",
                             label_visibility="collapsed")
    with top[2]:
        if fetched_at:
            st.caption(f"Positions as of {_fmt_ist(fetched_at) or fetched_at} · "
                       f"{len(positions)} open")
        else:
            st.caption("No positions loaded — click **Fetch positions**.")

    picked: list[str] = []
    if positions:
        table = [{"Analyze": False, "Symbol": p["symbol"], "Qty": p.get("quantity"),
                  "Avg": p.get("avg_price"), "Product": p.get("product")}
                 for p in positions]
        edited = st.data_editor(
            pd.DataFrame(table), hide_index=True, use_container_width=True,
            disabled=["Symbol", "Qty", "Avg", "Product"], key="analysis_pick",
            column_config={"Analyze": st.column_config.CheckboxColumn(
                "Analyze", help="Tick the stocks to send to the chosen skill.")})
        picked = _selected_symbols(edited.to_dict("records"))

    act = st.columns([1.6, 1.6, 3], vertical_alignment="center")
    with act[0]:
        if st.button(f"Analyze {len(picked)} selected", use_container_width=True,
                     type="primary", disabled=running or not picked):
            rid = _db(lambda s: s.start_analysis_run(skill, "symbols"))
            _db(lambda s: s.seed_analysis_results(rid, picked))
            _launch_analysis(rid)
            st.rerun()
    with act[1]:
        if st.button("Top 5 from this skill", use_container_width=True, disabled=running):
            rid = _db(lambda s: s.start_analysis_run(skill, "top5"))
            _launch_analysis(rid)
            st.rerun()
    with act[2]:
        if picked:
            st.caption(f"{len(picked)} stock(s) x ~2 min ≈ **{len(picked) * 2} minutes**. "
                       "The run continues if you navigate away.")
        else:
            st.caption("Top 5 is a single call — the skill runs its own screen and picks its "
                       "own names.")

    st.divider()
    _analysis_live()
```

(c) Wire it into `main()`, after the `lab` page:

```python
    analyze = st.Page(_analysis_page, title="Analyze", url_path="analysis")
    pages = [intraday, swing, live, ashort, lab, analyze]
```

(replacing the existing `pages = [intraday, swing, live, ashort, lab]` line).

- [ ] **Step 4: Run the page tests**

Run: `.venv/bin/python -m pytest tests/test_dashboard_analysis.py -q`
Expected: 9 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 6: Verify the app imports and the page is registered**

Run: `.venv/bin/python -c "import dashboard; print('ok', dashboard._analysis_page.__name__)"`
Expected: `ok _analysis_page` with no traceback

- [ ] **Step 7: Commit**

```bash
git add dashboard.py tests/test_dashboard_analysis.py
git commit -m "feat(analyze): Analyze page with checkbox picks, raw + formatted tabs"
```
