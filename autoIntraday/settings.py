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
    "screen_mode": "SCREEN_MODE",
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
    screen_mode: str
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


_DOTENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load_dotenv(env: Optional[dict] = None, path: str = _DOTENV) -> None:
    """Populate `env` from the project .env (KEY=VALUE lines) WITHOUT overriding anything already
    set — so a launchd plist / real shell env still wins, but a plain `streamlit run` (or the
    swing subprocess) picks up the same GROWW creds the intraday flow uses. No-op if .env absent."""
    env = os.environ if env is None else env
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in env:
                env[key] = val


def load_settings(path: Optional[str] = None, env: Optional[dict] = None) -> Settings:
    env = os.environ if env is None else env
    load_dotenv(env)                      # same creds as the intraday flow, if a .env exists
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
        screen_mode=pick("SCREEN_MODE", decision.get("screen_mode"), "skill"),
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
