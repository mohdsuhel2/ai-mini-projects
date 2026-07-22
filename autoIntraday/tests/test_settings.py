import pytest

from settings import load_settings, Settings, SettingsError


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return str(p)


def test_missing_file_all_defaults(tmp_path, monkeypatch):
    # Run from an empty dir so the cwd `config.yaml` fallback finds nothing — this test asserts
    # the true no-config defaults, independent of any real config.yaml in the project root.
    monkeypatch.chdir(tmp_path)
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


def test_load_dotenv_sets_missing_keys_but_not_existing(tmp_path):
    from settings import load_dotenv
    envfile = tmp_path / ".env"
    envfile.write_text('GROWW_API_KEY=fromfile\nGROWW_TOTP_SECRET="quoted"\n# comment\n\n')
    env = {"GROWW_API_KEY": "already"}   # existing must NOT be overridden
    load_dotenv(env, path=str(envfile))
    assert env["GROWW_API_KEY"] == "already"          # not clobbered
    assert env["GROWW_TOTP_SECRET"] == "quoted"       # quotes stripped, set because absent


def test_load_dotenv_missing_file_is_noop(tmp_path):
    from settings import load_dotenv
    env = {}
    load_dotenv(env, path=str(tmp_path / "nope.env"))
    assert env == {}
