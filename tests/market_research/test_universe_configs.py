from __future__ import annotations

from pathlib import Path

from src.research.market_research.universe import get_universe_config, load_universe_configs


def test_load_universe_configs_from_repo():
    repo_root = Path(__file__).resolve().parents[2]
    cfgs = load_universe_configs(repo_root=repo_root)
    names = {c.name for c in cfgs}
    assert {
        "commodities",
        "country_etfs",
        "crypto",
        "etfs_us",
        "fx_emerging",
        "fx_majors",
        "global_indexes",
        "rates_macro",
        "us_equities",
    }.issubset(names)


def test_rates_macro_series_ids_are_flattened():
    repo_root = Path(__file__).resolve().parents[2]
    cfg = get_universe_config("rates_macro", repo_root=repo_root)
    assert cfg is not None
    ids = cfg.series_ids()
    assert "DGS10" in ids
    assert "SOFR" in ids
    assert len(ids) == len(set(ids))

