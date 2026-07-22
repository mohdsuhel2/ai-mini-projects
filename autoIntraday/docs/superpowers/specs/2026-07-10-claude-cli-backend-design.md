# autoIntraday add-on: Claude-CLI decision backend (run on the Claude subscription)

## Context

An add-on to Phase 3 (see `2026-07-09-decision-engine-design.md`). The built decision engine
(`decision_engine.py`) calls the Anthropic **API** directly (`anthropic` SDK), which is billed
per token to an API account and is NOT covered by a Claude Pro/Max subscription. This add-on
provides a second backend that runs the same decision through **headless Claude Code**
(`claude -p`), which DOES run on the user's Claude Code subscription — so the hourly loop can
use the subscription instead of per-token API billing. The backend is selectable at deploy
time; the API backend stays alongside it.

### Why this is possible / the tradeoff

Headless `claude -p` is Anthropic's official CLI, supported for scripted/cron use, and runs
under the Pro/Max subscription (verified with the Claude Code guide). The tradeoff is
**usage limits, not cost**: a subscription is a usage allowance, not metered billing. An
hourly multi-stock Opus loop can exhaust a Max allowance; when the limit is hit the run
fails/rate-limits until reset (it does NOT silently fall back to paid API). So the CLI backend
suits lower volume (fewer stocks and/or Sonnet); heavy use is what the API backend is for.

### Decisions

- **Same interface, new class.** A sibling engine `ClaudeCliEngine.decide(symbol, indicators,
  position=None) -> Decision` — identical to `DecisionEngine.decide` — so the orchestrator is
  UNCHANGED (it just receives whichever engine it's given).
- **Reuse the shared engine pieces.** `ENGINE_PROMPT`, `build_user_message`, `DECISION_SCHEMA`,
  `_parse_decision`, `DecisionEngineError` are imported from `decision_engine.py` — the prompt
  and the parser are shared; only the "get raw model text" step differs.
- **Subprocess seam.** `ClaudeCliEngine` invokes `claude -p` through an injectable
  `runner(argv, input_text) -> (returncode, stdout, stderr)` (same pattern as `indicators.py`
  / `screener.py`), so unit tests mock it — no real CLI, no subscription spend, no network in
  tests.
- **Selection via env.** `DECISION_BACKEND` (`api` default | `claude_cli`), read by a
  `make_decision_engine(...)` factory used in `run_cycle_job._build_orchestrator`. Backend is a
  deployment concern, not a trading parameter, so it's an env var (matching the existing
  `INTRADAY_*` / `SCREENER_*` convention) rather than a store-config field.
- **Do NOT set `ANTHROPIC_API_KEY` when using `claude_cli`.** Its presence forces `claude` to
  bill the API instead of the subscription. The docs and the scheduler notes warn about this
  loudly.

## The `claude -p` invocation (flags confirmed from `claude --help`)

```
claude -p
  --output-format json
  --model <model>                       # e.g. claude-opus-4-8 (subscription may constrain it)
  --append-system-prompt <ENGINE_PROMPT>
  --json-schema <DECISION_SCHEMA-json>  # structured output
  [--allowedTools WebSearch]            # only when use_web_search
  <user message>                        # build_user_message(symbol, indicators, position)
```

- `--output-format json` returns a JSON **envelope** on stdout (Claude Code result object). The
  model's answer is inside it. The exact field name (`result` vs `text` vs `content`) is the one
  remaining unknown — the parser reads it defensively: parse stdout as JSON, take `result` if
  present, else the largest string field, else the whole stdout; then run the existing
  `_extract_json` / `_parse_decision`, which already tolerate a JSON object embedded in text.
  So the CLI backend leans on the free-text-JSON path already built and tested.
- `--json-schema` (which the CLI exposes) is passed as an extra guarantee, but correctness does
  NOT depend on it — the defensive parse covers both a schema-clean and a prose-wrapped result.
- The user message goes as the final positional prompt arg (or stdin with the default
  `--input-format text`).

## Architecture

### `claude_cli_engine.py` — `ClaudeCliEngine`
`ClaudeCliEngine(runner=_default_runner, use_web_search=True, model="claude-opus-4-8",
claude_bin=None)`.
- `_default_runner(argv, input_text)` uses `subprocess.run(argv, input=input_text,
  capture_output=True, text=True, timeout=...)`; `claude_bin` (env `CLAUDE_BIN`, default
  `"claude"`) is argv[0].
- `decide(symbol, indicators, position=None) -> Decision`: builds argv (flags above) with
  `ENGINE_PROMPT` as `--append-system-prompt` and `json.dumps(DECISION_SCHEMA)` as
  `--json-schema`, adds `--allowedTools WebSearch` when `use_web_search`; passes
  `build_user_message(...)` as the prompt; runs the runner; on non-zero exit / empty output
  raises `DecisionEngineError`; extracts the result text (defensive envelope parse) and returns
  `_parse_decision(text)`.
- No retry (a decision failure raises; the orchestrator skips that name — same contract as the
  API engine).

### `make_decision_engine(...)` — the selector
A module-level factory (in `decision_engine.py` or a small `engine_factory.py`) reading
`DECISION_BACKEND`:
- `"api"` (default) → `DecisionEngine(use_web_search=..., model=...)`.
- `"claude_cli"` → `ClaudeCliEngine(use_web_search=..., model=...)`.
- unknown value → `DecisionEngineError`.
`run_cycle_job._build_orchestrator` calls this factory instead of constructing `DecisionEngine`
directly, so the whole system picks the backend from one env var.

## Error handling

- Every failure (non-zero `claude` exit, empty stdout, unparseable envelope/decision) raises
  `DecisionEngineError`, the same single type callers already handle.
- A `claude` exit that indicates the subscription usage limit was hit surfaces as a
  `DecisionEngineError` like any other CLI failure — the orchestrator records the skip and the
  cycle continues; the run isn't corrupted.

## Testing

- Unit tests mock the `runner`: argv contains `-p`, `--output-format json`,
  `--append-system-prompt` with the engine prompt, the model, `--json-schema`, and
  `--allowedTools WebSearch` only when `use_web_search`; the indicator JSON is in the prompt; a
  fake JSON-envelope stdout parses into a `Decision`; non-zero exit / empty / garbage stdout
  raise `DecisionEngineError`; the defensive envelope parse handles both a `{"result": "...json..."}`
  envelope and a bare-JSON stdout.
- Unit tests for `make_decision_engine`: `DECISION_BACKEND` unset/`api` → `DecisionEngine`;
  `claude_cli` → `ClaudeCliEngine`; bad value → `DecisionEngineError`. (Constructing
  `DecisionEngine` must not require credentials — it uses the `client_factory` default lazily;
  if the factory would build a real client at construction, the selector test injects a stub or
  asserts type without invoking a call.)
- A manual, not-CI smoke script: verify `claude` is installed, run ONE real headless decision on
  a symbol via the CLI backend (uses the user's Claude login / subscription), and confirm it
  returns a parsed `Decision` — this also confirms the real `--output-format json` envelope
  field. Left for the user's machine (needs their `claude` login), like the other credentialed
  smokes.

## Out of scope

Changing the orchestrator (unchanged), the API backend (untouched — its tests stay green),
Managed Agents, and any change to how paper/live mode or capital rules work.
