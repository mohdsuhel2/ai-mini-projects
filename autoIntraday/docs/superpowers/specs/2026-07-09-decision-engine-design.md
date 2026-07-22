# autoIntraday Phase 3: Decision Engine (LLM-based) — Design

## Context

Phase 3 of `autoIntraday` (see `2026-07-09-groww-client-design.md` for the full 6-phase
system overview). This is the "brain" that decides what to trade. Phase 1 gave us the broker
client; Phase 2 the state store; Phase 3 produces the BUY/SELL/HOLD/WAIT decisions that
Phase 4's orchestrator will act on.

### Decision change from the original plan

The original system spec chose a **deterministic Python scoring engine** (no LLM at run
time). The user has since revised this: Phase 3 must instead **mirror the `intraday-analyst`
skill** — a Python tool computes the technical indicators, and **Claude reasons over that
data** (the skill's 20-step institutional decision engine) to produce the call. This means
the unattended hourly cron makes a **Claude API call per stock each cycle**.

Tradeoffs accepted with this change: higher run-time cost and latency, and
non-determinism (the same inputs can yield slightly different calls). Mitigations: the
frozen engine prompt is prompt-cached (~10× cheaper after the first call in a window), and
every decision persists its full raw model response for audit.

### Decisions (from brainstorming)

- **Model: `claude-opus-4-8` on every call**, adaptive thinking on (top reasoning quality;
  cost accepted).
- **Web search enabled** on the decision call (`web_search_20260209`) so Claude checks
  same-day catalysts before deciding, matching the skill (news overrides the chart).
- **Reuse the existing indicator tool via its own venv.** `stock_analyze_intraday.py` in the
  sibling `StockAnalayze` project (which imports `stock_analyze` + `stock_analyze_av` and has
  its own installed venv with `yfinance` etc.) is invoked as a subprocess to produce the
  indicator JSON — exactly how the `intraday-analyst` skill runs it. `autoIntraday` does NOT
  vendor/copy that multi-file chain or duplicate its heavy dependencies; it references the
  script + venv by a **configurable path** (env-overridable, defaulting to the known
  `StockAnalayze` location). Accepted tradeoff: this couples the local install to the sibling
  project's on-disk location — acceptable for a personal local cron, and it's the same
  coupling the skill already relies on. The engine does not recompute indicators; rewriting
  proven indicator math would be wasteful and risky.
- **Structured output** constrains the model reply to a typed `Decision` so the orchestrator
  gets parseable fields, not prose.

## Architecture

Two modules, each with one clear responsibility, connected by a plain dict (the indicator
JSON):

### `indicators.py` — indicator provider
`get_indicators(symbol: str) -> dict`. Runs the sibling `StockAnalayze` project's
`stock_analyze_intraday.py` the same way the skill does — `<StockAnalayze venv python>
stock_analyze_intraday.py -s <SYMBOL> --source yahoo` — captures stdout, parses the JSON,
returns it. A thin subprocess adapter (not an import) to stay robust to the script's side
effects and match the skill's proven usage. The Python-interpreter path and script path are
read from env vars (`INTRADAY_PYTHON`, `INTRADAY_SCRIPT`) with defaults pointing at the known
`StockAnalayze` location, and the working directory is set to the script's directory so its
sibling imports resolve. Raises `IndicatorError` (a subclass of `DecisionEngineError`) on
non-zero exit, empty output, or unparseable JSON. This subprocess boundary is the one seam
later phases and unit tests mock.

### `decision_engine.py` — the reasoning engine
`DecisionEngine(client_factory=_default_client_factory, use_web_search=True,
model="claude-opus-4-8")`. Wraps the Anthropic SDK. One public method:

`decide(symbol: str, indicators: dict, position: dict | None = None) -> Decision`
- Builds the system prompt (the frozen engine) + user message (indicator JSON + position
  context).
- Calls Claude with adaptive thinking, the web-search tool (when enabled), and a JSON-schema
  structured-output constraint.
- Handles `pause_turn` (server-tool loop) by re-sending until the model finishes, up to a
  small bounded number of continuations.
- Parses the final structured reply into a `Decision` and returns it.

`Decision` is a dataclass:
`action` ('BUY_NOW' | 'BUY_ON_PULLBACK' | 'BUY_ON_BREAKOUT' | 'SELL_NOW' | 'SHORT_NOW' |
'HOLD' | 'WAIT' | 'NO_TRADE'), `confidence` (0–100 int), `trade_quality` (0–100 int),
`entry` (float | None), `stop_loss` (float | None), `target1` (float | None), `target2`
(float | None), `target3` (float | None), `risk_reward` (float | None), `expected_move_pct`
(float | None), `invalidation` (str), `rationale` (str), `news_catalyst` (str | None),
`raw_response` (str — the full model output JSON, for audit → Phase 2 `decisions.raw_json`).

## The Claude call (shape)

- `model="claude-opus-4-8"`, `thinking={"type": "adaptive"}`, `max_tokens` sized for the
  engine's reasoning + structured reply (streaming not required at this size, but the call
  is made through `messages.create`).
- **System prompt = the engine**, one frozen text block with `cache_control:
  {"type": "ephemeral"}` so the loop's repeated calls read it from cache.
- **User message** = the indicator JSON (as text) plus a short position-context line.
- **Web search:** `tools=[{"type": "web_search_20260209", "name": "web_search",
  "max_uses": 3}]` when `use_web_search` is True; omitted otherwise.
- **Structured output:** `output_config={"format": {"type": "json_schema", "schema":
  DECISION_SCHEMA}}` where `DECISION_SCHEMA` matches the `Decision` fields
  (`additionalProperties: false`, all fields required, nullable via `["number", "null"]`).
- Sampling parameters (`temperature`/`top_p`/`top_k`) are NOT set — they 400 on Opus 4.8.

### Known unknown to verify (not assumed)
Whether the **web-search server tool and `output_config.format` structured output compose
in a single call** is not certain from the docs. The manual smoke test (below) verifies this
against the real API. **Documented fallback if they do not compose:** drop
`output_config.format`, instruct the model in the system prompt to end its turn with a single
JSON object matching the schema, and parse the JSON out of the final text block. The
`Decision` parsing layer is written to accept either path (a guaranteed-format text block, or
a JSON object extracted from free text), so the fallback is a one-line call-site change, not
a rewrite.

## Auth & configuration

- Credentials resolve via the standard Anthropic SDK chain — `ANTHROPIC_API_KEY`, or an
  `ant auth login` profile — never hardcoded. A bare `anthropic.Anthropic()` picks them up.
- `client_factory` is a constructor argument (default builds the real client) so unit tests
  inject a fake client and never hit the network — the same seam pattern as Phase 1's
  `GrowwClient(sdk_factory=...)`.

## Error handling

- `DecisionEngineError` is the single error type callers handle. `IndicatorError` subclasses
  it (indicator-fetch failures).
- API errors, `pause_turn` continuation exhaustion, malformed/missing structured output, and
  schema-parse failures all raise `DecisionEngineError` with the underlying cause chained.
- The engine never silently returns a default/"WAIT" on error — a failed decision raises, and
  Phase 4 decides what to do (skip the name this cycle). A fabricated decision is worse than a
  missed one.

## Testing

- Unit tests mock the `client_factory` boundary (a fake Anthropic client) and the
  `indicators.py` subprocess boundary — no network, no real script run:
  - prompt assembly: the indicator JSON and the engine prompt both appear in the request;
    the web-search tool is present when `use_web_search=True` and absent when False; the
    position context line reflects `position` (held vs flat).
  - structured-decision parsing: a well-formed model reply becomes a `Decision` with the
    right typed fields; a malformed reply raises `DecisionEngineError`.
  - `pause_turn` handling: a first response with `stop_reason="pause_turn"` is continued and
    the final decision is returned; exceeding the continuation cap raises.
  - error wrapping: an SDK exception and an indicator-fetch failure both surface as
    `DecisionEngineError` / `IndicatorError`.
- One manual, not-CI smoke script that makes a **real** Opus 4.8 call on one symbol
  (fetching real indicators for it), prints the decision, and thereby verifies the
  web-search + structured-output combination end to end. This is the Phase-3 analogue of
  Phase 1's Task 8 SDK-surface verification.

## Out of scope for Phase 3

Candidate screening / the "which stocks to consider" pool (Phase 4 — it feeds symbols into
`decide`), position sizing and pool/capital rules (Phase 4), order placement (Phase 1 client,
driven by Phase 4), scheduling (Phase 5), and the UI (Phase 6). Phase 3 is purely: symbol +
indicators (+ position) → a typed `Decision`.
