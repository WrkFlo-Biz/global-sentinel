#!/usr/bin/env python3
"""
Global Sentinel V5.1 — Regime Shift Scorer

Computes a composite regime shift probability from real bridge data:
- Aviation/travel disruptions (from aviation_disruption_bridge)
- Market microstructure stress (from market_microstructure_bridge)
- Finnhub headline pressure (from finnhub_bridge)
- FRED macro signals (from fred_bridge)
- GDELT event tone/intensity (from gdelt_bridge)
- Data freshness penalties

Weights are loaded from config/thresholds.yaml -> regime_weights.
Confidence adjusted for stale, conflicting, or fallback data.
"""

from __future__ import annotations

from typing import Any, Dict, List


class RegimeShiftScorer:
    """Weighted composite scorer for geopolitical regime shift probability."""

    def __init__(self, config: dict):
        self.weights = config.get("regime_weights", {})
        self.confidence_cfg = config.get("confidence", {})
        self.freshness_cfg = config.get("freshness", {})

    def score(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Compute regime shift score from snapshot bridge data.

        Returns dict with regime_shift_probability, component_scores,
        confidence, evidence, freshness, fallback_mode.
        """
        components: Dict[str, float] = {}
        evidence: List[str] = []
        freshness: Dict[str, bool] = {}
        stale_count = 0

        # --- Geopolitical Tension (aviation disruptions + GDELT) ---
        geo = self._score_geopolitical_tension(snapshot)
        components["geopolitical_tension"] = geo["score"]
        evidence.extend(geo.get("evidence", []))
        freshness["geopolitical_tension"] = geo.get("fresh", False)
        if not geo.get("fresh", False):
            stale_count += 1

        # --- Market Volatility (microstructure realized vol) ---
        vol = self._score_market_volatility(snapshot)
        components["market_volatility"] = vol["score"]
        evidence.extend(vol.get("evidence", []))
        freshness["market_volatility"] = vol.get("fresh", False)
        if not vol.get("fresh", False):
            stale_count += 1

        # --- Currency Stress (placeholder — needs FX bridge) ---
        fx = self._score_currency_stress(snapshot)
        components["currency_stress"] = fx["score"]
        freshness["currency_stress"] = fx.get("fresh", False)
        if not fx.get("fresh", False):
            stale_count += 1

        # --- Commodity Shock (placeholder — needs commodity bridge) ---
        comm = self._score_commodity_shock(snapshot)
        components["commodity_shock"] = comm["score"]
        freshness["commodity_shock"] = comm.get("fresh", False)
        if not comm.get("fresh", False):
            stale_count += 1

        # --- Policy Uncertainty (Finnhub headline pressure) ---
        pol = self._score_policy_uncertainty(snapshot)
        components["policy_uncertainty"] = pol["score"]
        evidence.extend(pol.get("evidence", []))
        freshness["policy_uncertainty"] = pol.get("fresh", False)

        # --- Labor Disruption (FRED labor series) ---
        labor = self._score_labor_disruption(snapshot)
        components["labor_disruption"] = labor["score"]
        freshness["labor_disruption"] = labor.get("fresh", False)

        # --- Credit Spread (microstructure-derived proxy) ---
        credit = self._score_credit_spread(snapshot)
        components["credit_spread"] = credit["score"]
        freshness["credit_spread"] = credit.get("fresh", False)

        # --- Liquidity Stress (microstructure spread/vol proxy) ---
        liq = self._score_liquidity_stress(snapshot)
        components["liquidity_stress"] = liq["score"]
        freshness["liquidity_stress"] = liq.get("fresh", False)

        # --- Volatility regime multiplier ---
        # When avg vol crosses thresholds, amplify the composite score
        vol_score = components.get("market_volatility", 0)
        vol_multiplier = 1.0
        if vol_score >= 0.9:       # crisis-level vol (>4% avg)
            vol_multiplier = 1.4
        elif vol_score >= 0.6:     # elevated vol (>2.5% avg)
            vol_multiplier = 1.2
        elif vol_score >= 0.35:    # normal-high vol (>1.5% avg)
            vol_multiplier = 1.1

        # --- Composite weighted sum ---
        regime_prob = sum(
            components.get(k, 0) * self.weights.get(k, 0)
            for k in self.weights
        )
        regime_prob *= vol_multiplier
        regime_prob = max(0.0, min(1.0, regime_prob))

        # --- Confidence ---
        confidence = self.confidence_cfg.get("base_confidence", 0.8)
        confidence -= stale_count * self.confidence_cfg.get("stale_data_penalty", 0.15)

        fresh_count = sum(1 for v in freshness.values() if v)
        fallback_mode = snapshot.get("fallback_mode", False)
        if fresh_count < 3 or fallback_mode:
            fallback_mode = True
            confidence -= self.confidence_cfg.get("fallback_mode_penalty", 0.2)

        # Penalize if fewer than quorum sources are fresh
        quorum = self.freshness_cfg.get("quorum_required_for_escalation", 3)
        if fresh_count < quorum:
            confidence -= 0.10

        confidence = max(0.0, min(1.0, confidence))

        return {
            "regime_shift_probability": round(regime_prob, 4),
            "component_scores": {k: round(v, 4) for k, v in components.items()},
            "confidence": round(confidence, 4),
            "evidence": evidence[:10],
            "freshness": freshness,
            "fallback_mode": fallback_mode,
        }

    # --- Signal Scoring Methods ---

    def _score_geopolitical_tension(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Score from aviation disruptions + GDELT events."""
        disruptions = snapshot.get("aviation_disruptions", [])
        gdelt_events = snapshot.get("gdelt_events", [])
        evidence = []

        score = 0.0

        # Aviation disruptions: severity-weighted
        if disruptions:
            high = sum(1 for d in disruptions if d.get("severity") == "high")
            medium = sum(1 for d in disruptions if d.get("severity") == "medium")
            low = sum(1 for d in disruptions if d.get("severity") == "low")

            # Each high disruption adds significant signal
            score += high * 0.15
            score += medium * 0.06
            score += low * 0.02

            for d in disruptions[:3]:
                evidence.append(d.get("title", "disruption event"))

        # GDELT events: tone-weighted
        if gdelt_events:
            negative_tone_sum = 0.0
            event_count = 0
            for evt in gdelt_events:
                tone = evt.get("avg_tone", 0.0)
                if tone < -3.0:  # Significantly negative tone
                    negative_tone_sum += abs(tone)
                    event_count += 1
                    if len(evidence) < 5:
                        evidence.append(evt.get("title", "GDELT negative event"))

            if event_count > 0:
                # Normalize: -10 tone is very negative
                avg_negative = negative_tone_sum / event_count
                score += min(avg_negative / 10.0, 0.5) * (min(event_count, 20) / 20.0)

        score = min(score, 1.0)
        fresh = bool(disruptions or gdelt_events)

        return {"score": round(score, 4), "fresh": fresh, "evidence": evidence}

    def _score_market_volatility(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Score from realized vol across watchlist symbols."""
        micro = snapshot.get("market_microstructure", {})
        evidence = []

        if not micro:
            return {"score": 0.2, "fresh": False, "evidence": ["no microstructure data"]}

        sigmas = []
        for sym, data in micro.items():
            sigma = data.get("sigma_daily_pct", 0.0)
            if sigma and sigma > 0:
                sigmas.append((sym, sigma))

        if not sigmas:
            return {"score": 0.2, "fresh": False, "evidence": ["no sigma data"]}

        avg_sigma = sum(s for _, s in sigmas) / len(sigmas)
        max_sigma_sym, max_sigma = max(sigmas, key=lambda x: x[1])

        # Scoring: normal daily vol ~1-2%, elevated ~2-4%, crisis >4%
        if avg_sigma > 4.0:
            score = 0.9
            evidence.append(f"avg realized vol {avg_sigma:.2f}% (crisis-level)")
        elif avg_sigma > 2.5:
            score = 0.6
            evidence.append(f"avg realized vol {avg_sigma:.2f}% (elevated)")
        elif avg_sigma > 1.5:
            score = 0.35
            evidence.append(f"avg realized vol {avg_sigma:.2f}% (normal-high)")
        else:
            score = 0.15
            evidence.append(f"avg realized vol {avg_sigma:.2f}% (calm)")

        # Spike detection: any single symbol >5% daily vol
        if max_sigma > 5.0:
            score = min(score + 0.15, 1.0)
            evidence.append(f"{max_sigma_sym} vol spike: {max_sigma:.2f}%")

        return {"score": round(score, 4), "fresh": True, "evidence": evidence}

    def _score_currency_stress(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Score from DXY/FX moves. Uses microstructure data for currency ETFs if available."""
        micro = snapshot.get("market_microstructure", {})
        # Look for currency-related symbols (UUP=dollar bull, FXE=euro, FXY=yen)
        fx_symbols = {"UUP", "FXE", "FXY", "EEM"}
        found = {s: micro[s] for s in fx_symbols if s in micro}

        if not found:
            return {"score": 0.15, "fresh": False}

        # High vol in currency ETFs suggests FX stress
        avg_sigma = sum(d.get("sigma_daily_pct", 0) for d in found.values()) / len(found)
        score = min(avg_sigma / 3.0, 1.0)  # 3% daily vol = max stress
        return {"score": round(score, 4), "fresh": True}

    def _score_commodity_shock(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Score from commodity-related symbols in microstructure."""
        micro = snapshot.get("market_microstructure", {})
        commodity_symbols = {"USO", "GLD", "SLV", "COPX", "XLE", "XOP"}
        found = {s: micro[s] for s in commodity_symbols if s in micro}

        if not found:
            return {"score": 0.15, "fresh": False}

        avg_sigma = sum(d.get("sigma_daily_pct", 0) for d in found.values()) / len(found)
        score = min(avg_sigma / 4.0, 1.0)  # 4% daily vol = max commodity shock
        return {"score": round(score, 4), "fresh": True}

    def _score_policy_uncertainty(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Score from Finnhub headline pressure."""
        finnhub_packets = snapshot.get("finnhub", [])
        evidence = []

        if not finnhub_packets:
            return {"score": 0.15, "fresh": False, "evidence": []}

        # Aggregate headline pressure scores from all packets
        pressure_scores = []
        rate_regime_count = 0
        for pkt in finnhub_packets:
            meta = pkt.get("parsing_meta", {})
            pressure = meta.get("headline_pressure_score", 0.0)
            if pressure and pressure > 0:
                pressure_scores.append(pressure)
            if pkt.get("rate_regime_shock_candidate"):
                rate_regime_count += 1
            # Extract top headlines for evidence
            for h in (meta.get("top_headlines_preview") or [])[:2]:
                if h and len(evidence) < 5:
                    evidence.append(h)

        if not pressure_scores:
            return {"score": 0.15, "fresh": True, "evidence": evidence}

        avg_pressure = sum(pressure_scores) / len(pressure_scores)
        score = min(avg_pressure * 1.5, 1.0)  # Scale up slightly

        # Boost if rate regime candidates detected
        if rate_regime_count > 0:
            score = min(score + 0.1 * min(rate_regime_count, 3), 1.0)

        return {"score": round(score, 4), "fresh": True, "evidence": evidence}

    def _score_labor_disruption(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Score from FRED labor series."""
        fred_packets = snapshot.get("fred", [])

        if not fred_packets:
            return {"score": 0.1, "fresh": False}

        # Look for labor-related FRED series with significant deltas
        labor_signal = 0.0
        for pkt in fred_packets:
            meta = pkt.get("parsing_meta", {})
            category = meta.get("series_category", "")
            if category not in ("labor",):
                sid = meta.get("series_id", "")
                if sid not in ("PAYEMS", "UNRATE", "ICSA"):
                    continue

            delta = meta.get("delta")
            if delta is not None:
                # Large delta in labor = disruption signal
                labor_signal += min(abs(delta) / 200.0, 0.3)

        score = min(labor_signal, 1.0) if labor_signal > 0 else 0.1
        return {"score": round(score, 4), "fresh": bool(fred_packets)}

    def _score_credit_spread(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Score from HYG/LQD vol as credit spread proxy."""
        micro = snapshot.get("market_microstructure", {})
        credit_symbols = {"HYG", "LQD", "JNK"}
        found = {s: micro[s] for s in credit_symbols if s in micro}

        if not found:
            return {"score": 0.1, "fresh": False}

        avg_sigma = sum(d.get("sigma_daily_pct", 0) for d in found.values()) / len(found)
        # Credit ETF vol >1.5% is elevated, >3% is crisis
        score = min(avg_sigma / 3.0, 1.0)
        return {"score": round(score, 4), "fresh": True}

    def _score_liquidity_stress(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Score from ADV changes and vol spikes across the board."""
        micro = snapshot.get("market_microstructure", {})

        if not micro:
            return {"score": 0.1, "fresh": False}

        # Count symbols with very high vol (liquidity stress indicator)
        high_vol_count = 0
        total = 0
        for sym, data in micro.items():
            sigma = data.get("sigma_daily_pct", 0.0)
            if sigma > 0:
                total += 1
                if sigma > 3.0:
                    high_vol_count += 1

        if total == 0:
            return {"score": 0.1, "fresh": False}

        # % of symbols with high vol
        stress_ratio = high_vol_count / total
        score = min(stress_ratio * 2.0, 1.0)  # 50% high-vol symbols = max stress
        return {"score": round(score, 4), "fresh": True}
