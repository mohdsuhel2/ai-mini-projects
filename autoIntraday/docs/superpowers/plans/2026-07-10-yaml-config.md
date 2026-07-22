# YAML Configuration File Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single `config.yaml` (Spring-Boot `application.yml` style) for deployment settings, with `${VAR}` secret placeholders and env-var override, without changing any module that currently reads env vars.

**Architecture:** `settings.py` loads YAML → interpolates `${VAR}` from env → resolves each field with precedence env › YAML › default → returns a typed `Settings`. `apply_to_environ()` pushes resolved values into `os.environ` (only where absent), so the existing env-reading modules keep working. Entry points load settings early; a one-time `init_config.py` seeds the DB trading config from the YAML.

**Tech Stack:** Python 3.10+, `pyyaml`, standard library, `pytest`. Reuses `store.py`.

## Global Constraints

- Precedence is **env var › YAML › built-in default** for every field that has a canonical env var. `apply_to_environ` sets an env var only when it is not already present (env-wins).
- Secrets are never written to the committed file — `config.example.yaml` uses `${VAR}` placeholders; the real `config.yaml` / `config.local.yaml` are gitignored.
- A missing config file is NOT an error (falls back to env → defaults). Malformed YAML raises `SettingsError`.
- The DB stays the live source of truth for trading settings; the YAML `trading_defaults` only seed the DB via the explicit `init_config.py` — normal cycles never touch trading config.
- No change to any trading logic or to the modules that read env vars (`indicators.py`, `screener.py`, `engine_factory.py`, `claude_cli_engine.py`).

---

### Task 1: `settings.py` + `config.example.yaml` + deps/gitignore

**Files:**
- Create: `settings.py`
- Create: `config.example.yaml`
- Modify: `requirements.txt`, `.gitignore`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `SettingsError(Exception)`; `Settings` dataclass (`db_path, decision_backend, model, web_search, claude_bin, indicator_python, indicator_script, screener_python, screener_script, anthropic_api_key, groww_api_key, groww_totp_secret, trading_defaults`); `load_settings(path=None, env=None) -> Settings`; `Settings.apply_to_environ(env=None) -> None`.

- [ ] **Step 1: Add `pyyaml`**

Append `pyyaml` to `requirements.txt`, then:
```bash
.venv/bin/pip install pyyaml
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_settings.py`:

```python
import pytest

from settings import load_settings, Settings, SettingsError


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return str(p)


def test_missing_file_all_defaults():
    s = load_settings(path=None, env={})
    assert s.decision_backend == "api"
    assert s.model == "claude-opus-4-8"
    assert s.web_search is True
    assert s.db_path.endswith("autointraday.db")
    assert s.trading_defaults["mode"] == "paper"


def test_loads_fields_from_yaml(tmp_path):
    path = _write(tmp_path, """
db_path: /tmp/foo.db
decision:
  backend: claude_cli
  model: claude-sonnet-5
  web_search: false
tools:
  claude_bin: /usr/local/bin/claude
  indicator_python: /x/py
  indicator_script: /x/ind.py
  screener_python: /x/py
  screener_script: /x/scr.py
trading_defaults:
  mode: paper
  total_pool: 50000
  max_open_positions: 3
  capital_per_position: 15000
  is_paused: false
""")
    s = load_settings(path=path, env={})
    assert s.db_path == "/tmp/foo.db"
    assert s.decision_backend == "claude_cli"
    assert s.model == "claude-sonnet-5"
    assert s.web_search is False
    assert s.claude_bin == "/usr/local/bin/claude"
    assert s.indicator_script == "/x/ind.py"
    assert s.trading_defaults["total_pool"] == 50000


def test_var_interpolation_from_env(tmp_path):
    path = _write(tmp_path, """
credentials:
  anthropic_api_key: ${ANTHROPIC_API_KEY}
  groww_api_key: ${GROWW_API_KEY}
  groww_totp_secret: ${GROWW_TOTP_SECRET}
""")
    s = load_settings(path=path, env={"ANTHROPIC_API_KEY": "sk-1", "GROWW_API_KEY": "gk",
                                      "GROWW_TOTP_SECRET": "ts"})
    assert s.anthropic_api_key == "sk-1"
    assert s.groww_api_key == "gk"
    assert s.groww_totp_secret == "ts"


def test_var_interpolation_unset_is_empty(tmp_path):
    path = _write(tmp_path, "credentials:\n  anthropic_api_key: ${ANTHROPIC_API_KEY}\n")
    s = load_settings(path=path, env={})
    assert s.anthropic_api_key == ""


def test_env_overrides_yaml(tmp_path):
    path = _write(tmp_path, "decision:\n  backend: claude_cli\ndb_path: /from/yaml.db\n")
    s = load_settings(path=path, env={"DECISION_BACKEND": "api", "AUTOINTRADAY_DB": "/from/env.db"})
    assert s.decision_backend == "api"        # env beats YAML
    assert s.db_path == "/from/env.db"


def test_malformed_yaml_raises(tmp_path):
    path = _write(tmp_path, "decision: [unclosed\n")
    with pytest.raises(SettingsError):
        load_settings(path=path, env={})


def test_db_path_expanduser():
    s = load_settings(path=None, env={"AUTOINTRADAY_DB": "~/x/y.db"})
    assert not s.db_path.startswith("~")


def test_apply_to_environ_sets_only_when_absent():
    s = load_settings(path=None, env={"DECISION_BACKEND": "claude_cli"})
    target = {"DECISION_BACKEND": "already-set"}   # existing value must NOT be clobbered
    s.apply_to_environ(env=target)
    assert target["DECISION_BACKEND"] == "already-set"
    assert target["INTRADAY_PYTHON"]              # a resolved default IS applied when absent
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'settings'`

- [ ] **Step 4: Implement `settings.py`**

```python
"""Layered configuration: config.yaml (Spring-Boot application.yml style) with ${VAR} secret
placeholders and env-var override. Precedence: env var > YAML > built-in default. See
docs/superpowers/specs/2026-07-10-yaml-config-design.md."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

_STOCKANALYZE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze"
_DEFAULT_DB = "~/.autointraday/autointraday.db"
_DEFAULT_TRADING = {"mode": "paper", "total_pool": 0, "max_open_positions": 0,
                    "capital_per_position": 0, "is_paused": False}

# field -> canonical env var (fields without an env var are YAML/default only)
_ENV_MAP = {
    "db_path": "AUTOINTRADAY_DB",
    "decision_backend": "DECISION_BACKEND",
    "claude_bin": "CLAUDE_BIN",
    "indicator_python": "INTRADAY_PYTHON",
    "indicator_script": "INTRADAY_SCRIPT",
    "screener_python": "SCREENER_PYTHON",
    "screener_script": "SCREENER_SCRIPT",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "groww_api_key": "GROWW_API_KEY",
    "groww_totp_secret": "GROWW_TOTP_SECRET",
}

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class SettingsError(Exception):
    """Raised when the config file cannot be parsed."""


@dataclass
class Settings:
    db_path: str
    decision_backend: str
    model: str
    web_search: bool
    claude_bin: str
    indicator_python: str
    indicator_script: str
    screener_python: str
    screener_script: str
    anthropic_api_key: str
    groww_api_key: str
    groww_totp_secret: str
    trading_defaults: dict = field(default_factory=dict)

    def apply_to_environ(self, env: Optional[dict] = None) -> None:
        env = os.environ if env is None else env
        for f, var in _ENV_MAP.items():
            val = getattr(self, f)
            if val and var not in env:
                env[var] = str(val)


def _interp(obj: Any, env: dict) -> Any:
    if isinstance(obj, dict):
        return {k: _interp(v, env) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interp(v, env) for v in obj]
    if isinstance(obj, str):
        return _VAR.sub(lambda m: env.get(m.group(1), m.group(2) or ""), obj)
    return obj


def load_settings(path: Optional[str] = None, env: Optional[dict] = None) -> Settings:
    env = os.environ if env is None else env
    cfg_path = path or env.get("AUTOINTRADAY_CONFIG")
    if cfg_path is None and os.path.exists("config.yaml"):
        cfg_path = "config.yaml"
    raw: dict = {}
    if cfg_path and os.path.exists(cfg_path):
        try:
            raw = yaml.safe_load(open(cfg_path, encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise SettingsError(f"could not parse config {cfg_path}: {e}") from e
    raw = _interp(raw, env)

    decision = raw.get("decision") or {}
    tools = raw.get("tools") or {}
    creds = raw.get("credentials") or {}

    def pick(env_var: Optional[str], yaml_val, default):
        if env_var and env.get(env_var):
            return env[env_var]
        if yaml_val is not None:
            return yaml_val
        return default

    return Settings(
        db_path=os.path.expanduser(pick("AUTOINTRADAY_DB", raw.get("db_path"), _DEFAULT_DB)),
        decision_backend=pick("DECISION_BACKEND", decision.get("backend"), "api"),
        model=decision.get("model") or "claude-opus-4-8",
        web_search=decision.get("web_search", True),
        claude_bin=pick("CLAUDE_BIN", tools.get("claude_bin"), "claude"),
        indicator_python=pick("INTRADAY_PYTHON", tools.get("indicator_python"),
                              f"{_STOCKANALYZE}/.venv/bin/python"),
        indicator_script=pick("INTRADAY_SCRIPT", tools.get("indicator_script"),
                              f"{_STOCKANALYZE}/stock_analyze_intraday.py"),
        screener_python=pick("SCREENER_PYTHON", tools.get("screener_python"),
                             f"{_STOCKANALYZE}/.venv/bin/python"),
        screener_script=pick("SCREENER_SCRIPT", tools.get("screener_script"),
                             f"{_STOCKANALYZE}/groww_intraday_screener.py"),
        anthropic_api_key=pick("ANTHROPIC_API_KEY", creds.get("anthropic_api_key"), ""),
        groww_api_key=pick("GROWW_API_KEY", creds.get("groww_api_key"), ""),
        groww_totp_secret=pick("GROWW_TOTP_SECRET", creds.get("groww_totp_secret"), ""),
        trading_defaults={**_DEFAULT_TRADING, **(raw.get("trading_defaults") or {})},
    )
```

- [ ] **Step 5: Create `config.example.yaml`**

```yaml
# autoIntraday configuration (Spring-Boot application.yml style).
# Copy to config.yaml (gitignored) and edit. Precedence: env var > this file > built-in default.
# Secrets use ${VAR} placeholders — real values stay in env / .env / the launchd plist,
# never in this file.

db_path: ~/.autointraday/autointraday.db

decision:
  backend: claude_cli          # api (Anthropic API, needs ANTHROPIC_API_KEY) | claude_cli (your Claude subscription)
  model: claude-opus-4-8
  web_search: true

credentials:
  # For backend: claude_cli, LEAVE anthropic_api_key unset (a set ANTHROPIC_API_KEY forces API billing).
  anthropic_api_key: ${ANTHROPIC_API_KEY}
  groww_api_key: ${GROWW_API_KEY}
  groww_totp_secret: ${GROWW_TOTP_SECRET}

tools:
  claude_bin: claude
  indicator_python: /Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python
  indicator_script: /Users/mohdsuhel/ai-mini-projects/StockAnalayze/stock_analyze_intraday.py
  screener_python: /Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python
  screener_script: /Users/mohdsuhel/ai-mini-projects/StockAnalayze/groww_intraday_screener.py

# First-run seed for the DB trading config. After `scripts/init_config.py` writes these to the DB,
# the DB is the live source of truth — change mode/pool/caps/pause in the dashboard, not here.
trading_defaults:
  mode: paper
  total_pool: 100000
  max_open_positions: 5
  capital_per_position: 20000
  is_paused: false
```

- [ ] **Step 6: gitignore the real config files**

Append to `.gitignore`:
```
config.yaml
config.local.yaml
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_settings.py -v`
Expected: PASS (8 tests)

- [ ] **Step 8: Commit**

```bash
git add settings.py config.example.yaml requirements.txt .gitignore tests/test_settings.py
git commit -m "Add settings.py YAML config loader with env override and secret placeholders"
```

---

### Task 2: Wire into the scheduler + dashboard + `init_config.py`

**Files:**
- Modify: `run_cycle_job.py`
- Modify: `dashboard.py`
- Create: `scripts/init_config.py`
- Test: `tests/test_init_config.py`

**Interfaces:**
- Consumes: `load_settings`, `Settings`, `store.Store`.
- Produces: `seed_trading_config(store, defaults: dict) -> None` (in `scripts/init_config.py`, unit-tested); `run_cycle_job.main` loads+applies settings before building; `dashboard.py` uses `settings.db_path`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_init_config.py`:

```python
from store import Store
from scripts.init_config import seed_trading_config


def test_seed_trading_config_writes_defaults():
    store = Store(":memory:")
    seed_trading_config(store, {"mode": "paper", "total_pool": 50000,
                                "max_open_positions": 4, "capital_per_position": 12500,
                                "is_paused": False})
    cfg = store.get_config()
    assert cfg.total_pool == 50000
    assert cfg.max_open_positions == 4
    assert cfg.capital_per_position == 12500
    assert cfg.mode == "paper"
    assert cfg.is_paused is False


def test_seed_trading_config_ignores_unknown_keys():
    store = Store(":memory:")
    # only the whitelisted trading fields are applied; extras are dropped, no crash
    seed_trading_config(store, {"total_pool": 10000, "bogus": 1})
    assert store.get_config().total_pool == 10000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_init_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.init_config'`

- [ ] **Step 3: Create `scripts/init_config.py`**

```python
#!/usr/bin/env python3
"""One-time DB init: seed the trading config (mode/pool/caps/pause) from config.yaml's
trading_defaults. After this, the DB is the live source of truth — change these in the
dashboard, not the YAML. Safe to re-run (it just re-applies the YAML defaults).

Usage: .venv/bin/python scripts/init_config.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from settings import load_settings
from store import Store

_TRADING_FIELDS = ("mode", "total_pool", "max_open_positions", "capital_per_position",
                   "is_paused")


def seed_trading_config(store, defaults: dict) -> None:
    fields = {k: defaults[k] for k in _TRADING_FIELDS if k in defaults}
    if fields:
        store.update_config(**fields)


def main() -> int:
    settings = load_settings()
    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
    store = Store(settings.db_path)
    seed_trading_config(store, settings.trading_defaults)
    cfg = store.get_config()
    print(f"seeded {settings.db_path}: mode={cfg.mode} pool={cfg.total_pool} "
          f"max_positions={cfg.max_open_positions} per_position={cfg.capital_per_position} "
          f"paused={cfg.is_paused}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Wire settings into `run_cycle_job.py`**

At the top of `main()` (the FIRST statements, before `_build_store`/`_build_orchestrator` run), load and apply settings, and use `settings.db_path`. Edit `main`:

```python
def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from settings import load_settings
    settings = load_settings()
    settings.apply_to_environ()          # populate env for the lazily-imported tool modules
    now = datetime.now(IST)
    holidays = load_holidays(HOLIDAYS_PATH)
    try:
        run_once(now, lambda: _build_store(settings.db_path), _build_orchestrator, holidays)
        return 0
    except Exception:
        log.exception("cycle failed")
        return 1
```

(Keep `DEFAULT_DB` for backward compatibility, but `main` now passes `settings.db_path`. `run_once`/`should_run`/`_build_orchestrator` are otherwise unchanged.)

- [ ] **Step 5: Wire settings into `dashboard.py`**

Replace the `DB_PATH = os.environ.get(...)` line with a settings load:

```python
from settings import load_settings

DB_PATH = load_settings().db_path
```

(Leave the rest of `dashboard.py` unchanged.)

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_init_config.py tests/test_run_cycle_job.py -v`
Expected: PASS — the seed tests + the existing run_cycle_job tests (which inject factories and don't call `main`).

- [ ] **Step 7: Commit**

```bash
git add run_cycle_job.py dashboard.py scripts/init_config.py tests/test_init_config.py
git commit -m "Wire settings into scheduler + dashboard; add init_config DB seeder"
```

---

### Task 3: Smoke scripts honor settings + docs

**Files:**
- Modify: `scripts/smoke_test_cycle.py`, `scripts/smoke_test_decision.py`, `scripts/smoke_test_claude_cli.py`, `scripts/smoke_test_groww_auth.py`
- Modify: `README.md`

**Interfaces:** no new API — makes the smoke scripts config-aware and documents the file.

- [ ] **Step 1: Make the smoke scripts load settings before the tool imports**

In each of the four smoke scripts, immediately after `sys.path.insert(0, ".")` and BEFORE the
`from indicators ...` / `from groww_client ...` imports, add:

```python
from settings import load_settings
load_settings().apply_to_environ()
```

This must come before importing `indicators`/`screener` (which read `INTRADAY_*`/`SCREENER_*` at
import) and before constructing `GrowwClient`/engines, so the config's values take effect. (For
`smoke_test_groww_auth.py` and `smoke_test_decision.py`, place it right after the `sys.path`
line, before their `from groww_client`/`from decision_engine`/`from indicators` imports.)

- [ ] **Step 2: Confirm the full suite passes and every smoke script still parses/imports**

Run: `.venv/bin/python -m pytest -q`
Expected: all green.
Run: `for f in scripts/smoke_test_*.py; do .venv/bin/python -c "import ast; ast.parse(open('$f').read())" && echo "$f OK"; done`

- [ ] **Step 3: Add a "Configuration file" section to `README.md`**

Append to `README.md`:

```markdown
## Configuration: config.yaml

Instead of setting many environment variables, copy `config.example.yaml` to `config.yaml`
(gitignored) and edit it — a single Spring-Boot-`application.yml`-style file for deployment
settings (DB path, decision backend + model, tool paths, and `trading_defaults` that seed the
DB on first run). Secrets use `${VAR}` placeholders, so real keys stay in your env / `.env` /
the launchd plist, never in the file.

Precedence is **env var > config.yaml > built-in default** — an env var (or a value in the
launchd plist) still overrides the file, so existing setups keep working.

\`\`\`bash
cp config.example.yaml config.yaml         # then edit paths / backend / trading_defaults
export $(cat .env | xargs)                 # secrets the ${VAR} placeholders reference
.venv/bin/python scripts/init_config.py    # seed the DB trading config from the YAML (run once)
\`\`\`

`AUTOINTRADAY_CONFIG=/path/to/config.yaml` points at a config file elsewhere. The scheduler
(`run_cycle_job.py`), dashboard, and smoke scripts all read it. Live trading settings
(mode/pool/caps/pause) remain owned by the DB and are edited in the dashboard — the YAML only
seeds them.
```

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_test_cycle.py scripts/smoke_test_decision.py scripts/smoke_test_claude_cli.py scripts/smoke_test_groww_auth.py README.md
git commit -m "Make smoke scripts config-aware; document config.yaml"
```

---

## Self-Review Notes

- **Spec coverage:** `settings.py` loader with `${VAR}` interpolation + env-override precedence + `apply_to_environ` (Task 1) · `config.example.yaml` template with secret placeholders + gitignore + pyyaml dep (Task 1) · scheduler + dashboard wiring + `init_config.py` DB seeder (Task 2) · config-aware smoke scripts + README (Task 3). All spec sections map to a task.
- **Type consistency:** `load_settings(path, env)` / `Settings.apply_to_environ(env)` signatures match across the tests and callers; `_ENV_MAP` field names match the `Settings` fields; `seed_trading_config` uses the store's `update_config` whitelisted keys; `run_cycle_job.main` and `dashboard.py` use `settings.db_path` consistently with `_build_store(db_path)`.
- **No placeholders:** every step has complete runnable code. The import-ordering requirement (load+apply settings BEFORE importing `indicators`/`screener`) is called out explicitly in the scheduler wiring and the smoke-script step, because those modules resolve their env-var defaults at import time. Expected test counts: 8 settings, 2 init_config.
- **Non-invasive:** `indicators.py`, `screener.py`, `engine_factory.py`, `claude_cli_engine.py` are unchanged — they still read env vars, which `apply_to_environ` now populates from the YAML (env still wins). The API/CLI backends and all trading logic are untouched.
