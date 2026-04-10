#!/usr/bin/env python3
"""
Universe configuration loader for the market research system.

Universes live under `config/universes/*.yaml` and describe:
- source: alpaca | fred | fmp
- asset_class: equity | etf | index | commodity | fx | crypto | macro
- symbols (explicit list), series (FRED), or a selection rule (Alpaca assets)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import yaml


def _default_repo_root() -> Path:
    # repo_root/.../src/research/market_research/universe.py -> parents[3] is repo root
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class UniverseConfig:
    name: str
    asset_class: str
    source: str
    description: str = ""
    priority: str = "medium"
    backfill_years: int = 20
    symbols: tuple[str, ...] = ()
    series: Dict[str, List[str]] | None = None
    selection: Dict[str, Any] | None = None
    raw: Dict[str, Any] | None = None

    def series_ids(self) -> List[str]:
        if not self.series:
            return []
        ids: List[str] = []
        for _, group_ids in self.series.items():
            for sid in group_ids or []:
                if isinstance(sid, str) and sid.strip():
                    ids.append(sid.strip())
        # De-dup while preserving order
        seen: set[str] = set()
        out: List[str] = []
        for sid in ids:
            if sid not in seen:
                seen.add(sid)
                out.append(sid)
        return out


def load_universe_configs(repo_root: Optional[Path] = None) -> List[UniverseConfig]:
    root = repo_root or _default_repo_root()
    universe_dir = root / "config" / "universes"
    if not universe_dir.exists():
        return []

    configs: List[UniverseConfig] = []
    for path in sorted(universe_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        name = str(data.get("name") or path.stem)
        asset_class = str(data.get("asset_class") or "")
        source = str(data.get("source") or "")
        description = str(data.get("description") or "")
        priority = str(data.get("priority") or "medium")
        backfill_years = int(data.get("backfill_years") or 20)
        symbols_raw = data.get("symbols") or []
        symbols: List[str] = []
        for sym in symbols_raw:
            if isinstance(sym, str) and sym.strip():
                symbols.append(sym.strip())
        series = data.get("series") if isinstance(data.get("series"), dict) else None
        selection = data.get("selection") if isinstance(data.get("selection"), dict) else None

        configs.append(
            UniverseConfig(
                name=name,
                asset_class=asset_class,
                source=source,
                description=description,
                priority=priority,
                backfill_years=backfill_years,
                symbols=tuple(symbols),
                series=series,
                selection=selection,
                raw=data,
            )
        )
    return configs


def get_universe_config(name: str, repo_root: Optional[Path] = None) -> Optional[UniverseConfig]:
    for cfg in load_universe_configs(repo_root=repo_root):
        if cfg.name == name:
            return cfg
    return None


def resolve_universe_items(
    cfg: UniverseConfig,
    *,
    alpaca_asset_fetcher: Optional[Callable[[], List[Dict[str, Any]]]] = None,
) -> List[str]:
    """
    Resolve a universe to a concrete list of symbols or series IDs.

    - If `cfg.symbols` is set, returns those.
    - If `cfg.series` is set, returns the flattened series IDs.
    - If `cfg.selection.method == alpaca_active_assets`, uses alpaca_asset_fetcher.
    """
    if cfg.symbols:
        return list(cfg.symbols)
    if cfg.series:
        return cfg.series_ids()

    if cfg.selection and cfg.selection.get("method") == "alpaca_active_assets":
        if not alpaca_asset_fetcher:
            raise RuntimeError("alpaca_asset_fetcher is required for alpaca_active_assets selection")
        assets = alpaca_asset_fetcher() or []
        filters = cfg.selection.get("filters") if isinstance(cfg.selection.get("filters"), dict) else {}
        cap_count = int(cfg.selection.get("cap_count") or 0)
        exchanges = set(filters.get("exchange") or [])
        want_tradable = filters.get("tradable")
        want_status = filters.get("status")

        symbols: List[str] = []
        for a in assets:
            if not isinstance(a, dict):
                continue
            if want_tradable is not None and bool(a.get("tradable")) is not bool(want_tradable):
                continue
            if want_status and str(a.get("status") or "").lower() != str(want_status).lower():
                continue
            if exchanges:
                exch = str(a.get("exchange") or "").upper()
                if exch not in {str(e).upper() for e in exchanges}:
                    continue
            sym = str(a.get("symbol") or "").strip()
            if sym:
                symbols.append(sym)
            if cap_count and len(symbols) >= cap_count:
                break
        return symbols

    return []


def iter_universe_symbol_tasks(
    configs: Iterable[UniverseConfig],
    *,
    include: Optional[set[str]] = None,
    exclude: Optional[set[str]] = None,
) -> Iterable[tuple[UniverseConfig, str]]:
    include = include or set()
    exclude = exclude or set()
    for cfg in configs:
        if include and cfg.name not in include:
            continue
        if cfg.name in exclude:
            continue
        for sym in cfg.symbols:
            yield cfg, sym
        for sid in cfg.series_ids():
            yield cfg, sid
