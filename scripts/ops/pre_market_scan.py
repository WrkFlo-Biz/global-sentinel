#!/usr/bin/env python3
"""Pre-market scanner for Global Sentinel.

Aggregates oil futures, overnight news, gap analysis, options flow,
VIX, momentum, regime state, and chokepoint risk into a compact
pre-market brief suitable for Telegram delivery.

Can be invoked from a systemd timer at 08:00 ET or run manually.
"""
from __future__ import annotations

import json
import logging
import pathlib
import datetime
import importlib
import sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)


class PreMarketScanner:
    """Collect and format pre-market intelligence."""

    def __init__(self, repo_root: str | pathlib.Path | None = None) -> None:
        if repo_root is None:
            repo_root = pathlib.Path(__file__).resolve().parents[2]
        self.repo_root = pathlib.Path(repo_root)
        self._config = self._load_config()
        self._scan_result: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _load_config(self) -> dict[str, Any]:
        """Load scanner configuration from repo config files."""
        cfg_path = self.repo_root / "config" / "scanner.json"
        if cfg_path.exists():
            with open(cfg_path) as f:
                return json.load(f)
        # Sensible defaults when no config file is present.
        return {
            "watchlist_size": 250,
            "gap_threshold_pct": 2.0,
            "telegram_enabled": True,
            "report_dir": "reports/operational",
        }

    # ------------------------------------------------------------------
    # Bridge helpers (best-effort imports from the monorepo)
    # ------------------------------------------------------------------

    def _try_import(self, module_path: str) -> Any:
        """Attempt to import a module from the repo; return None on failure."""
        try:
            return importlib.import_module(module_path)
        except (ImportError, ModuleNotFoundError):
            logger.debug("Module %s not available", module_path)
            return None

    # ------------------------------------------------------------------
    # Data collectors
    # ------------------------------------------------------------------

    def _fetch_oil_futures(self) -> dict[str, Any]:
        """Return oil futures snapshot: price, change_pct, direction."""
        bridge = self._try_import("src.ingestion.commodity_futures_bridge")
        if bridge and hasattr(bridge, "get_latest_oil"):
            try:
                data = bridge.get_latest_oil()
                price = float(data.get("price", 0.0))
                change_pct = float(data.get("change_pct", 0.0))
                return {
                    "price": price,
                    "change_pct": change_pct,
                    "direction": "up" if change_pct > 0 else ("down" if change_pct < 0 else "flat"),
                }
            except Exception as exc:
                logger.warning("Oil futures fetch failed: %s", exc)
        return {"price": 0.0, "change_pct": 0.0, "direction": "unknown"}

    def _fetch_overnight_news(self) -> list[dict[str, Any]]:
        """Pull overnight headlines from GDELT / Exa bridges."""
        results: list[dict[str, Any]] = []
        for mod_name, source in [
            ("src.ingestion.gdelt_bridge", "GDELT"),
            ("src.ingestion.exa_bridge", "Exa"),
        ]:
            bridge = self._try_import(mod_name)
            if bridge and hasattr(bridge, "get_recent_headlines"):
                try:
                    for item in bridge.get_recent_headlines():
                        results.append({
                            "source": source,
                            "headline": item.get("headline", ""),
                            "relevance": item.get("relevance", 0.0),
                        })
                except Exception as exc:
                    logger.warning("News fetch from %s failed: %s", source, exc)
        return results

    def _fetch_gaps(self) -> list[dict[str, Any]]:
        """Identify watchlist symbols gapping > threshold pre-market."""
        threshold = self._config.get("gap_threshold_pct", 2.0)
        bridge = self._try_import("src.ingestion.market_data_bridge")
        gaps: list[dict[str, Any]] = []
        if bridge and hasattr(bridge, "get_premarket_gaps"):
            try:
                raw = bridge.get_premarket_gaps(
                    min_gap_pct=threshold,
                    limit=self._config.get("watchlist_size", 250),
                )
                for g in raw:
                    gaps.append({
                        "symbol": g.get("symbol", ""),
                        "gap_pct": float(g.get("gap_pct", 0.0)),
                        "volume_ratio": float(g.get("volume_ratio", 0.0)),
                        "category": g.get("category", "unknown"),
                    })
            except Exception as exc:
                logger.warning("Gap scan failed: %s", exc)
        return gaps

    def _fetch_options_flow(self) -> list[dict[str, Any]]:
        """Return unusual options activity from the options_greeks bridge."""
        bridge = self._try_import("src.research.option_scenario_pricer")
        if bridge and hasattr(bridge, "get_unusual_flow"):
            try:
                return bridge.get_unusual_flow()
            except Exception as exc:
                logger.warning("Options flow fetch failed: %s", exc)
        return []

    def _fetch_vix(self) -> float:
        """Return the current VIX level."""
        bridge = self._try_import("src.ingestion.market_data_bridge")
        if bridge and hasattr(bridge, "get_vix"):
            try:
                return float(bridge.get_vix())
            except Exception as exc:
                logger.warning("VIX fetch failed: %s", exc)
        return 0.0

    def _fetch_momentum_overnight(self) -> list[dict[str, Any]]:
        """Return symbols with strongest overnight moves."""
        bridge = self._try_import("src.ingestion.market_data_bridge")
        if bridge and hasattr(bridge, "get_overnight_movers"):
            try:
                return bridge.get_overnight_movers(top_n=10)
            except Exception as exc:
                logger.warning("Overnight momentum fetch failed: %s", exc)
        return []

    def _fetch_regime(self) -> dict[str, Any]:
        """Return regime probability and mode from regime detector."""
        bridge = self._try_import("src.research.regime_conditioned_optimizer")
        if bridge and hasattr(bridge, "current_regime"):
            try:
                r = bridge.current_regime()
                return {
                    "probability": float(r.get("probability", 0.0)),
                    "mode": r.get("mode", "unknown"),
                }
            except Exception as exc:
                logger.warning("Regime fetch failed: %s", exc)
        return {"probability": 0.0, "mode": "unknown"}

    def _fetch_chokepoints(self) -> dict[str, float]:
        """Return chokepoint risk scores."""
        bridge = self._try_import("src.ingestion.chokepoint_risk_bridge")
        if bridge and hasattr(bridge, "get_risk_scores"):
            try:
                data = bridge.get_risk_scores()
                return {
                    "hormuz": float(data.get("hormuz", 0.0)),
                    "bab_el_mandeb": float(data.get("bab_el_mandeb", 0.0)),
                    "med": float(data.get("med", 0.0)),
                    "composite": float(data.get("composite", 0.0)),
                }
            except Exception as exc:
                logger.warning("Chokepoint fetch failed: %s", exc)
        return {"hormuz": 0.0, "bab_el_mandeb": 0.0, "med": 0.0, "composite": 0.0}

    def _predict_strategies(self) -> list[str]:
        """Predict which strategies are likely to fire at open."""
        bridge = self._try_import("src.strategies")
        if bridge and hasattr(bridge, "predict_active"):
            try:
                return bridge.predict_active()
            except Exception as exc:
                logger.warning("Strategy prediction failed: %s", exc)
        return []

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def scan(self) -> dict[str, Any]:
        """Execute the full pre-market scan and return structured results."""
        logger.info("Starting pre-market scan")
        result: dict[str, Any] = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "oil_futures": self._fetch_oil_futures(),
            "overnight_news": self._fetch_overnight_news(),
            "gaps": self._fetch_gaps(),
            "options_flow": self._fetch_options_flow(),
            "vix_level": self._fetch_vix(),
            "momentum_overnight": self._fetch_momentum_overnight(),
            "regime": self._fetch_regime(),
            "chokepoints": self._fetch_chokepoints(),
            "strategies_expected_to_fire": self._predict_strategies(),
        }
        self._scan_result = result
        logger.info("Pre-market scan complete")
        return result

    # ------------------------------------------------------------------
    # Telegram formatting
    # ------------------------------------------------------------------

    def format_telegram(self, result: dict[str, Any] | None = None) -> str:
        """Format scan results into a compact Telegram-friendly brief."""
        r = result or self._scan_result
        if r is None:
            return "No scan data available. Run scan() first."

        oil = r.get("oil_futures", {})
        oil_price = oil.get("price", 0.0)
        oil_chg = oil.get("change_pct", 0.0)
        oil_sign = "+" if oil_chg >= 0 else ""

        vix = r.get("vix_level", 0.0)

        # Top gap
        gaps = r.get("gaps", [])
        gap_line = ""
        if gaps:
            top = sorted(gaps, key=lambda g: abs(g.get("gap_pct", 0)), reverse=True)[0]
            gap_line = f", {top['symbol']} gap {'+' if top['gap_pct'] >= 0 else ''}{top['gap_pct']:.1f}%"

        # Unusual movers from momentum
        movers = r.get("momentum_overnight", [])
        mover_parts: list[str] = []
        for m in movers[:3]:
            sym = m.get("symbol", "???")
            chg = m.get("change_pct", 0.0)
            mover_parts.append(f"{sym} {'+' if chg >= 0 else ''}{chg:.1f}%")
        mover_line = ", ".join(mover_parts) if mover_parts else "none"

        regime = r.get("regime", {})
        regime_prob = regime.get("probability", 0.0)

        cp = r.get("chokepoints", {})
        h = cp.get("hormuz", 0.0)
        b = cp.get("bab_el_mandeb", 0.0)
        med = cp.get("med", 0.0)

        strats = r.get("strategies_expected_to_fire", [])
        strat_line = ", ".join(strats) if strats else "none"

        lines = [
            f"\U0001f4cb Pre-Market: Oil ${oil_price:.2f} ({oil_sign}{oil_chg:.1f}%){gap_line}, VIX {vix:.0f}",
            f"{len(movers)} unusual moves: {mover_line}",
            f"Regime {regime_prob:.2f}, Chokepoints H={h:.2f} B={b:.2f} M={med:.2f}",
            f"Strategies firing: {strat_line}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_report(self, result: dict[str, Any], date_str: str) -> pathlib.Path:
        """Save scan result to reports/operational/pre_market_{date}.json."""
        report_dir = self.repo_root / self._config.get("report_dir", "reports/operational")
        report_dir.mkdir(parents=True, exist_ok=True)
        out_path = report_dir / f"pre_market_{date_str}.json"
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        logger.info("Report saved to %s", out_path)
        return out_path

    def _send_telegram(self, message: str) -> None:
        """Send the formatted brief via Telegram notifier."""
        notifier = self._try_import("src.notifications.telegram_notifier")
        if notifier and hasattr(notifier, "send"):
            try:
                notifier.send(message)
                logger.info("Telegram brief sent")
            except Exception as exc:
                logger.warning("Telegram send failed: %s", exc)
        else:
            logger.info("Telegram notifier not available; printing to stdout")
            print(message)


def main() -> None:
    """Entry point for pre-market scan (systemd timer or manual)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    scanner = PreMarketScanner()
    result = scanner.scan()

    brief = scanner.format_telegram(result)
    scanner._send_telegram(brief)

    today = datetime.date.today().isoformat()
    scanner._save_report(result, today)

    print(brief)


if __name__ == "__main__":
    main()
