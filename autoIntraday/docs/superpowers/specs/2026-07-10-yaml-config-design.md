# autoIntraday add-on: YAML configuration file (Spring-Boot style)

## Context

The deployment/runtime settings are currently spread across environment variables
(`AUTOINTRADAY_DB`, `DECISION_BACKEND`, `INTRADAY_*`, `SCREENER_*`, `CLAUDE_BIN`,
`ANTHROPIC_API_KEY`, `GROWW_*`). This add-on adds a single `config.yaml` (like Spring Boot's
`application.yml`) that centralizes them, with `${VAR}` placeholders for secrets and env-var
override, so nothing that works today breaks.

### Decisions (from brainstorming)

- **DB stays the live source of truth for trading settings** (mode / total_pool /
  max_open_positions / capital_per_position / is_paused). The dashboard edits them and the cron
  reads them each cycle. The YAML only provides the **first-run seed** for them.
- **Secrets use `${VAR}` placeholders** (`anthropic_api_key: ${ANTHROPIC_API_KEY}`) — no secret
  values in the committed file; the real values stay in env / `.env` / the launchd plist.
- **Precedence: env var › YAML › built-in default** — Spring Boot semantics; an env var (or the
  launchd plist) still overrides the file, preserving all existing behavior.
- **Non-invasive:** `apply_to_environ()` pushes resolved values into `os.environ` (only where not
  already set), so the modules that already read env vars keep working unchanged — the YAML just
  becomes a convenient way to populate them.

## Architecture

### `settings.py` — the loader
- `Settings` dataclass: `db_path`, `decision_backend`, `model`, `web_search`, `claude_bin`,
  `indicator_python`, `indicator_script`, `screener_python`, `screener_script`,
  `anthropic_api_key`, `groww_api_key`, `groww_totp_secret`, `trading_defaults` (a dict:
  `mode`/`total_pool`/`max_open_positions`/`capital_per_position`/`is_paused`).
- `load_settings(path=None, env=os.environ) -> Settings`:
  - Config path = `path` arg → env `AUTOINTRADAY_CONFIG` → `./config.yaml` if it exists → else no
    file (all defaults).
  - Parse YAML (via `yaml.safe_load`). Missing/empty file → `{}`.
  - **`${VAR}` interpolation:** every string value equal to `${NAME}` (optionally with a
    `${NAME:-fallback}` form) is replaced by `env.get("NAME", fallback or "")`. Applied
    recursively over the parsed dict.
  - **Resolve each field with precedence env › YAML › default:** for a field with a canonical env
    var (e.g. `db_path`↔`AUTOINTRADAY_DB`, `decision_backend`↔`DECISION_BACKEND`, tool paths,
    `claude_bin`↔`CLAUDE_BIN`, the three secrets), the value is `env[VAR]` if set, else the YAML
    value, else the built-in default. `web_search`/`model`/`trading_defaults` come from YAML-or-
    default (no env equivalent).
  - `db_path` is `os.path.expanduser`-ed.
  - Returns the typed `Settings`.
- `Settings.apply_to_environ(env=os.environ) -> None`: for each field that has a canonical env var
  and a non-empty resolved value, set `env[VAR]` **only if not already present** (env-wins
  preserved). This lets the legacy env-reading modules (`indicators.py`, `screener.py`,
  `engine_factory.py`, `claude_cli_engine.py`, `run_cycle_job.py`) pick up YAML values with no
  change to them.

### `config.example.yaml` (committed) + `config.yaml` (gitignored)
The example is the documented template above (secrets as `${VAR}`). `.gitignore` gets `config.yaml`
and `config.local.yaml`.

### Wiring
- **`run_cycle_job.main()`**: first line loads settings and calls `apply_to_environ()` — so one
  `config.yaml` drives the whole scheduled job. Then it proceeds exactly as before (the lazily
  imported `indicators`/`screener`/`engine_factory` now see the populated env). The DB path used is
  `settings.db_path` (or, equivalently, the now-populated `AUTOINTRADAY_DB`).
- **`dashboard.py`**: loads settings and uses `settings.db_path` for the store.
- **`scripts/init_config.py`** (new): `load_settings()` → open `Store(settings.db_path)` →
  `store.update_config(**settings.trading_defaults)`. Run once to seed the DB's trading config
  from the YAML. Normal cycles never touch trading config (they read whatever is in the DB), so
  this never silently overrides the user's live dashboard changes.
- **Smoke scripts** call `load_settings().apply_to_environ()` at the top so they honor the config
  file too (still overridable by env).
- `pyyaml` added to `requirements.txt`.

## Error handling

- A missing config file is NOT an error — everything falls back to env vars then built-in
  defaults (identical to today's behavior). This keeps the file optional.
- A malformed YAML raises a `SettingsError` (a plain new exception) with the parse error, so a
  broken config fails loudly rather than silently ignoring settings.
- `${VAR}` referencing an unset env var resolves to empty string (same as an unset env var today) —
  not an error; the downstream code already handles missing creds at use time.

## Testing

- `settings.py` unit tests (pure, `tmp_path` + a passed-in `env` dict, no real files/os.environ
  mutation needed):
  - loads all fields from a YAML file.
  - `${VAR}` interpolation resolves from the provided env (and `${VAR:-default}` fallback).
  - precedence: an env value overrides the YAML value; YAML overrides the built-in default;
    default used when neither present.
  - missing file → all defaults (no error); malformed YAML → `SettingsError`.
  - `apply_to_environ` sets a var only when absent (does not clobber an existing env var).
  - `db_path` is `expanduser`-ed.
- `scripts/init_config.py` seed logic: factored as a pure `seed_trading_config(store, defaults)`
  helper, unit-tested against an in-memory `Store` (config reflects the defaults afterwards).
- Existing tests stay green: `run_cycle_job`'s `run_once`/`should_run` tests don't call `main()`,
  so the `main()` wiring is exercised only by the manual smoke run; `dashboard_data` tests are
  unaffected (dashboard.py itself is manual-verified).

## Out of scope

Profiles/multiple environments (Spring Boot `application-{profile}.yml`) — one file for this
single-user local setup; moving the live trading settings out of the DB (they stay there);
changing any trading logic.
