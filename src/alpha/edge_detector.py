"""Edge detector — core profit-finding module that runs every monitoring cycle."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known commodity / macro cascades
# ---------------------------------------------------------------------------
KNOWN_CASCADES: list[dict[str, Any]] = [
    {
        "primary": "oil",
        "secondary_symbols": ["MOS", "CF"],
        "label": "oil → fertilizer",
        "lag_days_min": 3,
        "lag_days_max": 5,
    },
    {
        "primary": "oil",
        "secondary_symbols": ["JETS", "AAL", "DAL", "UAL"],
        "label": "oil → gasoline → airlines",
        "lag_days_min": 1,
        "lag_days_max": 2,
    },
    {
        "primary": "nat_gas",
        "secondary_symbols": ["MOS", "CF"],
        "label": "nat_gas → fertilizer",
        "lag_days_min": 5,
        "lag_days_max": 7,
    },
    {
        "primary": "gold",
        "secondary_symbols": ["GDX", "SLV"],
        "label": "gold → miners → silver",
        "lag_days_min": 0,
        "lag_days_max": 1,
    },
    {
        "primary": "DXY",
        "secondary_symbols": ["EEM", "INDA"],
        "label": "DXY → EM",
        "lag_days_min": 1,
        "lag_days_max": 2,
    },
    {
        "primary": "VIX",
        "secondary_symbols": ["UVXY", "VXX", "SVXY"],
        "label": "VIX spike → vol instruments",
        "lag_days_min": 0,
        "lag_days_max": 0,
    },
]

# Regime-probability thresholds that matter for positioning
_REGIME_THRESHOLDS = [0.3, 0.5, 0.7]


class EdgeDetector:
    """Scans bridge results, scorecard, and market data for actionable edges.

    Designed to be called once per monitoring cycle.  All heavy state lives in
    the tracking dicts so the detector can remember across cycles.
    """

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]

        # Persistent tracking across cycles
        self.signal_lag: dict[str, list[dict[str, Any]]] = {}
        self.sector_correlations: dict[str, list[float]] = {}
        self.cascade_tracking: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(
        self,
        bridge_results: dict[str, Any] | None = None,
        scorecard: dict[str, Any] | None = None,
        market_data: dict[str, Any] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Run all edge detectors and return a unified findings dict."""
        bridge_results = bridge_results or {}
        scorecard = scorecard or {}
        market_data = market_data or {}

        signal_lag_findings = self._detect_signal_lag(bridge_results, market_data)
        divergence_findings = self._detect_divergences(market_data)
        cascade_findings = self._detect_cascades(market_data)
        regime_transition_findings = self._detect_regime_transitions(scorecard)
        smart_money_findings = self._detect_smart_money(bridge_results)

        findings = {
            "signal_lag_findings": signal_lag_findings,
            "divergence_findings": divergence_findings,
            "cascade_findings": cascade_findings,
            "regime_transition_findings": regime_transition_findings,
            "smart_money_findings": smart_money_findings,
        }

        non_empty = sum(1 for v in findings.values() if v)
        logger.info("EdgeDetector scan complete — %d categories with findings", non_empty)
        return findings

    def format_telegram(self, findings: dict[str, list[dict[str, Any]]] | None = None) -> str:
        """Return a compact Telegram-friendly summary of the most recent scan."""
        if findings is None:
            findings = self.scan()

        parts: list[str] = []

        # Cascade alerts
        for cf in findings.get("cascade_findings", []):
            if cf.get("status") == "pending":
                parts.append(
                    f"{cf['secondary_symbol']} flat while {cf['primary_move']}, "
                    f"cascade lag {cf['expected_lag_days']}d"
                )

        # Divergence alerts
        for df in findings.get("divergence_findings", []):
            parts.append(
                f"{df['sector']} divergence: {df['action']}"
            )

        # Smart money
        for sm in findings.get("smart_money_findings", []):
            parts.append(f"{sm['symbol']} {sm['signal_type']}")

        if not parts:
            return "\U0001f52e Edge: no actionable signals this cycle"

        return "\U0001f52e Edge: " + " | ".join(parts)

    # ------------------------------------------------------------------
    # Sub-methods
    # ------------------------------------------------------------------

    def _detect_signal_lag(
        self,
        bridge_results: dict[str, Any],
        market_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Track when signals arrive vs when the market actually moves.

        Compares bridge signal timestamps against price-action timestamps to
        find bridges that consistently *lead* the market.
        """
        findings: list[dict[str, Any]] = []
        now_ts = time.time()

        signals = bridge_results.get("signals", [])
        price_moves = market_data.get("price_moves", [])

        if not signals or not price_moves:
            return findings

        # Build a quick lookup of recent price moves keyed by symbol
        move_by_symbol: dict[str, dict[str, Any]] = {}
        for pm in price_moves:
            sym = pm.get("symbol", "")
            if sym:
                move_by_symbol[sym] = pm

        for sig in signals:
            signal_name = sig.get("name", "unknown")
            bridge_source = sig.get("bridge", "unknown")
            signal_ts = sig.get("timestamp", now_ts)
            symbol = sig.get("symbol", "")

            if symbol not in move_by_symbol:
                continue

            move_ts = move_by_symbol[symbol].get("timestamp", now_ts)
            lag_minutes = (move_ts - signal_ts) / 60.0

            # Track history for this signal
            self.signal_lag.setdefault(signal_name, []).append(
                {"lag_minutes": lag_minutes, "ts": now_ts}
            )
            # Keep last 100 observations
            self.signal_lag[signal_name] = self.signal_lag[signal_name][-100:]

            is_leading = lag_minutes > 0  # signal arrived before the move

            findings.append({
                "signal_name": signal_name,
                "bridge_source": bridge_source,
                "lag_minutes": round(lag_minutes, 1),
                "is_leading": is_leading,
            })

        return findings

    def _detect_divergences(
        self,
        market_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Compare sector correlations to 30-day rolling average.

        When a sector BREAKS its historical correlation with SPY, flag it.
        Example: defense up while SPY down.
        """
        findings: list[dict[str, Any]] = []

        sector_data = market_data.get("sectors", {})
        spy_return = market_data.get("spy_return")

        if not sector_data or spy_return is None:
            return findings

        for sector, info in sector_data.items():
            current_return = info.get("return")
            if current_return is None:
                continue

            # Update rolling correlation tracker
            history = self.sector_correlations.setdefault(sector, [])
            if spy_return != 0:
                implied_corr = current_return / spy_return if abs(spy_return) > 1e-9 else 0.0
            else:
                implied_corr = 0.0
            history.append(implied_corr)
            # Keep 30 observations (approximating 30 trading days)
            self.sector_correlations[sector] = history[-30:]

            if len(history) < 5:
                continue

            avg_corr = sum(history) / len(history)
            if abs(avg_corr) < 1e-9:
                continue

            divergence_pct = ((implied_corr - avg_corr) / abs(avg_corr)) * 100

            # Flag meaningful divergences (> 50% deviation from rolling average)
            if abs(divergence_pct) > 50:
                direction = "up" if current_return > 0 else "down"
                spy_dir = "up" if spy_return > 0 else "down"
                if (current_return > 0) != (spy_return > 0):
                    action = f"{direction} while SPY {spy_dir}"
                else:
                    action = f"amplified move ({direction})"

                findings.append({
                    "sector": sector,
                    "expected_correlation": round(avg_corr, 3),
                    "actual": round(implied_corr, 3),
                    "divergence_pct": round(divergence_pct, 1),
                    "action": action,
                })

        return findings

    def _detect_cascades(
        self,
        market_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Track cascade timing between primary commodity moves and secondaries.

        If oil up 35% this week but MOS flat → 'pending cascade, 3-5 day
        historical lag, WATCH'.
        """
        findings: list[dict[str, Any]] = []

        commodity_moves = market_data.get("commodity_moves", {})
        stock_moves = market_data.get("stock_moves", {})

        if not commodity_moves:
            return findings

        for cascade in KNOWN_CASCADES:
            primary = cascade["primary"]
            primary_info = commodity_moves.get(primary)
            if not primary_info:
                continue

            primary_pct = primary_info.get("pct_change", 0)
            # Only care about meaningful moves (> 2% weekly)
            if abs(primary_pct) < 2.0:
                continue

            primary_move = f"{primary} {'+'if primary_pct > 0 else ''}{primary_pct:.0f}%"
            days_since = primary_info.get("days_since_move", 0)
            lag_min = cascade["lag_days_min"]
            lag_max = cascade["lag_days_max"]
            expected_lag = f"{lag_min}-{lag_max}"

            for symbol in cascade["secondary_symbols"]:
                sec_info = stock_moves.get(symbol, {})
                sec_pct = sec_info.get("pct_change", 0)

                tracking_key = f"{primary}_{symbol}"

                # Determine cascade status
                if days_since < lag_min and abs(sec_pct) < abs(primary_pct) * 0.3:
                    status = "pending"
                    action = f"WATCH — historical {expected_lag}d lag"
                elif lag_min <= days_since <= lag_max:
                    if abs(sec_pct) < abs(primary_pct) * 0.3:
                        status = "in_progress"
                        action = f"IN WINDOW — expect move within {lag_max - days_since}d"
                    else:
                        status = "completed"
                        action = "cascade played out"
                elif days_since > lag_max:
                    if abs(sec_pct) < abs(primary_pct) * 0.3:
                        status = "in_progress"
                        action = "OVERDUE — cascade delayed, still watching"
                    else:
                        status = "completed"
                        action = "cascade played out (late)"
                else:
                    continue

                self.cascade_tracking[tracking_key] = {
                    "status": status,
                    "primary_pct": primary_pct,
                    "secondary_pct": sec_pct,
                    "days_since": days_since,
                    "updated": time.time(),
                }

                findings.append({
                    "primary_move": primary_move,
                    "secondary_symbol": symbol,
                    "expected_lag_days": expected_lag,
                    "days_elapsed": days_since,
                    "status": status,
                    "action": action,
                })

        return findings

    def _detect_regime_transitions(
        self,
        scorecard: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Track regime probability approaching thresholds (0.3, 0.5, 0.7).

        As the probability nears a threshold, the market often re-prices
        quickly — this is where edge lives.
        """
        findings: list[dict[str, Any]] = []

        regime_probs = scorecard.get("regime_probabilities", {})
        if not regime_probs:
            return findings

        for regime, current_prob in regime_probs.items():
            if not isinstance(current_prob, (int, float)):
                continue

            for threshold in _REGIME_THRESHOLDS:
                distance = abs(current_prob - threshold)
                # Flag when within 5 percentage points of a threshold
                if distance < 0.05:
                    direction = "approaching_from_below" if current_prob < threshold else "approaching_from_above"
                    findings.append({
                        "regime": regime,
                        "current_prob": round(current_prob, 4),
                        "nearest_threshold": threshold,
                        "distance": round(distance, 4),
                        "direction": direction,
                    })

        return findings

    def _detect_smart_money(
        self,
        bridge_results: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Check options flow for unusual activity and political disclosures.

        Looks for large block trades, unusual OI spikes, and congressional /
        insider filing signals from the relevant bridges.
        """
        findings: list[dict[str, Any]] = []

        # Options flow
        options_flow = bridge_results.get("options_flow", [])
        for flow in options_flow:
            premium = flow.get("premium", 0)
            symbol = flow.get("symbol", "")
            # Flag large premium trades (> $500K)
            if premium >= 500_000 and symbol:
                findings.append({
                    "symbol": symbol,
                    "source": "options_flow",
                    "signal_type": f"large block ${premium:,.0f}",
                    "action": "unusual options activity — investigate",
                })

            # Unusual OI spike
            oi_change_pct = flow.get("oi_change_pct", 0)
            if oi_change_pct > 100 and symbol:
                findings.append({
                    "symbol": symbol,
                    "source": "options_flow",
                    "signal_type": f"OI spike +{oi_change_pct:.0f}%",
                    "action": "open interest surge — positioning detected",
                })

        # Political / insider disclosures
        disclosures = bridge_results.get("political_disclosures", [])
        for disc in disclosures:
            symbol = disc.get("symbol", "")
            filer = disc.get("filer", "unknown")
            tx_type = disc.get("type", "purchase")
            if symbol:
                findings.append({
                    "symbol": symbol,
                    "source": "political_disclosure",
                    "signal_type": f"{filer} {tx_type}",
                    "action": f"congressional {tx_type} — track for follow-through",
                })

        # Insider filings (Form 4, etc.)
        insider_filings = bridge_results.get("insider_filings", [])
        for filing in insider_filings:
            symbol = filing.get("symbol", "")
            insider = filing.get("name", "unknown")
            tx_type = filing.get("type", "purchase")
            value = filing.get("value", 0)
            if symbol and value >= 100_000:
                findings.append({
                    "symbol": symbol,
                    "source": "insider_filing",
                    "signal_type": f"{insider} {tx_type} ${value:,.0f}",
                    "action": f"insider {tx_type} — significant size",
                })

        return findings
