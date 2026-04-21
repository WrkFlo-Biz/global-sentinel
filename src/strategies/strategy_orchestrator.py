#!/usr/bin/env python3
"""
Multi-Asset Strategy Orchestrator for Global Sentinel
13 strategies that train independently but share insights through quantum_feed.
Each strategy reads from shared data lake and writes its own signals.
Cross-pollination: quantum continuous learner ingests ALL strategy outputs.
"""
import json, os, datetime, traceback
from pathlib import Path

# New strategy modules
try:
    from .scalping_engine import run_scalping_engine
    from .kelly_sizer import run_kelly_sizer
    from .ict_smc_engine import run_ict_smc_engine
    from .chart_markup_engine import run_chart_markup
    from .power_market_engine import run_power_market
    from .ranked_asset_allocation import run_ranked_allocation
    from .systematic_options_selling import run_systematic_options
    NEW_STRATEGIES_AVAILABLE = True
except ImportError:
    try:
        from scalping_engine import run_scalping_engine
        from kelly_sizer import run_kelly_sizer
        from ict_smc_engine import run_ict_smc_engine
        from chart_markup_engine import run_chart_markup
        from power_market_engine import run_power_market
        from ranked_asset_allocation import run_ranked_allocation
        from systematic_options_selling import run_systematic_options
        NEW_STRATEGIES_AVAILABLE = True
    except ImportError:
        NEW_STRATEGIES_AVAILABLE = False

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO_ROOT / "data/quantum_feed"

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def log(msg):
    print(f"[{iso_now()}] ORCHESTRATOR: {msg}", flush=True)

def load_json(path):
    try: return json.loads(Path(path).read_text())
    except Exception: return {}

def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))

# === SHARED DATA LAYER ===
def get_shared_context():
    """Load all shared signals for cross-strategy insight."""
    ctx = {}
    for f in QF.glob("*.json"):
        try: ctx[f.stem] = json.loads(f.read_text())
        except Exception: pass
    return ctx

# === 1. STOCKS STRATEGY ===
def run_stocks_strategy(ctx):
    alpha = ctx.get("qlib_alpha_scores", {}).get("scores", [])
    tech = ctx.get("technical_analysis", {}).get("symbols", {})
    fund = ctx.get("fundamental_scores", {}).get("symbols", {})
    signals = []
    for a in alpha[:10]:
        sym = a.get("symbol", "")
        direction = a.get("direction", "long")
        score = a.get("alpha_score", 0)
        tech_score = tech.get(sym, {}).get("overall_score", 0) if isinstance(tech, dict) else 0
        val_score = fund.get(sym, {}).get("value_score", 5) if isinstance(fund, dict) else 5
        combined = score * 100 + tech_score * 5 + (val_score - 5) * 2
        signals.append({"symbol": sym, "direction": direction, "score": round(combined, 2), "source": "stocks"})
    signals.sort(key=lambda x: abs(x["score"]), reverse=True)
    return {"strategy": "stocks", "signals": signals[:5]}

# === 2. OPTIONS STRATEGY ===
def run_options_strategy(ctx):
    session = ctx.get("session_intelligence", {})
    amd = ctx.get("amd_phase", {})
    entry_quality = session.get("strategy", {}).get("entry_quality", 0.5) if isinstance(session.get("strategy"), dict) else 0.5
    should_trade = entry_quality >= 0.6
    signals_from_amd = amd.get("signals", [])
    return {"strategy": "options", "should_trade_0dte": should_trade, "entry_quality": entry_quality,
            "session": session.get("session", "unknown"), "amd_signals": signals_from_amd[:3],
            "rule": "100%+ gain = sell. -40% = stop. Sell before close for 0DTE."}

# === 3. CRYPTO STRATEGY ===
def run_crypto_strategy(ctx):
    raw = ctx.get("defi_data", {})
    defi = raw.get("data", raw)
    tvl_block = defi.get("tvl", {})
    tvl = tvl_block.get("current_tvl_usd", tvl_block.get("current_tvl_billions", 0))
    tvl_trend = tvl_block.get("trend", "unknown")
    stable_block = defi.get("stablecoins", {})
    stablecoin = stable_block.get("total_stablecoin_mcap_billions", stable_block.get("total_mcap", 0))
    signal = "risk_on" if tvl_trend == "expanding" else ("risk_off" if tvl_trend == "contracting" else "neutral")
    return {"strategy": "crypto", "tvl": tvl, "tvl_trend": tvl_trend, "stablecoin_mcap": stablecoin,
            "signal": signal, "direction": "long_btc_eth" if signal == "risk_on" else "avoid"}

# === 4. ETFs STRATEGY ===
def run_etfs_strategy(ctx):
    rotation = ctx.get("sector_rotation", {})
    leading = rotation.get("leading_sectors", [])
    lagging = rotation.get("lagging_sectors", [])
    return {"strategy": "etfs", "long_sectors": leading[:3], "avoid_sectors": lagging[:3],
            "rebalance": "weekly", "rule": "Equal weight top 3 performing sectors"}

# === 5. FUTURES/COMMODITIES STRATEGY ===
def run_futures_strategy(ctx):
    signal = ctx.get("latest_signal", {})
    war = signal.get("bucket_scores", {}).get("OIL_SUPPLY", 5)
    uncertainty = ctx.get("uncertainty_premium", {})
    premium = uncertainty.get("uncertainty_premium", 0)
    if war >= 7 and premium > 2:
        direction = "long_oil"
        tickers = ["USO", "UCO", "XLE", "OXY"]
    elif war <= 3:
        direction = "short_oil"
        tickers = ["SCO", "KOLD"]
    else:
        direction = "neutral"
        tickers = []
    return {"strategy": "futures_commodities", "war_intensity": war, "uncertainty_premium": premium,
            "direction": direction, "tickers": tickers}

# === 6. BONDS STRATEGY ===
def run_bonds_strategy(ctx):
    bond = ctx.get("bond_intelligence", {})
    curve_slope = bond.get("yield_curve_slope", 0)
    econ = ctx.get("economic_surprise", {})
    surprise = econ.get("rolling_30d_index", 0)
    if curve_slope < -0.2:
        signal = "recession_warning"
        direction = "long_TLT"
    elif surprise > 0.5:
        signal = "economy_hot"
        direction = "short_TLT"
    else:
        signal = "neutral"
        direction = "neutral"
    return {"strategy": "bonds", "curve_slope": curve_slope, "econ_surprise": surprise,
            "signal": signal, "direction": direction}

# === 7. CURRENCIES STRATEGY ===
def run_currencies_strategy(ctx):
    currency = ctx.get("currency_strength", {})
    usd_strength = currency.get("usd_index", 0)
    direction = "long_UUP" if usd_strength > 0.5 else ("short_UUP" if usd_strength < -0.5 else "neutral")
    return {"strategy": "currencies", "usd_strength": usd_strength, "direction": direction}

# === 8. PREDICTION MARKETS STRATEGY ===
def run_prediction_strategy(ctx):
    poly = ctx.get("polymarket_geopolitical", {})
    uncertainty = ctx.get("uncertainty_premium", {})
    real_risk = uncertainty.get("real_risk_score", 5)
    market_risk = uncertainty.get("market_implied_risk", 5)
    divergence = real_risk - market_risk
    if divergence > 2:
        signal = "market_complacent"
        action = "buy_vol_hedge"
    elif divergence < -2:
        signal = "market_fearful"
        action = "sell_vol"
    else:
        signal = "fair_pricing"
        action = "neutral"
    return {"strategy": "prediction_markets", "divergence": round(divergence, 2), "signal": signal, "action": action}

# === 9. WORLD INDICES STRATEGY ===
def run_world_strategy(ctx):
    acled = ctx.get("acled_conflict", {})
    regions = acled.get("regions", {}) if isinstance(acled, dict) else {}
    avoid = []
    favor = []
    for region, data in regions.items():
        if isinstance(data, dict) and data.get("conflict_intensity", 0) > 5:
            avoid.append(region)
        else:
            favor.append(region)
    return {"strategy": "world_indices", "avoid_regions": avoid, "favor_regions": favor,
            "etfs": {"avoid": ["EEM", "INDA"] if "middle_east" in avoid else [],
                     "favor": ["EFA", "EWJ"] if "asia_pacific" in favor else []}}

# === 10. SECTORS STRATEGY ===
def run_sectors_strategy(ctx):
    rotation = ctx.get("sector_rotation", {})
    return {"strategy": "sectors", "data": rotation,
            "rule": "Momentum rotation: long top 3, avoid bottom 3, rebalance weekly"}

# === CROSS-POLLINATION ENGINE ===
def cross_pollinate(results):
    """Extract insights that apply across strategies."""
    insights = []
    # If bonds say recession + stocks say bearish = high conviction short
    bonds = next((r for r in results if r.get("strategy") == "bonds"), {})
    stocks = next((r for r in results if r.get("strategy") == "stocks"), {})
    if bonds.get("signal") == "recession_warning":
        insights.append({"type": "cross_signal", "message": "Bonds signal recession — reduce equity exposure across all strategies"})
    # If prediction markets say complacent + options quality high = buy puts
    pred = next((r for r in results if r.get("strategy") == "prediction_markets"), {})
    opts = next((r for r in results if r.get("strategy") == "options"), {})
    if pred.get("signal") == "market_complacent" and opts.get("should_trade_0dte"):
        insights.append({"type": "cross_signal", "message": "Market complacent + good options window = buy protective puts"})
    # If crypto risk_off + futures bearish = broad risk-off regime
    crypto = next((r for r in results if r.get("strategy") == "crypto"), {})
    futures = next((r for r in results if r.get("strategy") == "futures_commodities"), {})
    if crypto.get("signal") == "risk_off" and futures.get("direction") == "long_oil":
        insights.append({"type": "cross_signal", "message": "Crypto risk-off + oil surging = stagflation regime. Favor energy, avoid growth."})
    # ICT SMC + Scalping confluence
    ict = next((r for r in results if r.get("strategy") == "ict_smc"), {})
    scalp = next((r for r in results if r.get("strategy") == "scalping_engine"), {})
    if ict.get("signals", 0) > 0 and scalp.get("signals", 0) > 0:
        insights.append({"type": "cross_signal", "message": "ICT SMC + Scalping both firing — high conviction intraday setups available"})
    # Kelly blocking warning
    kelly = next((r for r in results if r.get("strategy") == "kelly_sizer"), {})
    if kelly.get("blocked", 0) > 0:
        insights.append({"type": "risk_warning", "message": f"Kelly Criterion blocked {kelly['blocked']} strategies — negative edge detected, DO NOT TRADE those"})
    # Chart Markup + ICT/ORB confluence
    markup = next((r for r in results if r.get("strategy") == "chart_markup"), {})
    if markup.get("trade_ideas", 0) > 0 and ict.get("signals", 0) > 0:
        insights.append({"type": "cross_signal", "message": "Chart markup levels align with ICT SMC signals — high conviction structural trade setups"})
    if markup.get("confluence_zones", 0) >= 5:
        insights.append({"type": "cross_signal", "message": f"Chart markup found {markup['confluence_zones']} confluence zones — strong structural session ahead"})
    # Power market + futures cross-pollination
    power = next((r for r in results if r.get("strategy") == "power_market"), {})
    if power.get("signals", 0) > 0 and futures.get("direction") == "long_oil":
        insights.append({"type": "cross_signal", "message": "Power market + oil both bullish — energy sector high conviction long"})
    # RAAM allocation insights
    raam = next((r for r in results if r.get("strategy") == "ranked_allocation"), {})
    if raam.get("cash_pct", 0) >= 60:
        insights.append({"type": "risk_warning", "message": f"RAAM is {raam['cash_pct']}% cash — momentum weak across asset classes, reduce exposure"})
    elif raam.get("cash_pct", 0) == 0:
        insights.append({"type": "cross_signal", "message": "RAAM fully invested (0% cash) — strong momentum across multiple asset classes"})
    # Systematic options + prediction markets confluence
    sys_opts = next((r for r in results if r.get("strategy") == "systematic_options"), {})
    if sys_opts.get("signals", 0) > 0 and pred.get("signal") == "market_complacent":
        insights.append({"type": "risk_warning", "message": "Options selling signals active but market complacent — tighten stops on short premium positions"})
    return insights

# === MASTER ORCHESTRATOR ===
def run_all():
    log("Running all 13 strategies...")
    ctx = get_shared_context()
    log(f"Shared context: {len(ctx)} data sources loaded")

    strategies = [
        ("stocks", run_stocks_strategy),
        ("options", run_options_strategy),
        ("crypto", run_crypto_strategy),
        ("etfs", run_etfs_strategy),
        ("futures_commodities", run_futures_strategy),
        ("bonds", run_bonds_strategy),
        ("currencies", run_currencies_strategy),
        ("prediction_markets", run_prediction_strategy),
        ("world_indices", run_world_strategy),
        ("sectors", run_sectors_strategy),
    ]

    results = []
    for name, fn in strategies:
        try:
            result = fn(ctx)
            results.append(result)
            save_json(QF / f"strategy_{name}.json", result)
            log(f"  {name}: OK")
        except Exception as e:
            log(f"  {name}: ERROR - {e}")
            results.append({"strategy": name, "error": str(e)})

    # Run new strategy modules (they manage their own output files)
    if NEW_STRATEGIES_AVAILABLE:
        # Kelly Sizer runs FIRST so other strategies can read sizing data
        try:
            kelly_result = run_kelly_sizer()
            results.append({
                "strategy": "kelly_sizer",
                "tradeable": len(kelly_result.get("summary", {}).get("tradeable_strategies", [])),
                "blocked": len(kelly_result.get("summary", {}).get("blocked_strategies", [])),
            })
            log(f"  kelly_sizer: OK")
        except Exception as e:
            log(f"  kelly_sizer: ERROR - {e}")
            results.append({"strategy": "kelly_sizer", "error": str(e)})

        # Scalping Engine (reads kelly_sizing.json)
        try:
            scalp_result = run_scalping_engine()
            results.append({
                "strategy": "scalping_engine",
                "signals": scalp_result.get("total_signals", 0),
            })
            log(f"  scalping_engine: OK ({scalp_result.get('total_signals', 0)} signals)")
        except Exception as e:
            log(f"  scalping_engine: ERROR - {e}")
            results.append({"strategy": "scalping_engine", "error": str(e)})

        # ICT Smart Money Concepts (reads kelly_sizing.json)
        try:
            ict_result = run_ict_smc_engine()
            results.append({
                "strategy": "ict_smc",
                "signals": len(ict_result.get("smart_money_signals", [])),
            })
            log(f"  ict_smc: OK ({len(ict_result.get('smart_money_signals', []))} signals)")
        except Exception as e:
            log(f"  ict_smc: ERROR - {e}")
            results.append({"strategy": "ict_smc", "error": str(e)})

        # Chart Markup - structural levels, no fib (reads market data)
        try:
            markup_result = run_chart_markup()
            results.append({
                "strategy": "chart_markup",
                "confluence_zones": markup_result.get("total_confluence_zones", 0),
                "trade_ideas": markup_result.get("total_trade_ideas", 0),
                "top_ideas": markup_result.get("top_ideas", [])[:3],
            })
            log(f"  chart_markup: OK ({markup_result.get('total_confluence_zones', 0)} zones, "
                f"{markup_result.get('total_trade_ideas', 0)} ideas)")
        except Exception as e:
            log(f"  chart_markup: ERROR - {e}")
            results.append({"strategy": "chart_markup", "error": str(e)})

        # Power Market - basis, directional, DART (Neel Somani / Citadel approach)
        try:
            power_result = run_power_market()
            results.append({
                "strategy": "power_market",
                "signals": power_result.get("total_signals", 0),
                "all_signals": power_result.get("all_signals", [])[:3],
            })
            log(f"  power_market: OK ({power_result.get('total_signals', 0)} signals)")
        except Exception as e:
            log(f"  power_market: ERROR - {e}")
            results.append({"strategy": "power_market", "error": str(e)})

        # Ranked Asset Allocation Model (RAAM) - Dow Award 2018 / @macro_quant_rick
        try:
            raam_result = run_ranked_allocation()
            results.append({
                "strategy": "ranked_allocation",
                "selected": raam_result.get("selected_assets", []),
                "cash_pct": raam_result.get("cash_pct", 0),
                "invested_pct": raam_result.get("invested_pct", 0),
            })
            log(f"  ranked_allocation: OK ({len(raam_result.get('selected_assets', []))} assets, "
                f"{raam_result.get('cash_pct', 0)}% cash)")
        except Exception as e:
            log(f"  ranked_allocation: ERROR - {e}")
            results.append({"strategy": "ranked_allocation", "error": str(e)})

        # Systematic Options Selling - straddles/strangles (@poojawadhwa.official)
        try:
            opts_result = run_systematic_options()
            results.append({
                "strategy": "systematic_options",
                "signals": opts_result.get("total_signals", 0),
                "all_signals": opts_result.get("all_signals", [])[:3],
            })
            log(f"  systematic_options: OK ({opts_result.get('total_signals', 0)} signals)")
        except Exception as e:
            log(f"  systematic_options: ERROR - {e}")
            results.append({"strategy": "systematic_options", "error": str(e)})
    else:
        log("  New strategy modules not available (import failed)")

    # Cross-pollination
    insights = cross_pollinate(results)
    log(f"Cross-pollination: {len(insights)} insights")

    # Master output
    master = {
        "timestamp": iso_now(),
        "strategies_run": len(results),
        "strategies_ok": sum(1 for r in results if "error" not in r),
        "results": results,
        "cross_insights": insights,
        "shared_context_sources": len(ctx),
    }
    save_json(QF / "strategy_master.json", master)
    log(f"Master output saved. {len(results)} strategies, {len(insights)} cross-insights.")
    return master

if __name__ == "__main__":
    master = run_all()
    for r in master["results"]:
        print(f"  {r.get('strategy','?'):>25s}: {json.dumps({k:v for k,v in r.items() if k != 'strategy'})[:100]}")
    if master["cross_insights"]:
        print("\nCROSS-INSIGHTS:")
        for i in master["cross_insights"]:
            print(f"  {i['message']}")
