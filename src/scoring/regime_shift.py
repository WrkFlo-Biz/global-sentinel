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

        # --- Consciousness Coherence (GCP RNG + Narrative Velocity) ---
        cc = self._score_consciousness_coherence(snapshot)
        components["consciousness_coherence"] = cc["score"]
        evidence.extend(cc.get("evidence", []))
        freshness["consciousness_coherence"] = cc.get("fresh", False)
        if not cc.get("fresh", False):
            stale_count += 1

        # --- Politician Alpha (congressional whale trades) ---
        pol_alpha = self._score_politician_alpha(snapshot)
        components["politician_alpha"] = pol_alpha["score"]
        evidence.extend(pol_alpha.get("evidence", []))
        freshness["politician_alpha"] = pol_alpha.get("fresh", False)

        # --- Policy Signals (Fed Board + White House + Treasury OFAC + BLS) ---
        policy = self._score_policy_signals(snapshot)
        components["policy_signals"] = policy["score"]
        evidence.extend(policy.get("evidence", []))
        freshness["policy_signals"] = policy.get("fresh", False)

        # --- Yield Curve / Bond Market Signals ---
        yc = self._score_yield_curve(snapshot)
        components["yield_curve"] = yc["score"]
        evidence.extend(yc.get("evidence", []))
        freshness["yield_curve"] = yc.get("fresh", False)

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

    def _score_yield_curve(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Score from yield curve shape, credit spreads, and equity index data via FRED.

        Known series tracked:
        - Spreads: T10Y2Y, T10Y3M, T10YFF (inversion/steepening)
        - Curve points: DGS2, DGS5, DGS10, DGS30 (rapid moves)
        - Credit: BAMLH0A0HYM2 (HY OAS), BAMLC0A4CBBB (BBB OAS)
        - Risk: TEDRATE (may be discontinued), VIXCLS
        - Equities: SP500, NASDAQCOM, DJIA, WILLSMLCAP
        """
        fred_packets = snapshot.get("fred", [])
        evidence = []

        if not fred_packets:
            return {"score": 0.1, "fresh": False, "evidence": []}

        # Target series for yield curve scoring
        yield_curve_sids = {
            "T10Y2Y", "T10Y3M", "T10YFF",
            "DGS2", "DGS5", "DGS10", "DGS30",
            "BAMLH0A0HYM2", "BAMLC0A4CBBB",
            "TEDRATE", "VIXCLS",
            "SP500", "NASDAQCOM", "DJIA", "WILLSMLCAP",
        }

        score = 0.0
        found_any = False
        spread_values: Dict[str, float] = {}  # track spread levels for composite view

        for pkt in fred_packets:
            if not isinstance(pkt, dict):
                continue
            meta = pkt.get("parsing_meta", {})
            if not isinstance(meta, dict):
                continue
            sid = meta.get("series_id", "")
            if sid not in yield_curve_sids:
                continue

            latest = meta.get("latest_value")
            delta = meta.get("delta")
            category = meta.get("series_category", "")

            # Skip packets where FRED returned no valid observation
            if latest is None:
                continue

            found_any = True

            # --- Yield curve spread series ---
            if sid in ("T10Y2Y", "T10Y3M", "T10YFF"):
                spread_values[sid] = latest
                if latest < -0.5:
                    score += 0.35
                    evidence.append(f"{sid} deeply inverted at {latest:.2f}")
                elif latest < 0:
                    score += 0.20
                    evidence.append(f"{sid} inverted at {latest:.2f}")
                elif latest < 0.3:
                    score += 0.10  # Flat curve still concerning
                    evidence.append(f"{sid} near-flat at {latest:.2f}")
                else:
                    # Positive spread — normal, but still contributes baseline
                    score += 0.02

            # --- Rapid yield moves across the curve (2Y, 5Y, 10Y, 30Y) ---
            elif sid in ("DGS2", "DGS5", "DGS10", "DGS30"):
                if isinstance(delta, (int, float)) and abs(delta) > 0.15:
                    score += 0.10
                    direction = "spiking" if delta > 0 else "plunging"
                    evidence.append(f"{sid} yield {direction}: {delta:+.2f}%")
                elif isinstance(delta, (int, float)) and abs(delta) > 0.08:
                    score += 0.03  # moderate move

            # --- High yield credit spread widening ---
            elif sid in ("BAMLH0A0HYM2", "BAMLC0A4CBBB"):
                if isinstance(delta, (int, float)):
                    if delta > 0.50:
                        score += 0.25
                        evidence.append(f"{sid} credit spreads widening sharply: +{delta:.2f}")
                    elif delta > 0.20:
                        score += 0.10
                        evidence.append(f"{sid} credit spreads widening: +{delta:.2f}")
                    elif delta > 0.05:
                        score += 0.03
                # Also score absolute OAS level
                if isinstance(latest, (int, float)):
                    if latest > 6.0:
                        score += 0.15
                        evidence.append(f"{sid} OAS at distressed level: {latest:.2f}")
                    elif latest > 4.5:
                        score += 0.08

            # --- TED spread (interbank credit risk) — may be discontinued ---
            elif sid == "TEDRATE":
                if isinstance(latest, (int, float)):
                    if latest > 0.50:
                        score += 0.20
                        evidence.append(f"TED spread elevated at {latest:.2f}")
                    elif latest > 0.30:
                        score += 0.10

            # --- VIX level from FRED ---
            elif sid == "VIXCLS":
                if isinstance(latest, (int, float)):
                    if latest > 30:
                        score += 0.15
                        evidence.append(f"VIX elevated at {latest:.1f}")
                    elif latest > 20:
                        score += 0.05

            # --- Equity indices — large drops signal stress ---
            elif sid in ("SP500", "NASDAQCOM", "DJIA", "WILLSMLCAP"):
                if isinstance(delta, (int, float)) and (latest - delta) != 0:
                    pct_change = (delta / (latest - delta)) * 100
                    if pct_change < -3.0:
                        score += 0.20
                        evidence.append(f"{sid} dropped {pct_change:.1f}%")
                    elif pct_change < -2.0:
                        score += 0.12
                        evidence.append(f"{sid} declined {pct_change:.1f}%")
                    elif pct_change < -1.0:
                        score += 0.04

        if not found_any:
            return {"score": 0.1, "fresh": False, "evidence": []}

        # Baseline: if we found data but score is very low, set a floor
        # (calm markets with valid data should still register above zero)
        if score < 0.05 and found_any:
            score = 0.05

        return {"score": round(min(score, 1.0), 4), "fresh": True, "evidence": evidence[:5]}

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

    def _score_consciousness_coherence(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Score from GCP consciousness coherence + narrative velocity.

        Sentinel Logic Engine:
        - GCP high + narrative high  → amplify regime probability (systemic shock)
        - narrative high + GCP low   → discount signal (noise/bear trap)
        - GCP high + narrative low   → flag pre-pulse (early warning)
        """
        gcp = snapshot.get("gcp_consciousness", {})
        narrative = snapshot.get("narrative_velocity", {})
        evidence: List[str] = []

        max_z = float(gcp.get("max_z", 0))
        coherence_level = gcp.get("coherence_level", "random")
        regional_spikes = gcp.get("regional_spikes", [])
        velocity_score = float(narrative.get("velocity_score", 0))
        infection_rate = float(narrative.get("infection_rate", 0))

        gcp_fresh = gcp.get("fresh", False)
        narrative_fresh = narrative.get("fresh", False)
        fresh = gcp_fresh or narrative_fresh

        if not fresh:
            return {"score": 0.1, "fresh": False, "evidence": []}

        score = 0.0

        # Determine coherence flags
        gcp_high = coherence_level in ("high", "extreme") or abs(max_z) >= 2.5
        gcp_moderate = coherence_level == "moderate" or abs(max_z) >= 2.0
        narrative_high = velocity_score > 20 or infection_rate > 15

        # Sentinel Logic Engine scenarios
        if gcp_high and narrative_high:
            # Systemic shock: both layers firing → amplify
            score = min(0.5 + (abs(max_z) - 2.0) * 0.1 + velocity_score / 200.0, 1.0)
            evidence.append(
                f"CONSCIOUSNESS+NARRATIVE CONVERGENCE: GCP Z={max_z:.2f}, "
                f"velocity={velocity_score:.0f} — systemic shock signal"
            )
        elif narrative_high and not gcp_moderate:
            # Noise filter / bear trap: narrative panic without consciousness coherence
            score = max(0.05, velocity_score / 500.0)  # heavily discounted
            evidence.append(
                f"NOISE FILTER: narrative velocity={velocity_score:.0f} but "
                f"GCP Z={max_z:.2f} (incoherent) — likely bear trap"
            )
        elif gcp_high and not narrative_high:
            # Pre-pulse: consciousness coherent before news catches up
            score = min(0.3 + (abs(max_z) - 2.0) * 0.08, 0.7)
            evidence.append(
                f"PRE-PULSE: GCP Z={max_z:.2f} ({coherence_level}) with "
                f"low narrative velocity={velocity_score:.0f} — early warning"
            )
        elif gcp_moderate:
            # Moderate coherence — mild signal
            score = min(0.15 + (abs(max_z) - 1.5) * 0.05, 0.4)
            evidence.append(f"GCP moderate coherence: Z={max_z:.2f}")
        else:
            # Low/random coherence, low narrative — baseline
            score = 0.05

        # Regional spike bonus
        if regional_spikes:
            spike_bonus = min(len(regional_spikes) * 0.05, 0.15)
            score = min(score + spike_bonus, 1.0)
            for spike in regional_spikes[:2]:
                markets = ", ".join(spike.get("predicted_markets", [])[:3])
                evidence.append(
                    f"Regional consciousness spike: {spike.get('region')} "
                    f"Z={spike.get('z_score', 0):.2f} — watch {markets}"
                )

        # Add narrative evidence
        for ev in narrative.get("evidence", [])[:2]:
            evidence.append(ev)

        score = max(0.0, min(1.0, score))
        return {"score": round(score, 4), "fresh": fresh, "evidence": evidence}

    def _score_politician_alpha(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Score from congressional whale trading activity.
        High politician alpha = insiders are positioning = regime change likely."""
        pol = snapshot.get("politician_alpha", {})
        evidence: List[str] = []

        if not pol or not pol.get("fresh", False):
            return {"score": 0.0, "fresh": False}

        scores = pol.get("political_alpha_scores", {})
        whale_trades = pol.get("top_whale_trades", [])
        sentiment = pol.get("aggregate_sentiment", "neutral")
        total_trades = pol.get("total_trades_analyzed", 0)

        score = 0.0

        # High trade volume from politicians = they know something
        if total_trades >= 50:
            score += 0.3
            evidence.append(f"High congressional trading: {total_trades} trades analyzed")
        elif total_trades >= 20:
            score += 0.15

        # Aggregate sentiment divergence from market
        if sentiment in ("very_bullish", "very_bearish"):
            score += 0.2
            evidence.append(f"Congressional sentiment: {sentiment}")
        elif sentiment in ("bullish", "bearish"):
            score += 0.1

        # Top whale trades with high scores
        high_conviction = [w for w in whale_trades if w.get("score", 0) >= 7.0]
        if high_conviction:
            score += min(len(high_conviction) * 0.05, 0.3)
            top = high_conviction[0]
            evidence.append(
                f"Whale trade: {top.get('politician', '?')} "
                f"{top.get('transaction_type', '?')} {top.get('symbol', '?')} "
                f"(score: {top.get('score', 0):.0f})"
            )

        score = max(0.0, min(1.0, score))
        return {"score": round(score, 4), "fresh": True, "evidence": evidence}

    def _score_policy_signals(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Score from Fed Board, White House, Treasury OFAC, and BLS data.
        Policy shifts signal regime changes before markets price them."""
        evidence: List[str] = []
        score = 0.0
        any_fresh = False

        # Fed Board signals
        fed = snapshot.get("fed_board", {})
        if isinstance(fed, dict) and fed.get("fresh", False):
            any_fresh = True
            events = fed.get("events", fed.get("releases", []))
            if isinstance(events, list) and events:
                score += min(len(events) * 0.05, 0.2)
                evidence.append(f"Fed Board: {len(events)} recent policy signals")

        # White House policy
        wh = snapshot.get("whitehouse_policy", {})
        if isinstance(wh, dict) and wh.get("fresh", False):
            any_fresh = True
            actions = wh.get("executive_orders", wh.get("actions", wh.get("events", [])))
            if isinstance(actions, list) and actions:
                score += min(len(actions) * 0.05, 0.2)
                evidence.append(f"White House: {len(actions)} policy actions")

        # Treasury OFAC sanctions
        ofac = snapshot.get("treasury_ofac", {})
        if isinstance(ofac, dict) and ofac.get("fresh", False):
            any_fresh = True
            sanctions = ofac.get("new_designations", ofac.get("sanctions", ofac.get("entries", [])))
            if isinstance(sanctions, list) and sanctions:
                score += min(len(sanctions) * 0.08, 0.3)
                evidence.append(f"OFAC: {len(sanctions)} new sanctions designations")

        # BLS releases (jobs, CPI, etc.)
        bls = snapshot.get("bls_releases", {})
        if isinstance(bls, dict) and bls.get("fresh", False):
            any_fresh = True
            releases = bls.get("releases", bls.get("series", []))
            if isinstance(releases, (list, dict)):
                count = len(releases) if isinstance(releases, list) else len(releases.keys())
                if count:
                    score += min(count * 0.03, 0.15)
                    evidence.append(f"BLS: {count} economic data releases")

        score = max(0.0, min(1.0, score))
        return {"score": round(score, 4), "fresh": any_fresh, "evidence": evidence}
