#!/usr/bin/env python3
"""
Global Sentinel V5.2 — GSS Master Execution Engine

Implements the three-layer decision matrix:

1. The Field Layer (Leading): GCP consciousness Z-scores
   - RNG coherence precedes narrative events by hours/days
   - Statistical anomalies in global random number generators

2. The Narrative Layer (Coinciding): News velocity + sentiment
   - Infection rate of stories across global media
   - GDELT article counts, Finnhub sentiment aggregation

3. The Execution Layer (Lagging): Greeks, VIX, market data
   - Options gamma exposure, put/call ratios
   - Realized vol, open interest concentration

Decision signals:
- BLACK_SWAN_SHIELD: High Z + High velocity -> Buy protective puts
- GAMMA_SQUEEZE: High gamma + High narrative -> Buy calls on squeeze targets
- NOISE_FILTER: High narrative + Low Z -> Fade the hype (short)
- PRE_PULSE: Low narrative + High Z -> Accumulate before the event
- NEUTRAL: Baseline activity

Safety: ALL output is advisory-only. No auto-execution without human approval.
Alpaca paper trading does NOT support options — options recs are manual-only.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    import yaml
except ImportError:
    yaml = None


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Signal types
# ---------------------------------------------------------------------------
SIGNAL_BLACK_SWAN = "BLACK_SWAN_SHIELD"
SIGNAL_GAMMA_SQUEEZE = "GAMMA_SQUEEZE"
SIGNAL_NOISE_FILTER = "NOISE_FILTER"
SIGNAL_PRE_PULSE = "PRE_PULSE"
SIGNAL_NEUTRAL = "NEUTRAL"
SIGNAL_EMERGENCY_DELEVERAGE = "EMERGENCY_DELEVERAGE"

ACTION_MAP = {
    SIGNAL_BLACK_SWAN: "BUY_PUTS",
    SIGNAL_GAMMA_SQUEEZE: "BUY_CALLS",
    SIGNAL_NOISE_FILTER: "FADE_HYPE",
    SIGNAL_PRE_PULSE: "ACCUMULATE",
    SIGNAL_NEUTRAL: "HOLD",
    SIGNAL_EMERGENCY_DELEVERAGE: "DELEVERAGE",
}

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
Z_THRESHOLD_BLACK_SWAN = 2.5
Z_THRESHOLD_PRE_PULSE = 2.0
NARRATIVE_VEL_HIGH = 1.2
NARRATIVE_VEL_EXTREME = 2.0
NARRATIVE_VEL_LOW = 0.5
VIX_ELEVATED = 25.0
VIX_CRISIS = 35.0
MARGIN_WARNING = 0.75
MARGIN_CRITICAL = 0.85
MARGIN_LIQUIDATION = 0.90


class GSSExecutionEngine:
    """
    Global Sentinel System - Master Execution Engine

    Three-layer decision matrix:
    1. The Field Layer (Leading): GCP consciousness Z-scores
    2. The Narrative Layer (Coinciding): News velocity + sentiment
    3. The Execution Layer (Lagging): Greeks, VIX, market data

    Decision signals:
    - BLACK_SWAN_SHIELD: High Z + High velocity -> Buy protective puts
    - GAMMA_SQUEEZE: High gamma + High narrative -> Buy calls on squeeze targets
    - NOISE_FILTER: High narrative + Low Z -> Fade the hype (short)
    - PRE_PULSE: Low narrative + High Z -> Accumulate before the event
    - NEUTRAL: Baseline activity
    """

    def __init__(self, config: Optional[Union[Path, Dict[str, Any]]] = None):
        if isinstance(config, Path):
            self.config = self._load_config_from_repo(config)
        else:
            self.config = config or {}
        # Configurable overrides for thresholds
        thresholds = self.config.get("gss_thresholds", {})
        self.z_black_swan = thresholds.get("z_black_swan", Z_THRESHOLD_BLACK_SWAN)
        self.z_pre_pulse = thresholds.get("z_pre_pulse", Z_THRESHOLD_PRE_PULSE)
        self.narrative_high = thresholds.get("narrative_vel_high", NARRATIVE_VEL_HIGH)
        self.narrative_extreme = thresholds.get("narrative_vel_extreme", NARRATIVE_VEL_EXTREME)
        self.narrative_low = thresholds.get("narrative_vel_low", NARRATIVE_VEL_LOW)
        self.vix_elevated = thresholds.get("vix_elevated", VIX_ELEVATED)
        self.vix_crisis = thresholds.get("vix_crisis", VIX_CRISIS)

    @staticmethod
    def _load_config_from_repo(repo_root: Path) -> Dict[str, Any]:
        """Load config from repo_root/config/ YAML files."""
        config: Dict[str, Any] = {}
        if yaml is None:
            return config
        for name in ("thresholds.yaml", "execution_mode.yaml"):
            path = repo_root / "config" / name
            if path.exists():
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                config.update(loaded)
        return config

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def analyze(self, snapshot: Dict[str, Any], scorecard: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run the three-layer decision matrix against current bridge data.

        Args:
            snapshot: Full bridge snapshot dict (gcp_consciousness,
                      narrative_velocity, market_microstructure, etc.)
            scorecard: Current regime scorecard from RegimeShiftScorer

        Returns:
            GSS decision dict with signal, action, recommendations,
            and advisory_only flag.
        """
        # --- Extract field layer (GCP consciousness) ---
        gcp = snapshot.get("gcp_consciousness", {})
        z_score = float(gcp.get("max_z", 0))
        coherence_level = gcp.get("coherence_level", "random")
        regional_spikes = gcp.get("regional_spikes", [])

        # --- Extract narrative layer ---
        narrative = snapshot.get("narrative_velocity", {})
        narrative_vel = self._normalize_narrative_velocity(narrative)
        dominant_narrative = narrative.get("dominant_narrative", "none")

        # --- Extract execution layer ---
        micro = snapshot.get("market_microstructure", {})
        options = snapshot.get("options_greeks", {})
        gamma_exposure = options.get("net_gamma_exposure", 0.0)
        put_call_ratio = options.get("put_call_ratio", 1.0)
        open_interest_data = options.get("open_interest", {})
        vix = self._extract_vix(snapshot)

        # --- Extract politician alpha layer ---
        politician_alpha = snapshot.get("politician_alpha", {})
        pol_scores = politician_alpha.get("political_alpha_scores", {})
        pol_sentiment = politician_alpha.get("aggregate_sentiment", "neutral")
        pol_avg_score = (
            sum(pol_scores.values()) / len(pol_scores)
            if pol_scores else 0.0
        )

        # --- Extract portfolio/margin data ---
        portfolio = snapshot.get("portfolio", {})

        # --- Run decision matrix (priority order) ---
        # 1. Margin emergency overrides everything
        margin_status = self._margin_check(portfolio)
        gss_result = None

        if margin_status and margin_status.get("signal") == SIGNAL_EMERGENCY_DELEVERAGE:
            gss_result = self._build_result(
                signal=SIGNAL_EMERGENCY_DELEVERAGE,
                action="DELEVERAGE",
                reason=margin_status["reason"],
                confidence=0.99,
                z_score=z_score,
                coherence_level=coherence_level,
                regional_spikes=regional_spikes,
                narrative_vel=narrative_vel,
                dominant_narrative=dominant_narrative,
                vix=vix,
                gamma_exposure=gamma_exposure,
                put_call_ratio=put_call_ratio,
                hedge_recommendations=margin_status.get("recommendations", []),
                margin_status=margin_status,
            )

        # 2. Black Swan Shield (highest priority signal)
        if gss_result is None:
            result = self._detect_black_swan(z_score, narrative_vel, vix)
            if result:
                hedges = result.pop("hedge_recommendations", [])
                hedges += self.generate_hedge_recommendations(
                    SIGNAL_BLACK_SWAN, portfolio.get("positions", [])
                )
                gss_result = self._build_result(
                    signal=SIGNAL_BLACK_SWAN,
                    z_score=z_score,
                    coherence_level=coherence_level,
                    regional_spikes=regional_spikes,
                    narrative_vel=narrative_vel,
                    dominant_narrative=dominant_narrative,
                    vix=vix,
                    gamma_exposure=gamma_exposure,
                    put_call_ratio=put_call_ratio,
                    hedge_recommendations=hedges,
                    margin_status=margin_status,
                    **result,
                )

        # 3. Gamma Squeeze
        if gss_result is None:
            result = self._detect_gamma_squeeze(gamma_exposure, narrative_vel, open_interest_data)
            if result:
                hedges = result.pop("hedge_recommendations", [])
                hedges += self.generate_hedge_recommendations(
                    SIGNAL_GAMMA_SQUEEZE, portfolio.get("positions", [])
                )
                gss_result = self._build_result(
                    signal=SIGNAL_GAMMA_SQUEEZE,
                    z_score=z_score,
                    coherence_level=coherence_level,
                    regional_spikes=regional_spikes,
                    narrative_vel=narrative_vel,
                    dominant_narrative=dominant_narrative,
                    vix=vix,
                    gamma_exposure=gamma_exposure,
                    put_call_ratio=put_call_ratio,
                    hedge_recommendations=hedges,
                    margin_status=margin_status,
                    **result,
                )

        # 4. Noise Filter (high narrative, low field)
        if gss_result is None:
            result = self._detect_noise(narrative_vel, z_score, vix)
            if result:
                hedges = result.pop("hedge_recommendations", [])
                hedges += self.generate_hedge_recommendations(
                    SIGNAL_NOISE_FILTER, portfolio.get("positions", [])
                )
                gss_result = self._build_result(
                    signal=SIGNAL_NOISE_FILTER,
                    z_score=z_score,
                    coherence_level=coherence_level,
                    regional_spikes=regional_spikes,
                    narrative_vel=narrative_vel,
                    dominant_narrative=dominant_narrative,
                    vix=vix,
                    gamma_exposure=gamma_exposure,
                    put_call_ratio=put_call_ratio,
                    hedge_recommendations=hedges,
                    margin_status=margin_status,
                    **result,
                )

        # 5. Pre-Pulse (high field, low narrative)
        if gss_result is None:
            result = self._detect_pre_pulse(z_score, narrative_vel)
            if result:
                hedges = result.pop("hedge_recommendations", [])
                hedges += self.generate_hedge_recommendations(
                    SIGNAL_PRE_PULSE, portfolio.get("positions", [])
                )
                gss_result = self._build_result(
                    signal=SIGNAL_PRE_PULSE,
                    z_score=z_score,
                    coherence_level=coherence_level,
                    regional_spikes=regional_spikes,
                    narrative_vel=narrative_vel,
                    dominant_narrative=dominant_narrative,
                    vix=vix,
                    gamma_exposure=gamma_exposure,
                    put_call_ratio=put_call_ratio,
                    hedge_recommendations=hedges,
                    margin_status=margin_status,
                    **result,
                )

        # 6. Neutral — no actionable signal
        if gss_result is None:
            gss_result = self._build_result(
                signal=SIGNAL_NEUTRAL,
                action="HOLD",
                reason=(
                    f"No actionable signal. Field Z={z_score:.2f}, "
                    f"narrative_vel={narrative_vel:.2f}, VIX={vix:.1f}. "
                    "All layers within normal bounds."
                ),
                confidence=0.5,
                z_score=z_score,
                coherence_level=coherence_level,
                regional_spikes=regional_spikes,
                narrative_vel=narrative_vel,
                dominant_narrative=dominant_narrative,
                vix=vix,
                gamma_exposure=gamma_exposure,
                put_call_ratio=put_call_ratio,
                hedge_recommendations=[],
                margin_status=margin_status,
            )

        # --- Politician Alpha adjustments ---
        # Boost confidence if politicians are strongly bullish AND z_score is elevated
        if pol_avg_score > 3.0 and z_score > 2.0:
            original_conf = gss_result.get("confidence", 0)
            boosted = min(original_conf * 1.10, 0.99)
            gss_result["confidence"] = round(boosted, 3)
            gss_result["reason"] += (
                f" [POLITICAL_ALPHA_BOOST: Congressional insiders strongly bullish "
                f"(avg_score={pol_avg_score:.2f}) with elevated Z={z_score:.2f}, "
                f"confidence boosted +10%]"
            )

        # INSIDER_EXIT warning if politicians are selling heavily AND narrative is elevated
        if pol_avg_score < -2.0 and narrative_vel > 1.5:
            gss_result["reason"] += (
                f" [INSIDER_EXIT WARNING: Congressional insiders selling heavily "
                f"(avg_score={pol_avg_score:.2f}) with narrative_vel={narrative_vel:.2f}. "
                f"Smart money may be exiting ahead of adverse event.]"
            )

        # Attach politician_alpha data to result
        gss_result["politician_alpha"] = {
            "aggregate_sentiment": pol_sentiment,
            "avg_score": round(pol_avg_score, 3),
            "top_scores": dict(sorted(
                pol_scores.items(), key=lambda x: abs(x[1]), reverse=True
            )[:10]) if pol_scores else {},
            "total_symbols_tracked": len(pol_scores),
        }

        return gss_result

    # ------------------------------------------------------------------
    # Signal detectors
    # ------------------------------------------------------------------

    def _detect_black_swan(
        self, z_score: float, narrative_vel: float, vix: float
    ) -> Optional[Dict[str, Any]]:
        """
        BLACK_SWAN_SHIELD: Field coherence + narrative convergence.

        Triggers when z_score > 2.5 AND narrative_vel > 1.2.
        Both the consciousness field and news media are firing simultaneously,
        indicating a genuine systemic shock — not noise.
        """
        if z_score <= self.z_black_swan or narrative_vel <= self.narrative_high:
            return None

        # Confidence scales with how far above thresholds
        z_excess = (z_score - self.z_black_swan) / 2.0  # normalize 0-1 range
        vel_excess = (narrative_vel - self.narrative_high) / 3.0
        vix_factor = min(vix / self.vix_crisis, 1.0) * 0.2
        confidence = min(0.6 + z_excess * 0.2 + vel_excess * 0.15 + vix_factor, 0.98)

        severity = "EXTREME" if z_score > 3.5 else "HIGH"
        vix_note = f" VIX at {vix:.1f} confirms elevated fear." if vix > self.vix_elevated else ""

        recommendations = [
            {
                "instrument": "SPY puts",
                "action": "BUY",
                "spec": "2-4 weeks expiry, 5% OTM",
                "sizing": "2-3% of portfolio notional",
                "rationale": "Core protective hedge against broad equity selloff",
                "advisory_only": True,
                "options_note": "Requires manual execution on options-enabled account",
            },
            {
                "instrument": "QQQ puts",
                "action": "BUY",
                "spec": "2-4 weeks expiry, 5% OTM",
                "sizing": "1-2% of portfolio notional",
                "rationale": "Tech-heavy hedge — tech leads drawdowns",
                "advisory_only": True,
                "options_note": "Requires manual execution on options-enabled account",
            },
            {
                "instrument": "TLT calls",
                "action": "BUY",
                "spec": "1-2 months expiry, ATM",
                "sizing": "1% of portfolio notional",
                "rationale": "Flight to safety — long bonds rally in risk-off",
                "advisory_only": True,
                "options_note": "Requires manual execution on options-enabled account",
            },
            {
                "instrument": "Risky longs",
                "action": "LIQUIDATE",
                "spec": "Reduce high-beta equity to 50% of current size",
                "sizing": "Variable — proportional to position size",
                "rationale": "De-risk before potential crash",
                "advisory_only": True,
            },
            {
                "instrument": "Cash",
                "action": "INCREASE",
                "spec": "Target 30-40% cash allocation",
                "sizing": "From liquidated positions",
                "rationale": "Preserve capital for re-entry at lower levels",
                "advisory_only": True,
            },
        ]

        return {
            "action": "BUY_PUTS",
            "reason": (
                f"{severity} BLACK SWAN SHIELD: Consciousness field Z={z_score:.2f} "
                f"(>{self.z_black_swan}) with narrative velocity={narrative_vel:.2f} "
                f"(>{self.narrative_high}). Both leading and coinciding indicators "
                f"confirm systemic shock.{vix_note} Protective hedging recommended."
            ),
            "confidence": round(confidence, 3),
            "hedge_recommendations": recommendations,
        }

    def _detect_gamma_squeeze(
        self,
        gamma_exposure: float,
        narrative_vel: float,
        open_interest_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        GAMMA_SQUEEZE: Concentrated call open interest + high narrative velocity.

        Dealers short gamma must buy shares to hedge as price rises,
        creating a self-reinforcing squeeze.
        """
        if not open_interest_data and gamma_exposure == 0:
            return None

        # Identify squeeze candidates from open interest concentration
        squeeze_candidates = []
        for symbol, oi_data in open_interest_data.items():
            call_oi = float(oi_data.get("call_oi", 0))
            put_oi = float(oi_data.get("put_oi", 0))
            total_oi = call_oi + put_oi
            if total_oi == 0:
                continue
            call_ratio = call_oi / total_oi
            # High call/put ratio + high total OI = squeeze candidate
            if call_ratio > 0.7 and total_oi > 10000:
                squeeze_candidates.append({
                    "symbol": symbol,
                    "call_oi": call_oi,
                    "put_oi": put_oi,
                    "call_ratio": round(call_ratio, 3),
                    "total_oi": total_oi,
                })

        # Need concentrated call OI + narrative momentum
        has_concentration = len(squeeze_candidates) > 0 or abs(gamma_exposure) > 1e9
        has_narrative = narrative_vel > self.narrative_high

        if not (has_concentration and has_narrative):
            return None

        # Sort candidates by call ratio dominance
        squeeze_candidates.sort(key=lambda x: x["call_ratio"], reverse=True)
        top_candidates = squeeze_candidates[:5]

        confidence = min(
            0.5
            + (narrative_vel - self.narrative_high) * 0.1
            + len(squeeze_candidates) * 0.05,
            0.85,
        )

        recommendations = []
        for candidate in top_candidates:
            recommendations.append({
                "instrument": f"{candidate['symbol']} calls",
                "action": "BUY",
                "spec": "2-3 weeks expiry, slightly OTM",
                "sizing": "0.5-1% of portfolio per candidate",
                "rationale": (
                    f"Gamma squeeze candidate: {candidate['call_ratio']:.0%} call OI, "
                    f"{candidate['total_oi']:,.0f} total contracts"
                ),
                "advisory_only": True,
                "options_note": "Requires manual execution on options-enabled account",
            })

        if not recommendations:
            # Generic gamma squeeze play if we detected via aggregate gamma
            recommendations.append({
                "instrument": "High-gamma names (identify via OI scanner)",
                "action": "BUY_CALLS",
                "spec": "Near-dated, slightly OTM",
                "sizing": "0.5% of portfolio",
                "rationale": f"Net gamma exposure {gamma_exposure:,.0f} with high narrative velocity",
                "advisory_only": True,
                "options_note": "Requires manual execution on options-enabled account",
            })

        return {
            "action": "BUY_CALLS",
            "reason": (
                f"GAMMA SQUEEZE detected: {len(squeeze_candidates)} symbols with "
                f">70% call OI concentration. Narrative velocity={narrative_vel:.2f} "
                f"accelerating retail/institutional flow into calls. "
                f"Net gamma exposure: {gamma_exposure:,.0f}."
            ),
            "confidence": round(confidence, 3),
            "hedge_recommendations": recommendations,
        }

    def _detect_noise(
        self, narrative_vel: float, z_score: float, vix: float
    ) -> Optional[Dict[str, Any]]:
        """
        NOISE_FILTER: High narrative velocity WITHOUT field coherence.

        Triggers when narrative_vel > 2.0 AND z_score < 1.0.
        The news is screaming but the consciousness field is quiet — this
        pattern historically marks bear traps and fake panics. Fade the hype.
        """
        if narrative_vel <= self.narrative_extreme or z_score >= 1.0:
            return None

        # Higher confidence the more extreme the divergence
        vel_excess = (narrative_vel - self.narrative_extreme) / 3.0
        z_absence = (1.0 - z_score)  # how far below 1.0
        confidence = min(0.55 + vel_excess * 0.15 + z_absence * 0.1, 0.85)

        # VIX spike with no field coherence = classic overreaction
        vix_note = ""
        if vix > self.vix_elevated:
            confidence = min(confidence + 0.05, 0.90)
            vix_note = (
                f" VIX at {vix:.1f} suggests fear is elevated but field "
                "coherence does not confirm — likely temporary spike."
            )

        recommendations = [
            {
                "instrument": "SQQQ (3x inverse QQQ)",
                "action": "BUY (small)",
                "spec": "Day trade or 1-3 day hold",
                "sizing": "1% of portfolio",
                "rationale": "Fade tech panic if narrative overblown",
                "advisory_only": True,
            },
            {
                "instrument": "SH (inverse S&P 500)",
                "action": "BUY (small)",
                "spec": "Day trade or 1-3 day hold",
                "sizing": "1% of portfolio",
                "rationale": "Broad fade of market panic narrative",
                "advisory_only": True,
            },
            {
                "instrument": "SPY puts (short-dated)",
                "action": "SELL (if held)",
                "spec": "Close existing protective puts for profit",
                "sizing": "Reduce hedge size by 50%",
                "rationale": "If already hedged, take profit on overreaction",
                "advisory_only": True,
                "options_note": "Requires manual execution on options-enabled account",
            },
        ]

        return {
            "action": "FADE_HYPE",
            "reason": (
                f"NOISE FILTER: Narrative velocity={narrative_vel:.2f} "
                f"(>{self.narrative_extreme}) but consciousness field Z={z_score:.2f} "
                f"(<1.0) shows NO coherence. This divergence pattern historically "
                f"marks bear traps and media-driven fake panics. Fade the move.{vix_note}"
            ),
            "confidence": round(confidence, 3),
            "hedge_recommendations": recommendations,
        }

    def _detect_pre_pulse(
        self, z_score: float, narrative_vel: float
    ) -> Optional[Dict[str, Any]]:
        """
        PRE_PULSE: High field coherence WITHOUT narrative.

        Triggers when z_score > 2.0 AND narrative_vel < 0.5.
        The consciousness field is active but the news hasn't caught up.
        Historically, this precedes major events by hours to days.
        Early positioning window.
        """
        if z_score <= self.z_pre_pulse or narrative_vel >= self.narrative_low:
            return None

        z_excess = (z_score - self.z_pre_pulse) / 2.0
        vel_silence = (self.narrative_low - narrative_vel)
        confidence = min(0.45 + z_excess * 0.2 + vel_silence * 0.1, 0.80)

        severity = "STRONG" if z_score > 3.0 else "MODERATE"

        recommendations = [
            {
                "instrument": "VIX calls / UVXY shares",
                "action": "ACCUMULATE",
                "spec": "VIX calls 1-2 months out, ATM; or UVXY shares (small)",
                "sizing": "1-2% of portfolio",
                "rationale": "Vol is cheap before event — field says something is coming",
                "advisory_only": True,
                "options_note": "VIX calls require manual execution on options-enabled account",
            },
            {
                "instrument": "SPY puts (cheap, far-dated)",
                "action": "ACCUMULATE",
                "spec": "4-8 weeks expiry, 7-10% OTM (cheap tail hedge)",
                "sizing": "0.5-1% of portfolio",
                "rationale": "Cheap insurance while narrative is still quiet",
                "advisory_only": True,
                "options_note": "Requires manual execution on options-enabled account",
            },
            {
                "instrument": "GLD / TLT",
                "action": "ACCUMULATE",
                "spec": "Shares or near-ATM calls",
                "sizing": "2-3% of portfolio",
                "rationale": "Safe haven positioning before news catches up",
                "advisory_only": True,
            },
            {
                "instrument": "Cash",
                "action": "INCREASE",
                "spec": "Target 20-25% cash allocation",
                "sizing": "From trimming weakest positions",
                "rationale": "Preserve dry powder for when event materializes",
                "advisory_only": True,
            },
        ]

        return {
            "action": "ACCUMULATE",
            "reason": (
                f"{severity} PRE-PULSE: Consciousness field Z={z_score:.2f} "
                f"(>{self.z_pre_pulse}) but narrative velocity={narrative_vel:.2f} "
                f"(<{self.narrative_low}). The field is coherent before news has "
                f"caught up. Historical pattern: major event incoming within hours "
                f"to days. Accumulate hedges while they are cheap."
            ),
            "confidence": round(confidence, 3),
            "hedge_recommendations": recommendations,
        }

    # ------------------------------------------------------------------
    # Margin & risk management
    # ------------------------------------------------------------------

    def _margin_check(self, portfolio_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Check margin utilization and recommend de-leveraging if needed.

        Returns None if margin is healthy, otherwise returns a status dict
        with recommendations. At 85%+ margin, signals EMERGENCY_DELEVERAGE.
        """
        if not portfolio_data:
            return {"margin_usage": None, "status": "UNKNOWN", "reason": "No portfolio data"}

        equity = float(portfolio_data.get("equity", 0))
        margin_used = float(portfolio_data.get("margin_used", 0))
        buying_power = float(portfolio_data.get("buying_power", 0))

        if equity <= 0:
            return {"margin_usage": None, "status": "UNKNOWN", "reason": "No equity data"}

        margin_usage = margin_used / equity if equity > 0 else 0
        remaining_margin_pct = 1.0 - margin_usage

        result = {
            "margin_usage": round(margin_usage, 4),
            "remaining_margin_pct": round(remaining_margin_pct, 4),
            "equity": equity,
            "margin_used": margin_used,
            "buying_power": buying_power,
        }

        if margin_usage >= MARGIN_LIQUIDATION:
            result.update({
                "status": "LIQUIDATION_RISK",
                "signal": SIGNAL_EMERGENCY_DELEVERAGE,
                "reason": (
                    f"CRITICAL: Margin usage at {margin_usage:.1%} — only "
                    f"{remaining_margin_pct:.1%} remaining. Auto-liquidation imminent. "
                    f"Immediately close positions to reduce margin below 75%."
                ),
                "recommendations": self._liquidation_recommendations(portfolio_data),
            })
        elif margin_usage >= MARGIN_CRITICAL:
            result.update({
                "status": "CRITICAL",
                "signal": SIGNAL_EMERGENCY_DELEVERAGE,
                "reason": (
                    f"EMERGENCY: Margin usage at {margin_usage:.1%}. "
                    f"Close losing positions immediately. Target <75% margin usage."
                ),
                "recommendations": self._deleverage_recommendations(portfolio_data, target=0.70),
            })
        elif margin_usage >= MARGIN_WARNING:
            result.update({
                "status": "WARNING",
                "reason": (
                    f"Margin usage at {margin_usage:.1%} — approaching critical levels. "
                    f"Recommend reducing leverage. No new margin positions."
                ),
                "recommendations": [{
                    "instrument": "Portfolio",
                    "action": "REDUCE_LEVERAGE",
                    "spec": "No new margin positions; trim weakest holdings",
                    "sizing": "Reduce margin usage to <70%",
                    "rationale": "Proactive de-leveraging to maintain safety buffer",
                    "advisory_only": True,
                }],
            })
        else:
            result.update({
                "status": "HEALTHY",
                "reason": f"Margin usage at {margin_usage:.1%} — within safe limits.",
            })

        return result

    def _liquidation_recommendations(self, portfolio_data: Dict[str, Any]) -> List[Dict]:
        """Generate emergency liquidation recommendations at max margin."""
        positions = portfolio_data.get("positions", [])
        recs = []

        # Sort by unrealized P&L — liquidate biggest losers first
        sorted_positions = sorted(
            positions,
            key=lambda p: float(p.get("unrealized_pl", 0)),
        )

        for pos in sorted_positions[:5]:
            symbol = pos.get("symbol", "UNKNOWN")
            qty = pos.get("qty", 0)
            pl = float(pos.get("unrealized_pl", 0))
            recs.append({
                "instrument": symbol,
                "action": "LIQUIDATE",
                "spec": f"Close entire position ({qty} shares, P&L: ${pl:,.2f})",
                "sizing": "Full position",
                "rationale": "Emergency margin reduction — liquidate losers first",
                "advisory_only": True,
            })

        if not recs:
            recs.append({
                "instrument": "Portfolio",
                "action": "LIQUIDATE",
                "spec": "Close 50% of all positions by notional value",
                "sizing": "50% across the board",
                "rationale": "Emergency margin reduction — no position data available",
                "advisory_only": True,
            })

        return recs

    def _deleverage_recommendations(
        self, portfolio_data: Dict[str, Any], target: float = 0.70
    ) -> List[Dict]:
        """Generate de-leveraging recommendations to reach target margin usage."""
        positions = portfolio_data.get("positions", [])
        equity = float(portfolio_data.get("equity", 1))
        margin_used = float(portfolio_data.get("margin_used", 0))
        target_margin = equity * target
        excess = margin_used - target_margin

        recs = []
        if excess <= 0:
            return recs

        # Prioritize closing losers
        sorted_positions = sorted(
            positions,
            key=lambda p: float(p.get("unrealized_pl", 0)),
        )

        remaining_excess = excess
        for pos in sorted_positions:
            if remaining_excess <= 0:
                break
            symbol = pos.get("symbol", "UNKNOWN")
            market_value = abs(float(pos.get("market_value", 0)))
            if market_value <= 0:
                continue

            # Close enough of this position to cover the excess
            close_pct = min(remaining_excess / market_value, 1.0)
            remaining_excess -= market_value * close_pct

            recs.append({
                "instrument": symbol,
                "action": "REDUCE" if close_pct < 1.0 else "LIQUIDATE",
                "spec": f"Close {close_pct:.0%} of position (${market_value * close_pct:,.0f})",
                "sizing": f"{close_pct:.0%} of current position",
                "rationale": f"De-leverage to {target:.0%} margin target",
                "advisory_only": True,
            })

        return recs[:5]

    # ------------------------------------------------------------------
    # Hedge recommendation generator
    # ------------------------------------------------------------------

    def generate_hedge_recommendations(
        self, signal: str, portfolio_positions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Generate specific hedge trade recommendations based on signal type.

        Args:
            signal: The GSS signal (BLACK_SWAN_SHIELD, GAMMA_SQUEEZE, etc.)
            portfolio_positions: List of current portfolio positions

        Returns:
            List of hedge recommendation dicts
        """
        if signal == SIGNAL_BLACK_SWAN:
            return self._hedge_black_swan(portfolio_positions)
        elif signal == SIGNAL_GAMMA_SQUEEZE:
            return self._hedge_gamma_squeeze(portfolio_positions)
        elif signal == SIGNAL_NOISE_FILTER:
            return self._hedge_noise_filter(portfolio_positions)
        elif signal == SIGNAL_PRE_PULSE:
            return self._hedge_pre_pulse(portfolio_positions)
        return []

    def _hedge_black_swan(self, positions: List[Dict]) -> List[Dict]:
        """Protective puts on the largest equity positions + safe havens."""
        hedges = []

        # Protective puts on largest positions
        sorted_pos = sorted(
            positions,
            key=lambda p: abs(float(p.get("market_value", 0))),
            reverse=True,
        )

        for pos in sorted_pos[:5]:
            symbol = pos.get("symbol", "")
            market_value = abs(float(pos.get("market_value", 0)))
            side = pos.get("side", "long")
            if side != "long" or market_value < 1000:
                continue
            hedges.append({
                "instrument": f"{symbol} puts",
                "action": "BUY",
                "spec": "2-4 weeks expiry, 5% OTM",
                "sizing": f"Hedge ~${market_value:,.0f} notional",
                "rationale": f"Protective put on ${market_value:,.0f} long {symbol} position",
                "advisory_only": True,
                "options_note": "Requires manual execution on options-enabled account",
            })

        # Broad index protection
        hedges.append({
            "instrument": "SPY puts",
            "action": "BUY",
            "spec": "2-4 weeks expiry, 5% OTM",
            "sizing": "2-3% of portfolio notional",
            "rationale": "Broad market crash protection",
            "advisory_only": True,
            "options_note": "Requires manual execution on options-enabled account",
        })

        # Flight to safety
        hedges.append({
            "instrument": "TLT calls or shares",
            "action": "BUY",
            "spec": "Shares executable on Alpaca; calls require options account",
            "sizing": "3-5% of portfolio",
            "rationale": "Long bonds rally in risk-off",
            "advisory_only": True,
        })

        hedges.append({
            "instrument": "GLD shares",
            "action": "BUY",
            "spec": "Executable on Alpaca paper",
            "sizing": "2-3% of portfolio",
            "rationale": "Gold safe haven during systemic shock",
            "advisory_only": False,  # Shares can be traded on Alpaca paper
        })

        return hedges

    def _hedge_gamma_squeeze(self, positions: List[Dict]) -> List[Dict]:
        """Calls on identified squeeze candidates (all advisory)."""
        # Squeeze candidates come from the detector — these are generic templates
        return [
            {
                "instrument": "Squeeze candidate calls",
                "action": "BUY",
                "spec": "2-3 weeks expiry, slightly OTM",
                "sizing": "0.5-1% of portfolio per candidate",
                "rationale": "Ride the gamma squeeze momentum",
                "advisory_only": True,
                "options_note": "Requires manual execution on options-enabled account",
            },
            {
                "instrument": "Trailing stop on squeeze plays",
                "action": "SET",
                "spec": "15-20% trailing stop on calls",
                "sizing": "On all squeeze positions",
                "rationale": "Protect gains — squeezes reverse violently",
                "advisory_only": True,
                "options_note": "Requires manual execution on options-enabled account",
            },
        ]

    def _hedge_noise_filter(self, positions: List[Dict]) -> List[Dict]:
        """Inverse ETFs or short positions to fade the hype."""
        return [
            {
                "instrument": "SH (inverse S&P 500)",
                "action": "BUY",
                "spec": "Executable on Alpaca paper",
                "sizing": "1-2% of portfolio",
                "rationale": "Fade broad market panic — noise without substance",
                "advisory_only": False,
            },
            {
                "instrument": "SQQQ (3x inverse QQQ)",
                "action": "BUY (day trade only)",
                "spec": "Executable on Alpaca paper — CLOSE SAME DAY",
                "sizing": "0.5-1% of portfolio",
                "rationale": "Fade tech panic — leveraged, close by EOD",
                "advisory_only": False,
            },
            {
                "instrument": "Existing puts",
                "action": "TAKE PROFIT",
                "spec": "Close 50-75% of protective puts for profit",
                "sizing": "Reduce hedge overlay",
                "rationale": "If noise = the move is fake, hedge premium will decay",
                "advisory_only": True,
                "options_note": "Requires manual execution on options-enabled account",
            },
        ]

    def _hedge_pre_pulse(self, positions: List[Dict]) -> List[Dict]:
        """Accumulate cheap hedges before the event materializes."""
        return [
            {
                "instrument": "UVXY shares",
                "action": "ACCUMULATE",
                "spec": "Executable on Alpaca paper",
                "sizing": "1% of portfolio",
                "rationale": "Vol is cheap — field says event incoming",
                "advisory_only": False,
            },
            {
                "instrument": "GLD shares",
                "action": "ACCUMULATE",
                "spec": "Executable on Alpaca paper",
                "sizing": "2% of portfolio",
                "rationale": "Pre-position in safe haven before narrative catches up",
                "advisory_only": False,
            },
            {
                "instrument": "TLT shares",
                "action": "ACCUMULATE",
                "spec": "Executable on Alpaca paper",
                "sizing": "2% of portfolio",
                "rationale": "Long bonds as pre-event hedge",
                "advisory_only": False,
            },
            {
                "instrument": "SPY puts (cheap tail hedge)",
                "action": "BUY",
                "spec": "4-8 weeks, 7-10% OTM — very cheap",
                "sizing": "0.5% of portfolio",
                "rationale": "Lottery ticket hedge — massive payoff if event hits",
                "advisory_only": True,
                "options_note": "Requires manual execution on options-enabled account",
            },
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalize_narrative_velocity(self, narrative: Dict[str, Any]) -> float:
        """
        Normalize narrative velocity to a comparable scale.

        The narrative_velocity bridge produces velocity_score (0-100+) and
        infection_rate (0-50+). We normalize to a 0-5 scale where:
        - 0.0-0.5 = quiet (normal news flow)
        - 0.5-1.2 = moderate (elevated coverage)
        - 1.2-2.0 = high (significant event coverage)
        - 2.0+     = extreme (wall-to-wall coverage)

        If 'normalized_velocity' is present (e.g. from simulation data),
        use it directly.
        """
        # Allow pre-normalized value (used by simulator/tests)
        if "normalized_velocity" in narrative:
            return float(narrative["normalized_velocity"])

        velocity_score = float(narrative.get("velocity_score", 0))
        infection_rate = float(narrative.get("infection_rate", 0))

        # Scores < 10 are likely already on normalized 0-5 scale
        if velocity_score < 10:
            normalized = velocity_score
        else:
            # Raw bridge output: velocity_score of 20 = ~1.0 on our scale
            normalized = velocity_score / 20.0

        if infection_rate > 15:
            normalized += min((infection_rate - 15) / 50.0, 0.5)

        return round(max(0.0, normalized), 3)

    def _extract_vix(self, snapshot: Dict[str, Any]) -> float:
        """Extract VIX level from snapshot data."""
        # Check direct VIX field
        vix = snapshot.get("vix", 0)
        if vix:
            return float(vix)

        # Check market microstructure for VIX-related symbols
        micro = snapshot.get("market_microstructure", {})
        for sym in ("VIX", "^VIX", "VIXY", "UVXY"):
            if sym in micro:
                price = micro[sym].get("last_price") or micro[sym].get("close", 0)
                if price:
                    return float(price)

        # Estimate from realized vol if no direct VIX
        sigmas = []
        for sym, data in micro.items():
            sigma = data.get("sigma_daily_pct", 0)
            if sigma > 0:
                sigmas.append(sigma)

        if sigmas:
            # Rough proxy: annualized avg daily vol * sqrt(252)
            avg_sigma = sum(sigmas) / len(sigmas)
            return round(avg_sigma * math.sqrt(252), 1)

        return 0.0

    def _build_result(
        self,
        signal: str,
        action: str,
        reason: str,
        confidence: float,
        z_score: float,
        coherence_level: str,
        regional_spikes: List,
        narrative_vel: float,
        dominant_narrative: str,
        vix: float,
        gamma_exposure: float,
        put_call_ratio: float,
        hedge_recommendations: List[Dict],
        margin_status: Optional[Dict] = None,
        **_extra,
    ) -> Dict[str, Any]:
        """Build the standardized GSS output dict."""
        return {
            "timestamp_utc": iso_now(),
            "gss_signal": signal,
            "action": action,
            "reason": reason,
            "confidence": round(confidence, 3),
            "field_data": {
                "z_score": round(z_score, 3),
                "coherence_level": coherence_level,
                "regional_spikes": regional_spikes,
            },
            "narrative_data": {
                "velocity": round(narrative_vel, 3),
                "dominant_narrative": dominant_narrative,
            },
            "execution_data": {
                "vix": round(vix, 2),
                "gamma_exposure": gamma_exposure,
                "put_call_ratio": round(put_call_ratio, 3),
            },
            "hedge_recommendations": hedge_recommendations,
            "margin_status": margin_status or {},
            "advisory_only": True,  # Safety: no auto-execution without approval
            "alpaca_paper_note": (
                "Alpaca paper trading does NOT support options. "
                "All options recommendations require manual execution "
                "on a live options-enabled brokerage account. "
                "Share/ETF trades (advisory_only=False) can be executed on Alpaca paper."
            ),
        }
