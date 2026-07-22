# Skill-Driven One-Shot Screening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-name entry screening with ONE headless `claude -p` call per cycle that runs the full intraday-analyst skill and returns the top-5 candidates as typed Decisions.

**Architecture:** New `SkillScreenEngine` builds a system prompt from the full `~/.claude/skills/intraday-analyst/SKILL.md` plus a screening-mode addendum, calls `claude -p` with Bash restricted to the two StockAnalayze scripts, and parses a `{candidates: [...]}` JSON into `(symbol, Decision)` pairs. The orchestrator, when given a screen engine, swaps its candidate loop for one `screen()` call and feeds results through the existing gate/placement. `screen_mode: skill | classic` in config selects the path.

**Tech Stack:** Python 3.13 (`.venv`), pytest, headless Claude CLI (`claude -p`), existing `Decision` dataclass.

## Global Constraints

- Never set/require `ANTHROPIC_API_KEY` (subscription billing via `claude_cli`).
- Gate floors stay exactly `MIN_TRADE_QUALITY = 52`, `MIN_RISK_REWARD = 1.5`, `MIN_CONFIDENCE = 50` — this plan must not touch them.
- A skill-screen failure must NEVER fail the cycle — degrade to 0 candidates, cycle stays SUCCESS (spec: "Failure handling").
- Exits/trailing/OCO/square-off code paths must be untouched.
- Run tests with `.venv/bin/python -m pytest -q` from the repo root; full suite must stay green after every task.
- Working tree is the deploy artifact (launchd runs files directly) — commit per task, never leave the tree broken.

---

### Task 1: `screen_mode` setting

**Files:**
- Modify: `settings.py` (dataclass field, `_ENV_MAP`, `load_settings`)
- Modify: `config.yaml`, `config.example.yaml` (add `screen_mode` under `decision:`)
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `Settings.screen_mode: str` (`"skill"` default, `"classic"` rollback), env override `SCREEN_MODE`, exported by `apply_to_environ()`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_settings.py`:

```python
def test_screen_mode_defaults_to_skill(tmp_path):
    s = load_settings(path=str(tmp_path / "missing.yaml"), env={})
    assert s.screen_mode == "skill"


def test_screen_mode_yaml_and_env_precedence(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("decision:\n  screen_mode: classic\n")
    assert load_settings(path=str(cfg), env={}).screen_mode == "classic"
    # env var wins over YAML
    assert load_settings(path=str(cfg), env={"SCREEN_MODE": "skill"}).screen_mode == "skill"


def test_screen_mode_exported_to_environ(tmp_path):
    s = load_settings(path=str(tmp_path / "missing.yaml"), env={})
    env: dict = {}
    s.apply_to_environ(env)
    assert env["SCREEN_MODE"] == "skill"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_settings.py -q`
Expected: 3 FAILs — `Settings` has no attribute/kwarg `screen_mode`.

- [ ] **Step 3: Implement** — in `settings.py`:

Add to the `Settings` dataclass after `web_search: bool`:

```python
    screen_mode: str
```

Add to `_ENV_MAP` after the `"decision_backend"` entry:

```python
    "screen_mode": "SCREEN_MODE",
```

Add to the `Settings(...)` construction in `load_settings`, after the `web_search=` line:

```python
        screen_mode=pick("SCREEN_MODE", decision.get("screen_mode"), "skill"),
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_settings.py -q` → all pass. Then full suite `.venv/bin/python -m pytest -q` → green.

- [ ] **Step 5: Update both config files** — in `config.yaml` AND `config.example.yaml`, under the `decision:` block add:

```yaml
  screen_mode: skill           # skill (one-shot top-5 via full intraday-analyst skill) | classic (movers screener + per-name decisions)
```

- [ ] **Step 6: Commit**

```bash
git add settings.py tests/test_settings.py config.example.yaml
git commit -m "feat: screen_mode setting (skill | classic), default skill"
```

(`config.yaml` is gitignored — edit it, don't commit it.)

---

### Task 2: `SkillScreenEngine`

**Files:**
- Create: `skill_screen_engine.py`
- Test: `tests/test_skill_screen_engine.py`

**Interfaces:**
- Consumes: `Decision`, `VALID_ACTIONS`, `MODEL`, `DecisionEngineError` from `decision_engine.py`; `_result_text` from `claude_cli_engine.py`.
- Produces: `SkillScreenEngine(runner=..., use_web_search=True, model=MODEL, claude_bin=None, skill_path=SKILL_PATH)` with `.screen(exclude_symbols: Sequence[str]) -> list[tuple[str, Decision]]`; `SkillScreenError(DecisionEngineError)`; `SCREEN_SCHEMA`; `TIMEOUT_S = 1200`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_skill_screen_engine.py`:

```python
import json

import pytest

from decision_engine import Decision
from skill_screen_engine import (SCREEN_SCHEMA, SkillScreenEngine, SkillScreenError)


def _cand(symbol="RELIANCE", action="BUY_NOW", tq=70, conf=65, entry=100.0, stop=95.0,
          target1=110.0, rr=2.0):
    return {"symbol": symbol, "action": action, "confidence": conf, "trade_quality": tq,
            "entry": entry, "stop_loss": stop, "target1": target1, "risk_reward": rr}


def _envelope(payload: dict) -> str:
    # claude -p --output-format json wraps the answer in an envelope's "result" field
    return json.dumps({"type": "result", "is_error": False, "result": json.dumps(payload)})


def _engine(runner, skill_file, **kw):
    return SkillScreenEngine(runner=runner, skill_path=str(skill_file), **kw)


@pytest.fixture
def skill_file(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("# ROLE — test skill body\n")
    return f


def test_screen_parses_candidates_in_order(skill_file):
    payload = {"candidates": [_cand("AAA", tq=80), _cand("BBB", action="SHORT_NOW", tq=60)]}
    eng = _engine(lambda argv, text: (0, _envelope(payload), ""), skill_file)
    out = eng.screen(exclude_symbols=[])
    assert [s for s, _ in out] == ["AAA", "BBB"]
    sym, dec = out[0]
    assert isinstance(dec, Decision)
    assert dec.action == "BUY_NOW" and dec.trade_quality == 80 and dec.entry == 100.0


def test_screen_accepts_bare_json_without_envelope(skill_file):
    payload = {"candidates": [_cand("AAA")]}
    eng = _engine(lambda argv, text: (0, json.dumps(payload), ""), skill_file)
    assert [s for s, _ in eng.screen([])] == ["AAA"]


def test_screen_empty_list_is_valid(skill_file):
    eng = _engine(lambda argv, text: (0, _envelope({"candidates": []}), ""), skill_file)
    assert eng.screen([]) == []


def test_screen_missing_candidates_key_raises(skill_file):
    eng = _engine(lambda argv, text: (0, _envelope({"nope": 1}), ""), skill_file)
    with pytest.raises(SkillScreenError):
        eng.screen([])


def test_screen_invalid_action_raises(skill_file):
    eng = _engine(lambda argv, text: (0, _envelope(
        {"candidates": [_cand(action="YOLO")]}), ""), skill_file)
    with pytest.raises(SkillScreenError):
        eng.screen([])


def test_screen_nonzero_exit_raises(skill_file):
    eng = _engine(lambda argv, text: (1, "", "boom"), skill_file)
    with pytest.raises(SkillScreenError):
        eng.screen([])


def test_screen_empty_stdout_raises(skill_file):
    eng = _engine(lambda argv, text: (0, "", ""), skill_file)
    with pytest.raises(SkillScreenError):
        eng.screen([])


def test_screen_runner_exception_wrapped(skill_file):
    def boom(argv, text):
        raise TimeoutError("timed out")
    eng = _engine(boom, skill_file)
    with pytest.raises(SkillScreenError):
        eng.screen([])


def test_missing_skill_file_raises(tmp_path):
    eng = SkillScreenEngine(runner=lambda a, t: (0, "{}", ""),
                            skill_path=str(tmp_path / "nope.md"))
    with pytest.raises(SkillScreenError):
        eng.screen([])


def test_argv_and_prompt_wiring(skill_file):
    seen = {}

    def spy(argv, text):
        seen["argv"], seen["text"] = argv, text
        return 0, _envelope({"candidates": []}), ""

    eng = _engine(spy, skill_file, claude_bin="/bin/claude")
    eng.screen(exclude_symbols=["HELD1", "HELD2"])
    argv = seen["argv"]
    assert argv[0] == "/bin/claude" and "-p" in argv
    assert "--json-schema" in argv and json.dumps(SCREEN_SCHEMA) in argv
    sys_prompt = argv[argv.index("--append-system-prompt") + 1]
    assert "test skill body" in sys_prompt          # full SKILL.md embedded
    assert "SCREENING MODE" in sys_prompt           # addendum appended
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "WebSearch" in allowed
    assert allowed.count("Bash(") == 2              # exactly the two scripts, nothing else
    assert "groww_intraday_screener.py" in allowed and "stock_analyze_intraday.py" in allowed
    assert "HELD1" in seen["text"] and "HELD2" in seen["text"]   # exclusions in user message


def test_no_web_search_flag(skill_file):
    seen = {}

    def spy(argv, text):
        seen["argv"] = argv
        return 0, _envelope({"candidates": []}), ""

    _engine(spy, skill_file, use_web_search=False).screen([])
    allowed = seen["argv"][seen["argv"].index("--allowedTools") + 1]
    assert "WebSearch" not in allowed and "Bash(" in allowed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_skill_screen_engine.py -q`
Expected: collection error — `No module named 'skill_screen_engine'`.

- [ ] **Step 3: Implement** — create `skill_screen_engine.py`:

```python
"""Skill-driven one-shot screening backend — ONE headless `claude -p` per cycle runs the FULL
intraday-analyst skill (movers screener + indicator tool via restricted Bash, optional web
search) and returns the top-5 candidates as typed Decisions. Selected by SCREEN_MODE=skill.
See docs/superpowers/specs/2026-07-20-skill-screen-design.md."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable, Sequence

from claude_cli_engine import _result_text
from decision_engine import MODEL, VALID_ACTIONS, Decision, DecisionEngineError

_STOCKANALYZE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze"
SKILL_PATH = os.path.expanduser("~/.claude/skills/intraday-analyst/SKILL.md")
# Agentic session: screener + ~10-14 indicator runs + reasoning. An overrun past the 25-min
# cycle spacing only makes the overlap lock skip the next fire — same contract as today.
TIMEOUT_S = 1200
MAX_CANDIDATES = 5

_CANDIDATE_PROPS = {
    "symbol": {"type": "string"},
    "action": {"type": "string", "enum": list(VALID_ACTIONS)},
    "confidence": {"type": "integer"},
    "trade_quality": {"type": "integer"},
    "entry": {"type": ["number", "null"]},
    "stop_loss": {"type": ["number", "null"]},
    "target1": {"type": ["number", "null"]},
    "risk_reward": {"type": ["number", "null"]},
}
SCREEN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": MAX_CANDIDATES,
            "items": {"type": "object", "additionalProperties": False,
                      "properties": _CANDIDATE_PROPS,
                      "required": list(_CANDIDATE_PROPS)},
        },
    },
    "required": ["candidates"],
}


class SkillScreenError(DecisionEngineError):
    """One-shot skill screen failed: missing SKILL.md, CLI error/timeout, or bad JSON."""


def _default_runner(argv: list[str], input_text: str) -> tuple[int, str, str]:
    proc = subprocess.run(argv, input=input_text, capture_output=True, text=True,
                          timeout=TIMEOUT_S)
    return proc.returncode, proc.stdout, proc.stderr


def _screener_cmd() -> str:
    py = os.environ.get("SCREENER_PYTHON", f"{_STOCKANALYZE}/.venv/bin/python")
    script = os.environ.get("SCREENER_SCRIPT", f"{_STOCKANALYZE}/groww_intraday_screener.py")
    return f"{py} {script}"


def _indicator_cmd() -> str:
    py = os.environ.get("INTRADAY_PYTHON", f"{_STOCKANALYZE}/.venv/bin/python")
    script = os.environ.get("INTRADAY_SCRIPT", f"{_STOCKANALYZE}/stock_analyze_intraday.py")
    return f"{py} {script}"


def _addendum() -> str:
    return f"""

# SCREENING MODE (automated, headless)

You are running inside an automated trading system. Apply the full skill above, but instead of
one requested symbol, produce the TOP INTRADAY CANDIDATES RIGHT NOW:

1. Run the movers screener BOTH directions:
   `{_screener_cmd()} --direction up --top 7 --min-price 50 --min-mcap-cr 1000`
   and the same with `--direction down`.
2. Run the indicator tool on each shortlisted name:
   `{_indicator_cmd()} -s <SYMBOL> 2>/dev/null`
   and apply the skill's full methodology to each.
3. Rank by trade-quality score and return AT MOST {MAX_CANDIDATES} candidates worth acting on
   NOW. Only include candidates whose action is BUY_NOW, BUY_ON_PULLBACK, BUY_ON_BREAKOUT or
   SHORT_NOW. Returning fewer — or an empty list — is the CORRECT answer when there is no edge.

Rules:
- Skip any symbol listed as excluded in the user message (already held).
- Derive every score honestly per the skill's bands; do not inflate to fill 5 slots.
- Final output: JSON only, matching the enforced schema. No prose.
"""


def _parse_screen(raw_text: str) -> list[tuple[str, Decision]]:
    try:
        obj = json.loads(raw_text)
    except json.JSONDecodeError:
        start, end = raw_text.find("{"), raw_text.rfind("}")
        if start == -1 or end <= start:
            raise SkillScreenError(f"no JSON object in screen reply: {raw_text[:200]!r}")
        try:
            obj = json.loads(raw_text[start:end + 1])
        except json.JSONDecodeError as e:
            raise SkillScreenError(f"could not parse screen JSON: {e}") from e
    if not isinstance(obj, dict) or not isinstance(obj.get("candidates"), list):
        raise SkillScreenError(f"screen reply missing 'candidates' list: {raw_text[:200]!r}")

    def num(v):
        return None if v is None else float(v)

    out: list[tuple[str, Decision]] = []
    for cand in obj["candidates"]:
        if not isinstance(cand, dict) or not isinstance(cand.get("symbol"), str) \
                or not cand["symbol"].strip():
            raise SkillScreenError(f"bad candidate entry: {cand!r}")
        if cand.get("action") not in VALID_ACTIONS:
            raise SkillScreenError(f"invalid action {cand.get('action')!r} for "
                                   f"{cand['symbol']}")
        out.append((cand["symbol"].strip().upper(), Decision(
            action=cand["action"], confidence=int(cand["confidence"]),
            trade_quality=int(cand["trade_quality"]), entry=num(cand["entry"]),
            stop_loss=num(cand["stop_loss"]), target1=num(cand["target1"]),
            risk_reward=num(cand["risk_reward"]), raw_response=json.dumps(cand))))
    return out


class SkillScreenEngine:
    """One `screen()` call per cycle; returns ranked (symbol, Decision) pairs."""

    def __init__(self, runner: Callable[[list[str], str], tuple[int, str, str]] = _default_runner,
                 use_web_search: bool = True, model: str = MODEL,
                 claude_bin: str | None = None, skill_path: str = SKILL_PATH):
        self.runner = runner
        self.use_web_search = use_web_search
        self.model = model
        self.claude_bin = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
        self.skill_path = skill_path
        self._system_prompt: str | None = None

    def _system(self) -> str:
        if self._system_prompt is None:
            if not os.path.exists(self.skill_path):
                raise SkillScreenError(
                    f"intraday-analyst skill not found at {self.skill_path}")
            with open(self.skill_path, encoding="utf-8") as f:
                self._system_prompt = f.read() + _addendum()
        return self._system_prompt

    def _allowed_tools(self) -> str:
        tools = []
        if self.use_web_search:
            tools.append("WebSearch")
        tools.append(f"Bash({_screener_cmd()}:*)")
        tools.append(f"Bash({_indicator_cmd()}:*)")
        return ",".join(tools)

    def screen(self, exclude_symbols: Sequence[str] = ()) -> list[tuple[str, Decision]]:
        argv = [self.claude_bin, "-p", "--output-format", "json", "--model", self.model,
                "--append-system-prompt", self._system(),
                "--json-schema", json.dumps(SCREEN_SCHEMA),
                "--allowedTools", self._allowed_tools()]
        excluded = ", ".join(sorted(exclude_symbols)) or "none"
        user_message = (f"Find the top intraday candidates right now.\n"
                        f"Excluded symbols (already held — do not analyze or return): "
                        f"{excluded}")
        try:
            rc, out, err = self.runner(argv, user_message)
        except Exception as e:
            raise SkillScreenError(f"skill screen call failed: {e}") from e
        if rc != 0:
            raise SkillScreenError(f"claude CLI exit {rc}: {err.strip()[:300]}")
        if not out or not out.strip():
            raise SkillScreenError("claude CLI returned empty output")
        return _parse_screen(_result_text(out))
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_skill_screen_engine.py -q` → 12 pass. Full suite green.

- [ ] **Step 5: Commit**

```bash
git add skill_screen_engine.py tests/test_skill_screen_engine.py
git commit -m "feat: SkillScreenEngine — one-shot top-5 screening via full intraday-analyst skill"
```

---

### Task 3: factory + job wiring

**Files:**
- Modify: `engine_factory.py` (add `make_screen_engine`)
- Modify: `run_cycle_job.py` (`_build_orchestrator` passes `screen_engine=`)
- Test: `tests/test_skill_screen_engine.py` (append factory tests)

**Interfaces:**
- Consumes: `SkillScreenEngine` (Task 2); `SCREEN_MODE` env (Task 1 exports it).
- Produces: `make_screen_engine(use_web_search: bool = True, model: str = MODEL) -> SkillScreenEngine | None` (None = classic mode). `Orchestrator(..., screen_engine=...)` kwarg is wired in Task 4 — Task 3 only passes it through.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_skill_screen_engine.py`:

```python
from engine_factory import make_screen_engine
from decision_engine import DecisionEngineError


def test_factory_skill_mode(monkeypatch):
    monkeypatch.setenv("SCREEN_MODE", "skill")
    eng = make_screen_engine()
    assert isinstance(eng, SkillScreenEngine)


def test_factory_default_is_skill(monkeypatch):
    monkeypatch.delenv("SCREEN_MODE", raising=False)
    assert isinstance(make_screen_engine(), SkillScreenEngine)


def test_factory_classic_mode_returns_none(monkeypatch):
    monkeypatch.setenv("SCREEN_MODE", "classic")
    assert make_screen_engine() is None


def test_factory_unknown_mode_raises(monkeypatch):
    monkeypatch.setenv("SCREEN_MODE", "bogus")
    with pytest.raises(DecisionEngineError):
        make_screen_engine()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_skill_screen_engine.py -q`
Expected: 4 FAILs — `make_screen_engine` not defined.

- [ ] **Step 3: Implement** — append to `engine_factory.py`:

```python
def make_screen_engine(use_web_search: bool = True, model: str = MODEL):
    """Entry-screening backend from SCREEN_MODE: 'skill' (default — one-shot top-5 via the
    full intraday-analyst skill) returns a SkillScreenEngine; 'classic' returns None (movers
    screener + per-name decisions). See
    docs/superpowers/specs/2026-07-20-skill-screen-design.md."""
    mode = os.environ.get("SCREEN_MODE", "skill")
    if mode == "classic":
        return None
    if mode == "skill":
        from skill_screen_engine import SkillScreenEngine
        return SkillScreenEngine(use_web_search=use_web_search, model=model)
    raise DecisionEngineError(
        f"unknown SCREEN_MODE {mode!r}; use 'skill' or 'classic'")
```

And in `run_cycle_job.py`, replace the `_build_orchestrator` body's return with:

```python
def _build_orchestrator(store):
    from groww_client import GrowwClient
    from indicators import get_indicators
    from orchestrator import Orchestrator
    from screener import get_candidates
    from engine_factory import make_decision_engine, make_screen_engine
    cfg = store.get_config()
    return Orchestrator(store=store, client=GrowwClient(mode=cfg.mode),
                        engine=make_decision_engine(use_web_search=True),
                        get_indicators=get_indicators, get_candidates=get_candidates,
                        screen_engine=make_screen_engine(use_web_search=True))
```

(`Orchestrator` gains the `screen_engine` kwarg in Task 4; Tasks 3 and 4 must land in this order but the suite only fully passes after Task 4 — run `tests/test_skill_screen_engine.py` here, full suite at Task 4.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_skill_screen_engine.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add engine_factory.py run_cycle_job.py tests/test_skill_screen_engine.py
git commit -m "feat: make_screen_engine factory (SCREEN_MODE skill|classic) + job wiring"
```

---

### Task 4: orchestrator skill-screen path

**Files:**
- Modify: `orchestrator.py` (`__init__`, `_screen_and_enter`, new `_skill_screen_entries`)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `screen_engine.screen(exclude_symbols) -> list[tuple[str, Decision]]` (Task 2 shape); existing `_passes_entry_gate`, `_place_entry(run_id, symbol, decision, indicators, mode)`, `self.get_indicators(symbol)`.
- Produces: `Orchestrator(..., screen_engine=None)` — `None` keeps today's classic path byte-for-byte; non-None switches entries to the one-shot path. `_screen_and_enter` return contract unchanged: `(screened_count, entries)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_orchestrator.py`:

```python
class _FakeScreenEngine:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    def screen(self, exclude_symbols=()):
        self.calls.append(sorted(exclude_symbols))
        if self.error:
            raise self.error
        return self.results


def _screen_orch(store, screen_engine, indic_map=None, client=None):
    # classic get_candidates must never be called in skill mode — booby-trap it
    def trap(**kw):
        raise AssertionError("classic screener called in skill mode")
    return Orchestrator(store, client or _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                        get_indicators=lambda s: (indic_map or {}).get(s, _indic(s)),
                        get_candidates=trap, screen_engine=screen_engine)


def test_skill_screen_places_entries_in_quality_order():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=1,
         capital_per_position=20000.0)
    eng = _FakeScreenEngine(results=[
        ("LOW", _decision(action="BUY_NOW", tq=60, conf=60, rr=2.0, entry=50.0, stop=48.0)),
        ("HIGH", _decision(action="BUY_NOW", tq=90, conf=80, rr=2.5, entry=100.0, stop=96.0)),
    ])
    orch = _screen_orch(store, eng, indic_map={"HIGH": _indic("HIGH", last=100),
                                               "LOW": _indic("LOW", last=50)})
    summary = orch.run_cycle()
    assert summary["entries"] == 1 and summary["candidates"] == 2
    open_syms = [p.symbol for p in store.get_open_positions()]
    assert open_syms == ["HIGH"]                     # best quality wins the only slot


def test_skill_screen_gate_rejects_and_records():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    eng = _FakeScreenEngine(results=[
        ("WEAK", _decision(action="BUY_NOW", tq=40, conf=40, rr=1.0)),
    ])
    orch = _screen_orch(store, eng)
    summary = orch.run_cycle()
    assert summary["entries"] == 0 and summary["candidates"] == 1
    recs = store.get_decisions_for_run(summary["run_id"])
    assert any(r.symbol == "WEAK" and r.reason == "below gate" for r in recs)


def test_skill_screen_failure_degrades_to_zero_candidates():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                        entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    eng = _FakeScreenEngine(error=RuntimeError("claude CLI exit 1"))
    orch = _screen_orch(store, eng, indic_map={"A": _indic("A", last=111)})
    summary = orch.run_cycle()
    assert summary["status"] == "SUCCESS"
    assert summary["candidates"] == 0 and summary["entries"] == 0
    assert summary["exits"] == 1                     # exits still managed


def test_skill_screen_refilters_held_symbols():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    store.open_position(symbol="HELD", exchange="NSE", side="LONG", quantity=10,
                        entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    eng = _FakeScreenEngine(results=[
        ("HELD", _decision(action="BUY_NOW", tq=90, conf=80, rr=2.5)),
        ("FRESH", _decision(action="BUY_NOW", tq=70, conf=65, rr=2.0)),
    ])
    orch = _screen_orch(store, eng,
                        indic_map={"HELD": _indic("HELD", last=100),
                                   "FRESH": _indic("FRESH", last=100)})
    summary = orch.run_cycle()
    # excluded symbol passed to the engine AND re-filtered even though the model returned it
    assert eng.calls == [["HELD"]]
    open_syms = sorted(p.symbol for p in store.get_open_positions())
    assert open_syms == ["FRESH", "HELD"]            # HELD is the pre-existing position only
    assert summary["entries"] == 1


def test_skill_screen_skipped_when_book_full():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=1,
         capital_per_position=20000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                        entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    eng = _FakeScreenEngine(results=[("B", _decision(action="BUY_NOW", tq=90, conf=80))])
    orch = _screen_orch(store, eng, indic_map={"A": _indic("A", last=105)})
    orch.run_cycle()
    assert eng.calls == []                           # book full -> no expensive skill call


def test_classic_path_untouched_when_no_screen_engine():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client, engine = _FakeClient(), _FakeEngine(
        _decision(action="BUY_NOW", tq=90, conf=80, rr=2.5))
    orch = Orchestrator(store, client, engine,
                        get_indicators=lambda s: _indic(s, last=100),
                        get_candidates=lambda **kw: [{"symbol": "CLS"}])
    summary = orch.run_cycle()
    assert summary["entries"] == 1
    assert engine.calls and engine.calls[0][0] == "CLS"   # per-name decide still used
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -q`
Expected: new tests FAIL — `Orchestrator.__init__` has no `screen_engine` kwarg. (`store.get_decisions_for_run(run_id)` and `record_decision(..., score=, raw_json=)` are verified real signatures in `store.py:391-408`.)

- [ ] **Step 3: Implement** — in `orchestrator.py`:

`__init__` gains a trailing kwarg:

```python
    def __init__(self, store, client, engine, get_indicators: Callable[[str], dict],
                 get_candidates: Callable[..., list], now_provider: Callable[[], datetime] = _utc_now,
                 screen_engine=None):
        ...
        self.screen_engine = screen_engine
```

In `_screen_and_enter`, immediately after the `held = (...)` assignment, branch:

```python
        if self.screen_engine is not None:
            return self._skill_screen_entries(run_id, cfg, free_slots, held)
```

New method after `_screen_and_enter`:

```python
    def _skill_screen_entries(self, run_id: int, cfg, free_slots: int,
                              held: set[str]) -> tuple[int, int]:
        """One-shot skill screen: a single agentic claude call ranks the whole market and
        returns <=5 ready-made Decisions; gate + placement below are the SAME code the classic
        path uses. A screen failure degrades to 0 candidates — never fails the cycle."""
        try:
            results = self.screen_engine.screen(exclude_symbols=sorted(held))
        except Exception as e:
            log.warning("skill screen failed (%s) — no candidates this cycle", e)
            return 0, 0
        results = sorted(results, key=lambda sc: (sc[1].trade_quality is None,
                                                  -(sc[1].trade_quality or 0)))
        entries = 0
        for symbol, decision in results:
            if entries >= free_slots:
                break
            if symbol in held:      # belt and braces — the model was told to exclude these
                log.warning("skill screen returned held symbol %s — ignoring", symbol)
                continue
            if not _passes_entry_gate(decision):
                self.store.record_decision(run_id=run_id, symbol=symbol,
                                           action=decision.action,
                                           score=decision.trade_quality, reason="below gate",
                                           raw_json=decision.raw_response)
                continue
            try:
                indicators = self.get_indicators(symbol)
                placed = self._place_entry(run_id, symbol, decision, indicators, cfg.mode)
            except Exception as e:
                log.exception("entry placement failed for %s", symbol)
                self.store.record_decision(run_id=run_id, symbol=symbol, action="SKIP",
                                           reason=f"entry error: {e}")
                continue
            if placed:
                entries += 1
                held.add(symbol)
        return len(results), entries
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -q` → all pass. Then the FULL suite `.venv/bin/python -m pytest -q` → green (this also proves Task 3's `run_cycle_job` wiring imports cleanly).

- [ ] **Step 5: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "feat: orchestrator skill-screen entry path (one-shot top-5, gate + placement reused)"
```

---

### Task 5: smoke script + docs

**Files:**
- Create: `scripts/smoke_test_skill_screen.py`
- Modify: `README.md` (pipeline description)

**Interfaces:**
- Consumes: `load_settings().apply_to_environ()`, `make_screen_engine` (Task 3).

- [ ] **Step 1: Create `scripts/smoke_test_skill_screen.py`** (mirror the style of `scripts/smoke_test_claude_cli.py`):

```python
#!/usr/bin/env python3
"""Manual, not-CI smoke test: run ONE real skill-screen call (claude -p on the subscription,
agentic: movers screener + indicator tool via Bash) and print the top-5. Costs one long Opus
session; run during market hours for meaningful output.

Usage: .venv/bin/python scripts/smoke_test_skill_screen.py [EXCLUDE_SYMBOL ...]
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from settings import load_settings
load_settings().apply_to_environ()

from engine_factory import make_screen_engine


def main() -> None:
    engine = make_screen_engine(use_web_search=True)
    if engine is None:
        print("SCREEN_MODE=classic — nothing to smoke test; set SCREEN_MODE=skill")
        sys.exit(1)
    exclude = [s.upper() for s in sys.argv[1:]]
    print(f"running one skill screen (exclude={exclude or 'none'}) — takes several minutes…")
    results = engine.screen(exclude_symbols=exclude)
    if not results:
        print("skill screen: OK — empty top-5 (no edge right now)")
        return
    print(f"skill screen: OK — {len(results)} candidate(s):")
    for symbol, d in results:
        print(f"  {symbol:12s} {d.action:16s} q={d.trade_quality} conf={d.confidence} "
              f"entry={d.entry} stop={d.stop_loss} t1={d.target1} rr={d.risk_reward}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax-check it** (no credentials needed for a parse check):

Run: `.venv/bin/python -m py_compile scripts/smoke_test_skill_screen.py` → exit 0.

- [ ] **Step 3: Update `README.md`** — in the pipeline description (the paragraph describing screening/decisions, near "run_cycle_job.py is the entry point"), add one sentence:

```markdown
Entry screening runs in `screen_mode: skill` by default — one agentic `claude -p` call per
cycle runs the full intraday-analyst skill (screener + indicator tool via restricted Bash)
and returns the top-5 candidates; `screen_mode: classic` in config.yaml restores the
movers-screener + per-name-decision pipeline. Smoke test: `.venv/bin/python
scripts/smoke_test_skill_screen.py`.
```

- [ ] **Step 4: Full suite + commit**

Run: `.venv/bin/python -m pytest -q` → green.

```bash
git add scripts/smoke_test_skill_screen.py README.md
git commit -m "feat: skill-screen smoke script + README"
```

---

### Post-implementation verification (manual, user-visible)

1. `.venv/bin/python -m pytest -q` — full suite green.
2. During market hours: `export $(cat .env | xargs) && .venv/bin/python scripts/smoke_test_skill_screen.py` — eyeball the top-5 (symbols plausible, scores in the skill's bands, entries/stops sane).
3. Watch the next scheduled cycle's log line `skill screen` in `~/.autointraday/cycle.err.log` and the dashboard decisions table.
4. Rollback if needed: set `screen_mode: classic` in `config.yaml` (no reload needed — the job reads config each run).
```
