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
