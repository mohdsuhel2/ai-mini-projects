# Claude-CLI Decision Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second decision backend that runs the LLM call through headless Claude Code (`claude -p`, on the user's subscription) instead of the raw Anthropic API — same `decide()` interface, selectable via `DECISION_BACKEND`, API backend kept alongside and untouched.

**Architecture:** `ClaudeCliEngine` (new) mirrors `DecisionEngine.decide` but shells out to `claude -p` via an injectable runner seam, reusing the shared `ENGINE_PROMPT` / `build_user_message` / `DECISION_SCHEMA` / `_parse_decision`. `make_decision_engine()` picks the backend from `DECISION_BACKEND`; the scheduler builds the engine through it.

**Tech Stack:** Python 3.10+, standard-library `subprocess`/`json`, `pytest`. Reuses `decision_engine.py` / `engine_prompt.py`. Depends on the `claude` CLI at runtime (not in unit tests — the runner is mocked).

## Global Constraints

- The runner (`runner(argv, input_text) -> (rc, out, err)`) is injectable; unit tests mock it — no real `claude` CLI, no subscription spend, no network in tests.
- Every error the module raises is a `DecisionEngineError` (reused from `decision_engine.py`).
- Shared engine pieces are imported, not duplicated: `ENGINE_PROMPT` (engine_prompt.py), `build_user_message`, `DECISION_SCHEMA`, `_parse_decision`, `DecisionEngineError`, `MODEL` (decision_engine.py).
- `ClaudeCliEngine.decide` has the SAME signature as `DecisionEngine.decide` so the orchestrator is unchanged.
- The `claude -p` invocation flags are confirmed present via `claude --help`: `-p`, `--output-format json`, `--model`, `--append-system-prompt`, `--json-schema`, `--allowedTools`.
- No retry on the CLI call (a failure raises; the orchestrator skips that name).

---

### Task 1: `claude_cli_engine.py` — `ClaudeCliEngine`

**Files:**
- Create: `claude_cli_engine.py`
- Test: `tests/test_claude_cli_engine.py`

**Interfaces:**
- Consumes: `ENGINE_PROMPT`, `build_user_message`, `DECISION_SCHEMA`, `_parse_decision`, `DecisionEngineError`, `MODEL`.
- Produces: `_default_runner(argv, input_text) -> (int, str, str)`; `_result_text(stdout: str) -> str` (defensive envelope parse); `ClaudeCliEngine(runner=_default_runner, use_web_search=True, model=MODEL, claude_bin=None)` with `decide(symbol, indicators, position=None) -> Decision`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_claude_cli_engine.py`:

```python
import json

import pytest

from claude_cli_engine import ClaudeCliEngine, _result_text
from decision_engine import DecisionEngineError

_DECISION = {
    "action": "BUY_NOW", "confidence": 78, "trade_quality": 82, "entry": 2456.7,
    "stop_loss": 2440.0, "target1": 2480.0, "target2": None, "target3": None,
    "risk_reward": 2.1, "expected_move_pct": 1.3, "invalidation": "15m close below VWAP",
    "rationale": "fresh breakout", "news_catalyst": None}
_DECISION_JSON = json.dumps(_DECISION)


def _runner_factory(rc, out, err=""):
    def runner(argv, input_text):
        runner.argv = argv
        runner.input_text = input_text
        return (rc, out, err)
    return runner


def test_result_text_unwraps_envelope():
    env = json.dumps({"type": "result", "result": _DECISION_JSON, "session_id": "x"})
    assert _result_text(env) == _DECISION_JSON


def test_result_text_bare_json_passthrough():
    # stdout that is already the decision object (no envelope) is returned as-is
    assert _result_text(_DECISION_JSON) == _DECISION_JSON


def test_result_text_non_json_passthrough():
    assert _result_text("plain text answer") == "plain text answer"


def test_decide_builds_argv_and_parses():
    runner = _runner_factory(0, json.dumps({"result": _DECISION_JSON}))
    eng = ClaudeCliEngine(runner=runner, use_web_search=True, model="claude-opus-4-8")
    d = eng.decide("RELIANCE", {"symbol": "RELIANCE", "price": {"last": 2456.7}})
    assert d.action == "BUY_NOW" and d.entry == 2456.7
    argv = runner.argv
    assert "-p" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "claude-opus-4-8"
    assert "--append-system-prompt" in argv and "--json-schema" in argv
    assert "--allowedTools" in argv and "WebSearch" in argv
    # the indicator JSON + symbol go to the CLI via stdin (the user message)
    assert "RELIANCE" in runner.input_text and "2456.7" in runner.input_text


def test_decide_without_web_search_omits_tool():
    runner = _runner_factory(0, json.dumps({"result": _DECISION_JSON}))
    ClaudeCliEngine(runner=runner, use_web_search=False).decide("TCS", {"symbol": "TCS"})
    assert "--allowedTools" not in runner.argv


def test_decide_nonzero_exit_raises():
    runner = _runner_factory(1, "", "usage limit reached")
    with pytest.raises(DecisionEngineError, match="claude"):
        ClaudeCliEngine(runner=runner).decide("X", {"symbol": "X"})


def test_decide_empty_output_raises():
    runner = _runner_factory(0, "   ")
    with pytest.raises(DecisionEngineError, match="empty"):
        ClaudeCliEngine(runner=runner).decide("X", {"symbol": "X"})


def test_decide_garbage_output_raises():
    runner = _runner_factory(0, json.dumps({"result": "I could not decide."}))
    with pytest.raises(DecisionEngineError):
        ClaudeCliEngine(runner=runner).decide("X", {"symbol": "X"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_claude_cli_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_cli_engine'`

- [ ] **Step 3: Implement `claude_cli_engine.py`**

```python
"""Claude-CLI decision backend — runs the intraday decision through headless `claude -p`
(on the user's Claude subscription) instead of the raw Anthropic API. Same decide() interface
as DecisionEngine; reuses the shared engine prompt + parser. See
docs/superpowers/specs/2026-07-10-claude-cli-backend-design.md.

Note: when using this backend, do NOT set ANTHROPIC_API_KEY in the environment — its presence
makes `claude` bill the API instead of the subscription."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable

from decision_engine import (DECISION_SCHEMA, MODEL, DecisionEngineError, _parse_decision,
                             build_user_message)
from engine_prompt import ENGINE_PROMPT


def _default_runner(argv: list[str], input_text: str) -> tuple[int, str, str]:
    proc = subprocess.run(argv, input=input_text, capture_output=True, text=True, timeout=180)
    return proc.returncode, proc.stdout, proc.stderr


def _result_text(stdout: str) -> str:
    """Extract the model's answer from `claude -p --output-format json` stdout. Defensive:
    unwrap the JSON envelope's result field if present, else return stdout unchanged (the
    downstream _parse_decision tolerates a JSON object embedded in text either way)."""
    s = stdout.strip()
    try:
        env = json.loads(s)
    except json.JSONDecodeError:
        return s
    if isinstance(env, dict):
        for key in ("result", "text", "content"):
            v = env.get(key)
            if isinstance(v, str) and v.strip():
                return v
    return s


class ClaudeCliEngine:
    def __init__(self, runner: Callable[[list[str], str], tuple[int, str, str]] = _default_runner,
                 use_web_search: bool = True, model: str = MODEL, claude_bin: str | None = None):
        self.runner = runner
        self.use_web_search = use_web_search
        self.model = model
        self.claude_bin = claude_bin or os.environ.get("CLAUDE_BIN", "claude")

    def decide(self, symbol: str, indicators: dict, position: dict | None = None):
        argv = [self.claude_bin, "-p", "--output-format", "json", "--model", self.model,
                "--append-system-prompt", ENGINE_PROMPT,
                "--json-schema", json.dumps(DECISION_SCHEMA)]
        if self.use_web_search:
            argv += ["--allowedTools", "WebSearch"]
        user_message = build_user_message(symbol, indicators, position)
        try:
            rc, out, err = self.runner(argv, user_message)
        except Exception as e:
            raise DecisionEngineError(f"claude CLI call failed for {symbol}: {e}") from e
        if rc != 0:
            raise DecisionEngineError(f"claude CLI exit {rc} for {symbol}: {err.strip()}")
        if not out or not out.strip():
            raise DecisionEngineError(f"claude CLI returned empty output for {symbol}")
        return _parse_decision(_result_text(out))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_claude_cli_engine.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add claude_cli_engine.py tests/test_claude_cli_engine.py
git commit -m "Add ClaudeCliEngine: headless claude -p decision backend"
```

---

### Task 2: `engine_factory.py` selector + wire into the scheduler

**Files:**
- Create: `engine_factory.py`
- Modify: `run_cycle_job.py`
- Test: `tests/test_engine_factory.py`

**Interfaces:**
- Consumes: `DecisionEngine`, `DecisionEngineError`, `MODEL` (decision_engine), `ClaudeCliEngine` (claude_cli_engine).
- Produces: `make_decision_engine(use_web_search=True, model=MODEL) -> DecisionEngine | ClaudeCliEngine` reading `DECISION_BACKEND` (`api` default | `claude_cli`; unknown → `DecisionEngineError`). `run_cycle_job._build_orchestrator` uses it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_engine_factory.py`:

```python
import pytest

from engine_factory import make_decision_engine
from decision_engine import DecisionEngine, DecisionEngineError
from claude_cli_engine import ClaudeCliEngine


def test_default_backend_is_api(monkeypatch):
    monkeypatch.delenv("DECISION_BACKEND", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")   # so anthropic.Anthropic() constructs
    eng = make_decision_engine()
    assert isinstance(eng, DecisionEngine)


def test_api_backend_explicit(monkeypatch):
    monkeypatch.setenv("DECISION_BACKEND", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert isinstance(make_decision_engine(), DecisionEngine)


def test_claude_cli_backend(monkeypatch):
    monkeypatch.setenv("DECISION_BACKEND", "claude_cli")
    eng = make_decision_engine(use_web_search=False)
    assert isinstance(eng, ClaudeCliEngine)
    assert eng.use_web_search is False


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("DECISION_BACKEND", "bogus")
    with pytest.raises(DecisionEngineError, match="DECISION_BACKEND"):
        make_decision_engine()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine_factory'`

- [ ] **Step 3: Implement `engine_factory.py`**

```python
"""Selects the decision backend from the DECISION_BACKEND env var: 'api' (default, raw
Anthropic API) or 'claude_cli' (headless `claude -p`, on the Claude subscription). See
docs/superpowers/specs/2026-07-10-claude-cli-backend-design.md."""
from __future__ import annotations

import os

from decision_engine import MODEL, DecisionEngine, DecisionEngineError


def make_decision_engine(use_web_search: bool = True, model: str = MODEL):
    backend = os.environ.get("DECISION_BACKEND", "api")
    if backend == "api":
        return DecisionEngine(use_web_search=use_web_search, model=model)
    if backend == "claude_cli":
        from claude_cli_engine import ClaudeCliEngine
        return ClaudeCliEngine(use_web_search=use_web_search, model=model)
    raise DecisionEngineError(
        f"unknown DECISION_BACKEND {backend!r}; use 'api' or 'claude_cli'")
```

- [ ] **Step 4: Wire it into `run_cycle_job._build_orchestrator`**

In `run_cycle_job.py`, inside `_build_orchestrator`, replace the direct `DecisionEngine(...)`
construction with the factory. Change the import line and the engine construction:

```python
def _build_orchestrator(store):
    from groww_client import GrowwClient
    from indicators import get_indicators
    from orchestrator import Orchestrator
    from screener import get_candidates
    from engine_factory import make_decision_engine
    cfg = store.get_config()
    return Orchestrator(store=store, client=GrowwClient(mode=cfg.mode),
                        engine=make_decision_engine(use_web_search=True),
                        get_indicators=get_indicators, get_candidates=get_candidates)
```

(Remove the now-unused `from decision_engine import DecisionEngine` import inside the function.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_engine_factory.py tests/test_run_cycle_job.py -v`
Expected: PASS — the 4 new factory tests + the existing run_cycle_job tests (which inject their own factories and don't exercise `_build_orchestrator`).

- [ ] **Step 6: Commit**

```bash
git add engine_factory.py run_cycle_job.py tests/test_engine_factory.py
git commit -m "Add engine_factory selector and wire DECISION_BACKEND into the scheduler"
```

---

### Task 3: Verification smoke script + docs

**Files:**
- Create: `scripts/smoke_test_claude_cli.py`
- Modify: `README.md`
- Modify: `deploy/install_launchd.md`

**Interfaces:**
- Consumes: `ClaudeCliEngine`, `get_indicators`. Produces: no new API — verification + docs.

- [ ] **Step 1: Confirm the full suite passes**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — all phases + the two new test files.

- [ ] **Step 2: Write the smoke script**

Create `scripts/smoke_test_claude_cli.py`:

```python
#!/usr/bin/env python3
"""Manual, not-CI smoke test: run ONE real intraday decision through the headless `claude -p`
backend (on your Claude subscription) for one symbol. Verifies the CLI backend end to end,
including the real `--output-format json` envelope.

Usage: .venv/bin/python scripts/smoke_test_claude_cli.py RELIANCE
Requires: the `claude` CLI installed and logged in to your Claude subscription, and the sibling
StockAnalayze venv (for indicators). Do NOT set ANTHROPIC_API_KEY (it would force API billing).
"""
from __future__ import annotations

import shutil
import sys

sys.path.insert(0, ".")

from claude_cli_engine import ClaudeCliEngine
from decision_engine import DecisionEngineError
from indicators import get_indicators


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    if shutil.which("claude") is None:
        print("FAILED: `claude` CLI not found on PATH — install/login to Claude Code first.")
        sys.exit(1)
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("WARNING: ANTHROPIC_API_KEY is set — `claude` will bill the API, not your "
              "subscription. Unset it to use the subscription.")
    try:
        print(f"fetching indicators for {symbol} ...")
        indicators = get_indicators(symbol)
        print("indicators: OK")
        engine = ClaudeCliEngine(use_web_search=True)
        decision = engine.decide(symbol, indicators)
        print(f"decide (claude_cli): OK -> {decision.action} "
              f"(confidence {decision.confidence}, quality {decision.trade_quality})")
        print(f"  entry={decision.entry} stop={decision.stop_loss} "
              f"t1={decision.target1} R:R={decision.risk_reward}")
        print(f"  rationale: {decision.rationale}")
    except DecisionEngineError as e:
        print(f"FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Confirm the smoke script parses**

Run: `.venv/bin/python -c "import ast; ast.parse(open('scripts/smoke_test_claude_cli.py').read()); print('smoke parses OK')"`

- [ ] **Step 4: Add a "Choosing the decision backend" section to `README.md`**

Append to `README.md`:

```markdown
## Decision backend: API vs Claude subscription

The decision engine can run either way, selected by the `DECISION_BACKEND` env var:

- `DECISION_BACKEND=api` (default) — calls the Anthropic **API** (`decision_engine.py`). Billed
  per token to an Anthropic API account; needs `ANTHROPIC_API_KEY`. No usage ceiling.
- `DECISION_BACKEND=claude_cli` — runs the decision through headless **`claude -p`**
  (`claude_cli_engine.py`), on your **Claude Pro/Max subscription**. Needs the `claude` CLI
  installed and logged in. **Do NOT set `ANTHROPIC_API_KEY`** in the job env — its presence
  makes `claude` bill the API instead of the subscription. Subject to your subscription's usage
  limits (an hourly multi-stock Opus loop can exhaust them — consider fewer stocks and/or a
  cheaper model); when the limit is hit the cycle fails and retries next hour rather than
  silently billing the API.

The orchestrator is identical either way — only where the reasoning runs differs.

### Verify the Claude-CLI backend (one real decision on your subscription)

\`\`\`bash
DECISION_BACKEND=claude_cli .venv/bin/python scripts/smoke_test_claude_cli.py RELIANCE
\`\`\`
```

- [ ] **Step 5: Note the backend choice in the launchd install doc**

In `deploy/install_launchd.md`, in the credentials/env section, add a line:

```markdown
- **Decision backend:** set `DECISION_BACKEND=claude_cli` in the plist `EnvironmentVariables`
  to run decisions on your Claude subscription via `claude -p` (and then do NOT set
  `ANTHROPIC_API_KEY`, which would force API billing). Leave it unset (or `api`) with
  `ANTHROPIC_API_KEY` set to use the pay-per-token API. The `claude` binary must be on the
  job's PATH, or set `CLAUDE_BIN` to its absolute path.
```

- [ ] **Step 6: Commit**

```bash
git add scripts/smoke_test_claude_cli.py README.md deploy/install_launchd.md
git commit -m "Add Claude-CLI backend smoke test and backend-selection docs"
```

---

## Self-Review Notes

- **Spec coverage:** `ClaudeCliEngine` with runner seam + defensive envelope parse + same decide() interface (Task 1) · `DECISION_BACKEND` selector + scheduler wiring (Task 2) · verification smoke (claude-present check + real headless decision) + README/launchd docs incl. the ANTHROPIC_API_KEY caveat (Task 3). All spec sections map to a task.
- **Type consistency:** `ClaudeCliEngine.decide(symbol, indicators, position=None)` matches `DecisionEngine.decide` exactly, so the `Orchestrator`'s `engine.decide(...)` calls work unchanged; the factory returns either type behind the same interface; reused imports (`ENGINE_PROMPT`, `build_user_message`, `DECISION_SCHEMA`, `_parse_decision`, `DecisionEngineError`, `MODEL`) match their real definitions.
- **No placeholders:** every step has complete runnable code. The one real unknown (the exact `--output-format json` envelope field) is handled by the defensive `_result_text` (tries `result`/`text`/`content`, else passthrough) AND confirmed by the Task 3 smoke run — not a silent assumption. The `claude -p` flags themselves are confirmed present via `claude --help`. Expected test counts: 8 claude_cli_engine, 4 engine_factory.
- **Safety:** the CLI backend is unit-tested with a mocked runner (no subscription spend); the ANTHROPIC_API_KEY-forces-API-billing gotcha is documented in the module docstring, the smoke script (warns at runtime), the README, and the launchd doc.
