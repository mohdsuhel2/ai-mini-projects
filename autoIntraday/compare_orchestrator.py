"""Compare-testing orchestration — runs N strategies each cycle on IDENTICAL market data as
fully isolated PAPER ledgers, so they can be objectively compared before any live use. Never
places a broker order (every client is paper and asserted so). See
docs/superpowers/specs/2026-07-24-multi-strategy-compare-design.md."""
from __future__ import annotations

import contextvars
import logging
from itertools import zip_longest
from typing import Callable

from orchestrator import SLOT_HEADROOM, Orchestrator
from store import ScopedStore
from strategies import compare_ledger_id

log = logging.getLogger("autointraday.compare")

# The strategy whose cycle is currently executing — a _StrategyTagFilter reads this to prefix every
# autointraday log line with e.g. [intraday-v1], so the interleaved compare log is attributable.
# Set per strategy in the loop below; needs no changes to the Orchestrator's own log calls.
_current_strategy: contextvars.ContextVar = contextvars.ContextVar(
    "compare_strategy", default=None)


class _StrategyTagFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        sid = _current_strategy.get()
        if sid and not str(record.msg).startswith("["):
            record.msg = f"[{sid}] {record.msg}"
        return True


# The loggers whose records get strategy-tagged. A logging Filter only rewrites records emitted
# DIRECTLY to the logger it's attached to (it is NOT re-applied to a child's propagated records),
# so we attach to each source logger. The orchestrator emits the trade actions (BUY/exit/stop
# moves) we care most about; add more names here to tag engine/client lines too.
_TAGGED_LOGGERS = ("autointraday.orchestrator",)


def install_strategy_log_tagging(logger_names=_TAGGED_LOGGERS) -> None:
    for name in logger_names:
        lg = logging.getLogger(name)
        if not any(isinstance(f, _StrategyTagFilter) for f in lg.filters):
            lg.addFilter(_StrategyTagFilter())


class SharedMarketData:
    """Per-cycle cache guaranteeing every strategy sees IDENTICAL indicators and the IDENTICAL
    candidate universe. The indicator tool runs once per symbol and the screener once per
    direction; all strategies reuse the exact same objects. A fresh instance per cycle keeps
    market data fresh each cycle while sharing it within a cycle."""

    def __init__(self, get_indicators: Callable[[str], dict],
                 get_candidates: Callable[..., list], top: int):
        self._get_indicators = get_indicators
        self._get_candidates = get_candidates
        self._top = top
        self._ind: dict[str, dict] = {}
        self._cand: dict[str, list] = {}
        self.indicator_calls = 0      # how many times the underlying tool actually ran (for tests)
        self.candidate_calls = 0

    def indicators(self, symbol: str) -> dict:
        if symbol not in self._ind:
            self.indicator_calls += 1
            self._ind[symbol] = self._get_indicators(symbol)
        return self._ind[symbol]

    def candidates(self, direction: str = "up", top=None) -> list:
        if direction not in self._cand:
            self.candidate_calls += 1
            self._cand[direction] = self._get_candidates(direction=direction, top=self._top)
        return self._cand[direction]


class PrecomputedDecisionEngine:
    """Serves entry decisions from a bulk pre-decide (one call over the whole shared universe),
    falling back to the real per-name engine for anything not pre-decided or for open-position
    management (which the bulk pass doesn't cover). This is how Compare runs ~1 decision call per
    strategy instead of one per candidate, on identical data."""

    def __init__(self, decisions_by_symbol: dict, fallback):
        self._d = decisions_by_symbol
        self._fallback = fallback

    def decide(self, symbol, indicators, position=None, book=None):
        if position is None:
            cached = self._d.get(symbol) or self._d.get(str(symbol).upper())
            if cached is not None:
                return cached
        return self._fallback.decide(symbol, indicators, position, book)


class CompareOrchestrator:
    """Drives several strategies each cycle over one SharedMarketData, each inside its own paper,
    strategy_id-scoped Orchestrator. Strategies share only the market data; positions, orders,
    capital, P&L and logs are fully isolated. Broker is hard-off: every client is paper and any
    non-paper client raises."""

    def __init__(self, store, strategies, get_indicators, get_candidates,
                 client_factory: Callable[[], object] | None = None,
                 now_provider: Callable | None = None, screen_top: int | None = None):
        if not strategies:
            raise ValueError("compare mode needs at least one strategy")
        self.store = store
        self.strategies = list(strategies)
        self._get_indicators = get_indicators
        self._get_candidates = get_candidates
        self._now = now_provider
        self._screen_top = screen_top
        self._client_factory = client_factory or self._default_paper_client
        install_strategy_log_tagging()

    @staticmethod
    def _default_paper_client():
        from groww_client import GrowwClient
        return GrowwClient(mode="paper")

    def run_cycle(self, squareoff_only: bool = False) -> dict:
        cfg = self.store.get_config()
        top = self._screen_top or (cfg.max_open_positions + SLOT_HEADROOM)
        shared = SharedMarketData(self._get_indicators, self._get_candidates, top=top)
        # Bulk pre-decide: build the shared universe + its indicators ONCE, then each strategy
        # decides the whole list in a single call. Skipped on square-off (no new entries).
        bulk_items = [] if squareoff_only else self._universe_items(shared)
        results: dict[str, dict] = {}
        total_errors = 0
        for strat in self.strategies:
            client = self._client_factory()
            if getattr(client, "mode", None) != "paper":
                raise RuntimeError(
                    "compare mode must never use a live broker client — refusing to run")
            orch_kwargs = {"now_provider": self._now} if self._now else {}
            token = _current_strategy.set(strat.id)   # tag bulk-decide + cycle logs with the strategy
            try:
                engine = self._engine_for(strat, bulk_items)
                # Isolated compare ledger ("cmp:<id>") so the comparison starts from a clean slate,
                # never inheriting the strategy's live/paper P&L history.
                orch = Orchestrator(
                    store=ScopedStore(self.store, compare_ledger_id(strat.id)), client=client,
                    engine=engine,
                    get_indicators=shared.indicators, get_candidates=shared.candidates,
                    screen_engine=None,      # classic loop over SHARED universe (cached decisions)
                    **orch_kwargs)
                results[strat.id] = orch.run_cycle(squareoff_only=squareoff_only)
            finally:
                _current_strategy.reset(token)
            total_errors += results[strat.id].get("errors", 0)
            log.info("%s -> %s", strat.id, results[strat.id])
        return {"mode": "compare", "strategies": results, "errors": total_errors,
                "indicator_fetches": shared.indicator_calls}

    def _universe_items(self, shared: "SharedMarketData") -> list[dict]:
        """The shared candidate universe (both directions, interleaved + de-duped) paired with each
        name's indicators — fetched once, reused by every strategy's bulk decide + its Orchestrator."""
        try:
            ups, downs = shared.candidates("up"), shared.candidates("down")
        except Exception:
            log.warning("compare: shared screen failed — no bulk universe this cycle", exc_info=True)
            return []
        seen: set = set()
        items: list[dict] = []
        for pair in zip_longest(ups or [], downs or []):
            for cand in pair:
                sym = cand and cand.get("symbol")
                if not sym or sym in seen:
                    continue
                seen.add(sym)
                try:
                    items.append({"symbol": sym, "indicators": shared.indicators(sym)})
                except Exception:
                    log.warning("compare: indicators failed for %s — skipping in bulk", sym)
        return items

    def _engine_for(self, strat, bulk_items: list[dict]):
        """A PrecomputedDecisionEngine backed by ONE bulk decide over the shared universe, with the
        per-name skill engine as fallback (open-position management + any missed name). A bulk
        failure degrades gracefully to per-name — never fails the cycle."""
        fallback = strat.make_decision_engine()
        if not bulk_items:
            return fallback
        try:
            decided = strat.make_bulk_engine().decide_many(bulk_items)
            log.info("%s bulk-decided %d/%d names in one call", strat.id, len(decided),
                     len(bulk_items))
        except Exception:
            log.warning("%s bulk decide failed — falling back to per-name", strat.id, exc_info=True)
            decided = {}
        return PrecomputedDecisionEngine(decided, fallback)
