#!/usr/bin/env python3
"""
Uncertainty Premium Detector
Based on Lagarde thesis: markets are overly optimistic about Iran conflict resolution.
Detects when market implied volatility (VIX) is too low relative to actual geopolitical risk,
creating a mispricing opportunity.

Key insight: When VIX is complacent but real risk is elevated, buying vol is the trade.
When VIX is panicking but peace is likely, selling vol is the trade.
"""
import json, os, datetime
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
OUTPUT_PATH = REPO_ROOT / "data/quantum_feed/uncertainty_premium.json"

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}

def compute_uncertainty_premium():
    """
    Compare market-implied uncertainty (VIX, options prices) vs actual geopolitical risk
    (war intensity, Hormuz status, peace probability) to find mispricings.
    """
    # Gather risk signals
    latest_signal = load_json(REPO_ROOT / "data/quantum_feed/latest_signal.json")
    polymarket = load_json(REPO_ROOT / "data/quantum_feed/polymarket_geopolitical.json")
    hmm_regime = load_json(REPO_ROOT / "data/quantum_feed/hmm_regime.json")
    session = load_json(REPO_ROOT / "data/quantum_feed/session_intelligence.json")

    # Extract war intensity (0-10 scale)
    war_intensity = 7.0  # Default elevated
    if latest_signal:
        buckets = latest_signal.get("bucket_scores", {})
        war_intensity = max(
            buckets.get("GEOPOLITICAL", 5),
            buckets.get("OIL_SUPPLY", 5),
            buckets.get("SHIPPING", 3),
        )

    # Extract peace probability from Polymarket
    peace_prob = 0.30  # Default
    if polymarket:
        peace_data = polymarket.get("peace_aggregate", {})
        peace_prob = peace_data.get("avg_probability", 0.30)

    # Extract VIX level (proxy for market-implied uncertainty)
    vix = 25.0  # Default
    try:
        import urllib.request
        env = {}
        env_path = REPO_ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k] = v
        key = env.get("ALPACA_API_KEY_LIVE", "")
        secret = env.get("ALPACA_SECRET_KEY_LIVE", "")
        if key and secret:
            url = "https://data.alpaca.markets/v2/stocks/quotes/latest?symbols=UVXY"
            req = urllib.request.Request(url)
            req.add_header("APCA-API-KEY-ID", key)
            req.add_header("APCA-API-SECRET-KEY", secret)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            uvxy = (data["quotes"]["UVXY"]["bp"] + data["quotes"]["UVXY"]["ap"]) / 2
            # UVXY ~50 corresponds to VIX ~25-27
            vix = uvxy * 0.52  # Rough approximation
    except Exception:
        pass

    # === CORE CALCULATION: Uncertainty Premium ===
    # Real risk score (0-10): combines war intensity, inverse peace probability, structural factors
    hormuz_disruption_factor = 0.92  # 92% tanker traffic reduction
    structural_duration_factor = 0.8  # Lagarde: "years not weeks"
    rate_hike_risk = 0.6  # ECB signaling hikes, Fed may follow

    real_risk = (
        war_intensity * 0.3 +
        (1 - peace_prob) * 10 * 0.2 +
        hormuz_disruption_factor * 10 * 0.2 +
        structural_duration_factor * 10 * 0.15 +
        rate_hike_risk * 10 * 0.15
    )

    # Market-implied risk (from VIX)
    # VIX 15 = low risk, VIX 25 = moderate, VIX 35 = high, VIX 50+ = panic
    market_implied_risk = min(10, (vix - 12) / 3.8)

    # Uncertainty premium = real risk - market implied risk
    # Positive = market is TOO COMPLACENT (buy vol / buy puts)
    # Negative = market is TOO FEARFUL (sell vol / buy calls)
    uncertainty_premium = real_risk - market_implied_risk

    # Generate trading signals based on premium
    signals = []

    if uncertainty_premium > 2.0:
        # Market is significantly underpricing risk
        signals.append({
            "signal": "BUY_VOL",
            "description": "Market is complacent — buy volatility (UVXY calls, SPY puts)",
            "strength": min(10, uncertainty_premium),
            "tickers": ["UVXY", "VXX", "SPY puts", "QQQ puts"],
            "rationale": "Lagarde thesis: markets overly optimistic, real risk much higher than VIX implies"
        })
        signals.append({
            "signal": "HEDGE_ENERGY",
            "description": "Oil disruption not fully priced — long energy hedges",
            "strength": min(10, uncertainty_premium * 0.8),
            "tickers": ["USO calls", "XLE calls", "OXY", "CVX"],
            "rationale": "Hormuz 92% disrupted but oil only at $92, should be $110+ if conflict persists"
        })
        signals.append({
            "signal": "SHORT_COMPLACENCY",
            "description": "Short assets that benefit from peace (overpriced on optimism)",
            "strength": min(10, uncertainty_premium * 0.6),
            "tickers": ["JETS puts", "CCL puts", "EEM puts"],
            "rationale": "Airlines, cruises, EM priced for quick peace — Lagarde says years"
        })
    elif uncertainty_premium < -2.0:
        # Market is significantly overpricing risk
        signals.append({
            "signal": "SELL_VOL",
            "description": "Market is panicking — sell volatility (short UVXY, buy SPY calls)",
            "strength": min(10, abs(uncertainty_premium)),
            "tickers": ["SVXY", "SPY calls", "QQQ calls"],
            "rationale": "VIX pricing in worse than reality — mean reversion likely"
        })
    else:
        signals.append({
            "signal": "NEUTRAL",
            "description": "Uncertainty fairly priced — no edge in vol trading",
            "strength": 0,
            "tickers": [],
            "rationale": "Market and reality roughly aligned"
        })

    result = {
        "timestamp": iso_now(),
        "real_risk_score": round(real_risk, 2),
        "market_implied_risk": round(market_implied_risk, 2),
        "uncertainty_premium": round(uncertainty_premium, 2),
        "premium_interpretation": "COMPLACENT" if uncertainty_premium > 1.5 else ("FEARFUL" if uncertainty_premium < -1.5 else "FAIR"),
        "components": {
            "war_intensity": round(war_intensity, 1),
            "peace_probability": round(peace_prob, 3),
            "hormuz_disruption": hormuz_disruption_factor,
            "vix_level": round(vix, 1),
            "structural_duration": "years (Lagarde ECB)",
            "rate_hike_risk": rate_hike_risk,
        },
        "signals": signals,
        "lagarde_thesis": "Markets are overly optimistic about Iran conflict resolution. Return to normality could take years. Structural energy costs, supply chain rewiring, persistent inflation not priced in.",
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2))
    return result

if __name__ == "__main__":
    result = compute_uncertainty_premium()
    print(json.dumps(result, indent=2))
