"""End-of-day report generator: aggregates all system components into a single report."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class DailyReportGenerator:
    """Generate comprehensive end-of-day reports from all available system components.

    All components are optional — the report uses whatever is available.
    """

    DAILY_TARGET = 500.0  # dollars

    def __init__(self, repo_root: str | Path | None = None) -> None:
        if repo_root is not None:
            self.repo_root = Path(repo_root)
        else:
            self.repo_root = Path(__file__).resolve().parents[2]

    # ------------------------------------------------------------------
    # Component loaders — each returns a dict or None
    # ------------------------------------------------------------------

    def _load_pnl(self, date: str, components: dict | None) -> dict | None:
        """PnL by account, by strategy, combined, vs target."""
        if components and "pnl" in components:
            return components["pnl"]
        return None

    def _load_tca_summary(self, date: str, components: dict | None) -> dict | None:
        if components and "tca_summary" in components:
            return components["tca_summary"]
        return None

    def _load_reconciliation(self, date: str, components: dict | None) -> dict | None:
        if components and "reconciliation" in components:
            return components["reconciliation"]
        return None

    def _load_bridge_health(self, date: str, components: dict | None) -> dict | None:
        if components and "bridge_health" in components:
            return components["bridge_health"]
        return None

    def _load_regime_summary(self, date: str, components: dict | None) -> dict | None:
        if components and "regime_summary" in components:
            return components["regime_summary"]
        return None

    def _load_chokepoint_scores(self, date: str, components: dict | None) -> dict | None:
        if components and "chokepoint_scores" in components:
            return components["chokepoint_scores"]
        return None

    def _load_analog_matches(self, date: str, components: dict | None) -> list | None:
        if components and "analog_matches" in components:
            return components["analog_matches"]
        return None

    def _load_scanner_discoveries(self, date: str, components: dict | None) -> list | None:
        if components and "scanner_discoveries" in components:
            return components["scanner_discoveries"]
        return None

    def _load_strategy_scorecard(self, date: str, components: dict | None) -> dict | None:
        if components and "strategy_scorecard" in components:
            return components["strategy_scorecard"]
        return None

    def _load_compliance_results(self, date: str, components: dict | None) -> dict | None:
        if components and "compliance_results" in components:
            return components["compliance_results"]
        return None

    def _load_baseline_benchmark(self, date: str, components: dict | None) -> dict | None:
        if components and "baseline_benchmark" in components:
            return components["baseline_benchmark"]
        return None

    def _load_performance_attribution(self, date: str, components: dict | None) -> dict | None:
        if components and "performance_attribution" in components:
            return components["performance_attribution"]
        return None

    def _load_scenario_estimates(self, date: str, components: dict | None) -> dict | None:
        if components and "scenario_estimates" in components:
            return components["scenario_estimates"]
        return None

    def _load_edge_findings(self, date: str, components: dict | None) -> dict | None:
        if components and "edge_findings" in components:
            return components["edge_findings"]
        return None

    def _load_momentum_summary(self, date: str, components: dict | None) -> dict | None:
        if components and "momentum_summary" in components:
            return components["momentum_summary"]
        return None

    def _load_cross_asset_summary(self, date: str, components: dict | None) -> dict | None:
        if components and "cross_asset_summary" in components:
            return components["cross_asset_summary"]
        return None

    # ------------------------------------------------------------------
    # Main generate
    # ------------------------------------------------------------------

    def generate(
        self,
        date: str | None = None,
        components: dict | None = None,
    ) -> dict:
        """Generate full end-of-day report from all available components.

        Args:
            date: ISO date string (YYYY-MM-DD). Defaults to today UTC.
            components: Optional dict of pre-loaded component data keyed by name.

        Returns:
            Comprehensive report dict with all available sections.
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        report: dict = {
            "date": date,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Load each component — include only what's available
        loaders = {
            "pnl": self._load_pnl,
            "tca_summary": self._load_tca_summary,
            "reconciliation": self._load_reconciliation,
            "bridge_health": self._load_bridge_health,
            "regime_summary": self._load_regime_summary,
            "chokepoint_scores": self._load_chokepoint_scores,
            "analog_matches": self._load_analog_matches,
            "scanner_discoveries": self._load_scanner_discoveries,
            "strategy_scorecard": self._load_strategy_scorecard,
            "compliance_results": self._load_compliance_results,
            "baseline_benchmark": self._load_baseline_benchmark,
            "performance_attribution": self._load_performance_attribution,
            "scenario_estimates": self._load_scenario_estimates,
            "edge_findings": self._load_edge_findings,
            "momentum_summary": self._load_momentum_summary,
            "cross_asset_summary": self._load_cross_asset_summary,
        }

        for key, loader in loaders.items():
            try:
                result = loader(date, components)
                if result is not None:
                    report[key] = result
            except Exception:
                logger.exception("Failed to load component: %s", key)

        return report

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_report(
        self,
        report: dict,
        date: str | None = None,
        report_dir: str = "reports/operational",
    ) -> dict:
        """Save report as JSON and Markdown.

        Args:
            report: The report dict from generate().
            date: ISO date string for filename. Defaults to report['date'].
            report_dir: Directory relative to repo_root.

        Returns:
            Dict with json_path and md_path of saved files.
        """
        if date is None:
            date = report.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

        out_dir = self.repo_root / report_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        # JSON
        json_path = out_dir / f"daily_report_{date}.json"
        json_path.write_text(json.dumps(report, indent=2, default=str))
        logger.info("Saved JSON report: %s", json_path)

        # Markdown
        md_path = out_dir / f"daily_report_{date}.md"
        md_lines = self._render_markdown(report)
        md_path.write_text(md_lines)
        logger.info("Saved Markdown report: %s", md_path)

        return {"json_path": str(json_path), "md_path": str(md_path)}

    def _render_markdown(self, report: dict) -> str:
        """Render report dict as human-readable Markdown."""
        date = report.get("date", "unknown")
        lines = [f"# Daily Report — {date}", ""]

        if "pnl" in report:
            pnl = report["pnl"]
            combined = pnl.get("combined", 0.0)
            target_met = "MET" if combined >= self.DAILY_TARGET else "MISSED"
            lines.append(f"## PnL: ${combined:+,.0f} (target ${self.DAILY_TARGET:.0f} {target_met})")
            if "by_account" in pnl:
                for acct, val in pnl["by_account"].items():
                    lines.append(f"- {acct}: ${val:+,.0f}")
            if "by_strategy" in pnl:
                lines.append("")
                lines.append("### By Strategy")
                for strat, val in pnl["by_strategy"].items():
                    lines.append(f"- {strat}: ${val:+,.0f}")
            lines.append("")

        for section in [
            "tca_summary", "reconciliation", "bridge_health", "regime_summary",
            "chokepoint_scores", "strategy_scorecard", "compliance_results",
            "baseline_benchmark", "performance_attribution", "scenario_estimates",
            "edge_findings", "momentum_summary", "cross_asset_summary",
        ]:
            if section in report:
                title = section.replace("_", " ").title()
                lines.append(f"## {title}")
                data = report[section]
                if isinstance(data, dict):
                    for k, v in data.items():
                        lines.append(f"- **{k}**: {v}")
                elif isinstance(data, list):
                    for item in data:
                        lines.append(f"- {item}")
                else:
                    lines.append(str(data))
                lines.append("")

        if "analog_matches" in report:
            lines.append("## Analog Matches")
            for match in report["analog_matches"]:
                if isinstance(match, dict):
                    lines.append(f"- {match.get('description', match)}")
                else:
                    lines.append(f"- {match}")
            lines.append("")

        if "scanner_discoveries" in report:
            lines.append("## Scanner Discoveries")
            for disc in report["scanner_discoveries"]:
                if isinstance(disc, dict):
                    lines.append(f"- {disc.get('description', disc)}")
                else:
                    lines.append(f"- {disc}")
            lines.append("")

        lines.append(f"*Generated: {report.get('generated_at', 'unknown')}*")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------

    def format_telegram(self, report: dict) -> str:
        """Format report as a 10-line max Telegram summary."""
        date = report.get("date", "unknown")

        # Line 1: header
        lines = [f"Daily Report -- {date}"]

        # Line 2: PnL
        pnl_data = report.get("pnl", {})
        combined = pnl_data.get("combined", 0.0)
        target_icon = "target met" if combined >= self.DAILY_TARGET else "target missed"
        lines.append(f"PnL: ${combined:+,.0f} (${self.DAILY_TARGET:.0f} {target_icon})")

        # Line 3: best/worst strategy
        by_strategy = pnl_data.get("by_strategy", {})
        if by_strategy:
            best = max(by_strategy.items(), key=lambda x: x[1])
            worst = min(by_strategy.items(), key=lambda x: x[1])
            lines.append(f"Best: {best[0]} ${best[1]:+,.0f} | Worst: {worst[0]} ${worst[1]:+,.0f}")
        else:
            lines.append("Best/Worst: no strategy data")

        # Line 4: strategy scorecard
        scorecard = report.get("strategy_scorecard", {})
        active = scorecard.get("active_count", 0)
        total = scorecard.get("total_count", 0)
        profitable = scorecard.get("profitable_count", 0)
        if total > 0:
            lines.append(f"Strategies: {active}/{total} active, {profitable} profitable")
        else:
            lines.append("Strategies: no scorecard data")

        # Line 5: edge findings
        edge = report.get("edge_findings", {})
        leading = edge.get("leading_signals", 0)
        cascades = edge.get("pending_cascades", 0)
        lines.append(f"Edge: {leading} leading signals, {cascades} cascades pending")

        # Line 6: attribution
        attr = report.get("performance_attribution", {})
        if attr:
            total_pnl = attr.get("daily_pnl", combined) or 1.0
            a_pct = round(abs(attr.get("alpha", 0)) / abs(total_pnl) * 100) if total_pnl else 0
            b_pct = round(abs(attr.get("beta", 0)) / abs(total_pnl) * 100) if total_pnl else 0
            o_pct = round(abs(attr.get("oil_factor", 0)) / abs(total_pnl) * 100) if total_pnl else 0
            lines.append(f"Attribution: Alpha {a_pct}%, Beta {b_pct}%, Oil {o_pct}%")
        else:
            lines.append("Attribution: no data")

        # Line 7: exposure
        regime = report.get("regime_summary", {})
        gross = regime.get("gross_exposure", 0)
        net = regime.get("net_exposure", 0)
        eff_bets = regime.get("effective_bets", 0)
        lines.append(f"Exposure: G:{gross}% N:{net:+}% | Effective bets: {eff_bets}")

        # Line 8: bridges
        bridge = report.get("bridge_health", {})
        fresh = bridge.get("fresh", 0)
        total_bridges = bridge.get("total", 0)
        lines.append(f"Bridges: {fresh}/{total_bridges} fresh")

        # Line 9: alerts
        compliance = report.get("compliance_results", {})
        warnings = compliance.get("warnings", 0)
        critical = compliance.get("critical", 0)
        lines.append(f"Alerts: {warnings} warnings, {critical} critical")

        # Line 10: tomorrow
        tomorrow = report.get("edge_findings", {}).get("tomorrow_watch", "no outlook available")
        lines.append(f"Tomorrow: {tomorrow}")

        return "\n".join(lines[:10])
