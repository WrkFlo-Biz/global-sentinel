#!/usr/bin/env python3
"""
Global Sentinel — 24/7 Volatility Opportunity Monitor

Runs continuously. Monitors:
  - Real-time market data (VIX, oil, gold, futures) from Yahoo Finance
  - Crypto prices 24/7 (BTC, ETH, SOL) from Alpaca
  - Global Sentinel war intensity + bucket scores (latest_signal.json)
  - Current positions and buying power

Logic:
  - Computes a VOLATILITY SCORE (0–10) each cycle
  - Computes an OPPORTUNITY SIGNAL per instrument (BUY / WAIT / AVOID)
  - Sends Telegram alert when a high-confidence opportunity emerges
  - During market hours: monitors equity ETFs
  - Extended hours (4–9:30 AM, 4–8 PM ET): limit orders with extended_hours=true
  - 24/7: Crypto (BTC, ETH, SOL)
  - Does NOT auto-execute — sends recommendation + approval touch file

Cycle: every 5 minutes
"""
import json, ssl, time, sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─── Config ────────────────────────────────────────────────────────────────────
REPO_ROOT   = Path("/opt/global-sentinel")
SIGNAL_FILE = REPO_ROOT / "data/quantum_feed/latest_signal.json"
APPROVAL_DIR = Path("/tmp")

CYCLE_SECS = 300  # 5 minutes

# Volatility thresholds to trigger alerts
VOL_SCORE_ALERT   = 5.5   # send opportunity alert above this
VOL_SCORE_STRONG  = 7.0   # "strong" signal label above this
MIN_CONF_TO_ALERT = 0.60  # instrument-level confidence floor

# Position sizing ($)
MAX_SINGLE_POSITION = 50.0
MIN_CASH_RESERVE    = 20.0   # never deploy below this cash level

# ─── Watchlist ─────────────────────────────────────────────────────────────────
# Each entry: symbol, type, bias (LONG/SHORT), trigger buckets, description
WATCHLIST = [
    # --- Oil & Energy ---
    {"sym": "UCO",  "type": "etf",    "bias": "LONG",  "buckets": ["OIL_SUPPLY","ENERGY_CASCADE"],   "desc": "2x WTI crude oil"},
    {"sym": "SCO",  "type": "etf",    "bias": "SHORT", "buckets": ["OIL_SUPPLY","ENERGY_CASCADE"],   "desc": "2x inverse crude (oil drop)"},
    {"sym": "BOIL", "type": "etf",    "bias": "LONG",  "buckets": ["ENERGY_CASCADE"],                "desc": "2x natural gas"},
    {"sym": "KOLD", "type": "etf",    "bias": "SHORT", "buckets": ["ENERGY_CASCADE"],                "desc": "2x inverse natgas"},
    # --- Volatility ---
    {"sym": "UVXY", "type": "etf",    "bias": "LONG",  "buckets": ["GEOPOLITICAL","TECH_SELLOFF"],   "desc": "1.5x VIX futures"},
    {"sym": "SVXY", "type": "etf",    "bias": "SHORT", "buckets": ["GEOPOLITICAL","TECH_SELLOFF"],   "desc": "0.5x inverse VIX (vol crush)"},
    # --- Defense ---
    {"sym": "ITA",  "type": "etf",    "bias": "LONG",  "buckets": ["DEFENSE","GEOPOLITICAL"],        "desc": "Aerospace & defense"},
    # --- Safe Haven ---
    {"sym": "GLD",  "type": "etf",    "bias": "LONG",  "buckets": ["SAFE_HAVEN","INFLATION"],        "desc": "Gold ETF"},
    {"sym": "TLT",  "type": "etf",    "bias": "LONG",  "buckets": ["SAFE_HAVEN","TECH_SELLOFF"],     "desc": "20yr Treasury bonds"},
    {"sym": "TBT",  "type": "etf",    "bias": "SHORT", "buckets": ["INFLATION","SAFE_HAVEN"],        "desc": "2x inverse TLT (rates up)"},
    # --- Inverse Equity ---
    {"sym": "SQQQ", "type": "etf",    "bias": "SHORT", "buckets": ["TECH_SELLOFF","GEOPOLITICAL"],   "desc": "3x inverse Nasdaq"},
    {"sym": "SPXS", "type": "etf",    "bias": "SHORT", "buckets": ["TECH_SELLOFF","GEOPOLITICAL"],   "desc": "3x inverse S&P500"},
    # --- Shipping ---
    {"sym": "BDRY", "type": "etf",    "bias": "LONG",  "buckets": ["SHIPPING","OIL_SUPPLY"],         "desc": "Dry bulk shipping"},
    # --- Food/Ag ---
    {"sym": "WEAT", "type": "etf",    "bias": "LONG",  "buckets": ["FOOD_CHAIN","GEOPOLITICAL"],     "desc": "Wheat futures ETF"},
    {"sym": "CORN", "type": "etf",    "bias": "LONG",  "buckets": ["FOOD_CHAIN"],                    "desc": "Corn futures ETF"},
    # --- Crypto 24/7 ---
    {"sym": "BTC/USD",  "type": "crypto", "bias": "LONG", "buckets": ["SAFE_HAVEN","INFLATION"],     "desc": "Bitcoin (24/7)"},
    {"sym": "ETH/USD",  "type": "crypto", "bias": "LONG", "buckets": ["TECH_SELLOFF","SAFE_HAVEN"],  "desc": "Ethereum (24/7)"},
    {"sym": "SOL/USD",  "type": "crypto", "bias": "LONG", "buckets": ["TECH_SELLOFF"],               "desc": "Solana (24/7)"},
]

# ─── Init ───────────────────────────────────────────────────────────────────────
ctx = ssl.create_default_context()

env = {}
with open(REPO_ROOT / ".env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip("'\"")

TG_TOKEN   = env.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT    = env.get("TELEGRAM_CHAT_ID", "")
# Route noisy system updates to topic group instead of main chat
TG_TOPIC_CHAT  = env.get("TELEGRAM_TOPIC_CHAT_ID", "")
TG_V6_THREAD   = env.get("TELEGRAM_V6_DIGEST_THREAD_ID", "")
ALP_KEY    = env.get("ALPACA_API_KEY_LIVE", "")
ALP_SECRET = env.get("ALPACA_SECRET_KEY_LIVE", "")
ALP_H = {
    "APCA-API-KEY-ID":     ALP_KEY,
    "APCA-API-SECRET-KEY": ALP_SECRET,
    "Content-Type":        "application/json",
}
BASE  = "https://api.alpaca.markets"
BDATA = "https://data.alpaca.markets"

# ─── Helpers ────────────────────────────────────────────────────────────────────
def _notifications_muted() -> bool:
    raw = env.get("TELEGRAM_UPDATES_MUTED_UNTIL", "")
    if not raw:
        return False
    try:
        deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            from datetime import timezone as _tz
            deadline = deadline.replace(tzinfo=_tz.utc)
        return datetime.now(timezone.utc) < deadline
    except Exception:
        return False

def send_telegram(msg: str):
    if _notifications_muted():
        return
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        payload = json.dumps({"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        print(f"  [TG] {e}")


def send_telegram_topic(msg: str):
    """Send to topic group chat (system noise channel) instead of main chat."""
    if _notifications_muted():
        return
    chat = TG_TOPIC_CHAT or TG_CHAT
    thread = TG_V6_THREAD
    if not TG_TOKEN or not chat:
        return
    try:
        payload_d = {"chat_id": chat, "text": msg, "parse_mode": "HTML", "disable_notification": True}
        if thread:
            payload_d["message_thread_id"] = int(thread)
        payload = json.dumps(payload_d).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        print(f"  [TG-TOPIC] {e}")


def alp_get(path: str, base: str = BASE) -> dict:
    try:
        req = urllib.request.Request(f"{base}{path}", headers=ALP_H)
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  [ALP GET {path}] {e}")
        return {}


def alp_post(path: str, body: dict) -> dict:
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(f"{BASE}{path}", data=data, headers=ALP_H, method="POST")
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  [ALP POST {path}] {e}")
        return {}


def yahoo_quote_single(sym: str) -> tuple:
    """Fetch one Yahoo Finance quote via v8 chart API. Returns (sym, {price, change_pct})."""
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=2d"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            data = json.loads(r.read())
        meta  = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
        price = meta.get("regularMarketPrice", 0)
        prev  = meta.get("chartPreviousClose", price) or price
        chg   = ((price - prev) / prev * 100) if prev else 0
        return sym, {"price": price, "change_pct": round(chg, 2)}
    except Exception as e:
        print(f"  [YAHOO {sym}] {e}")
        return sym, {"price": 0, "change_pct": 0}


def yahoo_quote(symbols: list) -> dict:
    """Fetch Yahoo Finance quotes in parallel threads."""
    import concurrent.futures
    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(symbols)) as ex:
        futures = {ex.submit(yahoo_quote_single, s): s for s in symbols}
        for f in concurrent.futures.as_completed(futures, timeout=20):
            try:
                sym, data = f.result()
                result[sym] = data
            except Exception:
                pass
    return result


def load_signal() -> dict:
    """Load latest Global Sentinel signal (< 30 min fresh)."""
    if not SIGNAL_FILE.exists():
        return {}
    try:
        data = json.loads(SIGNAL_FILE.read_text())
        ts = datetime.fromisoformat(data["timestamp_utc"])
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > 1800:
            return {}
        return data
    except Exception:
        return {}


def session_type(now_et: datetime) -> str:
    """Return 'regular', 'extended', or 'closed' based on ET time."""
    h, m = now_et.hour, now_et.minute
    # Regular: 9:30–16:00
    if (h > 9 or (h == 9 and m >= 30)) and h < 16:
        return "regular"
    # Pre-market: 4:00–9:30
    if 4 <= h < 9 or (h == 9 and m < 30):
        return "extended"
    # After-hours: 16:00–20:00
    if 16 <= h < 20:
        return "extended"
    return "closed"


# ─── Core scoring ───────────────────────────────────────────────────────────────
def compute_volatility_score(market: dict, signal: dict) -> dict:
    """
    Returns a dict with:
      total_score (0–10), components, market_regime, notes
    """
    components = {}
    notes = []

    vix_price  = market.get("^VIX",  {}).get("price", 0)
    vix_chg    = market.get("^VIX",  {}).get("change_pct", 0)
    wti_chg    = market.get("CL=F",  {}).get("change_pct", 0)
    gold_chg   = market.get("GC=F",  {}).get("change_pct", 0)
    sp500_chg  = market.get("ES=F",  {}).get("change_pct", 0)
    ndx_chg    = market.get("NQ=F",  {}).get("change_pct", 0)
    natgas_chg = market.get("NG=F",  {}).get("change_pct", 0)

    # VIX level score (0–3)
    if vix_price >= 35:
        vix_score = 3.0; notes.append(f"VIX={vix_price:.1f} (extreme fear)")
    elif vix_price >= 25:
        vix_score = 2.0; notes.append(f"VIX={vix_price:.1f} (elevated)")
    elif vix_price >= 18:
        vix_score = 1.0; notes.append(f"VIX={vix_price:.1f} (moderate)")
    else:
        vix_score = 0.0
    components["vix_level"] = vix_score

    # VIX spike/crush (0–2)
    vix_move = abs(vix_chg)
    if vix_move >= 15:
        components["vix_move"] = 2.0; notes.append(f"VIX move {vix_chg:+.1f}%")
    elif vix_move >= 8:
        components["vix_move"] = 1.0
    else:
        components["vix_move"] = 0.0

    # Oil move (0–2) — big oil move = geopolitical volatility
    oil_move = abs(wti_chg)
    if oil_move >= 4:
        components["oil_move"] = 2.0; notes.append(f"WTI {wti_chg:+.1f}%")
    elif oil_move >= 2:
        components["oil_move"] = 1.0; notes.append(f"WTI {wti_chg:+.1f}%")
    else:
        components["oil_move"] = 0.0

    # Gold move (0–1.5) — safe haven demand
    if gold_chg >= 1.5 or gold_chg <= -1.5:
        components["gold_move"] = 1.5; notes.append(f"Gold {gold_chg:+.1f}%")
    elif abs(gold_chg) >= 0.8:
        components["gold_move"] = 0.75
    else:
        components["gold_move"] = 0.0

    # Equity futures divergence (0–1.5) — big equity moves = opportunity
    eq_move = max(abs(sp500_chg), abs(ndx_chg))
    if eq_move >= 2:
        components["equity_move"] = 1.5; notes.append(f"Futures ±{eq_move:.1f}%")
    elif eq_move >= 1:
        components["equity_move"] = 0.75
    else:
        components["equity_move"] = 0.0

    # GS war intensity (0–2)
    war = signal.get("war_intensity", 0) or signal.get("market_war_intensity", 0)
    if war >= 7:
        components["war_intensity"] = 2.0; notes.append(f"War intensity {war:.1f}/10")
    elif war >= 4:
        components["war_intensity"] = 1.0
    elif war >= 2:
        components["war_intensity"] = 0.5
    else:
        components["war_intensity"] = 0.0

    total = min(10.0, sum(components.values()))

    # Regime classification
    if total >= 7.5:
        regime = "CRISIS"
    elif total >= 5.5:
        regime = "ELEVATED"
    elif total >= 3.0:
        regime = "MODERATE"
    else:
        regime = "CALM"

    return {
        "total_score": round(total, 2),
        "components":  components,
        "regime":      regime,
        "notes":       notes,
        "war_intensity": war,
        "vix":         vix_price,
        "vix_chg":     vix_chg,
        "wti_chg":     wti_chg,
    }


def score_instruments(vol: dict, signal: dict, session: str, held_syms: set) -> list:
    """
    Returns list of scored opportunities, sorted by confidence desc.
    Skips instruments not tradable in current session.
    """
    bucket_scores = signal.get("bucket_scores", {})
    war           = vol["war_intensity"]
    vix           = vol["vix"]
    vix_chg       = vol["vix_chg"]
    wti_chg       = vol["wti_chg"]
    total_vol     = vol["total_score"]

    opps = []
    for inst in WATCHLIST:
        sym    = inst["sym"]
        itype  = inst["type"]
        bias   = inst["bias"]
        bkts   = inst["buckets"]

        # Session filter
        if itype == "crypto":
            pass  # always tradable
        elif session == "closed":
            continue
        # ETFs only in regular or extended
        # (already covered: if not crypto and session==closed, skip)

        # Skip already held
        alp_sym = sym.replace("/USD", "USD")
        if alp_sym in held_syms or sym in held_syms:
            continue

        # Bucket signal strength
        bucket_max = max((bucket_scores.get(b, 0) for b in bkts), default=0)

        # Directional confidence
        # LONG bias: want high bucket score + rising stress indicators
        # SHORT bias: want high bucket score + falling stress (mean reversion)
        if bias == "LONG":
            bucket_conf = bucket_max / 10.0
        else:  # SHORT
            # Inverse ETFs profit when underlying stress is HIGH (counter-intuitive naming)
            # e.g. SQQQ LONG when market is falling = bucket_conf is equity stress
            bucket_conf = bucket_max / 10.0

        # VIX-based confidence boost for volatility products
        vix_boost = 0.0
        if sym in ("UVXY",) and vix >= 25:
            vix_boost = 0.15
        elif sym in ("SVXY",) and vix >= 30 and vix_chg < -5:
            vix_boost = 0.20  # vol crush play

        # Oil-based boost
        oil_boost = 0.0
        if sym in ("UCO", "GUSH", "BOIL") and wti_chg >= 2:
            oil_boost = 0.15
        elif sym in ("SCO", "KOLD") and wti_chg <= -2:
            oil_boost = 0.15

        # War intensity boost for defense + safe haven
        war_boost = 0.0
        if sym in ("ITA", "GLD", "TLT") and war >= 5:
            war_boost = 0.10

        # Global vol score contribution
        vol_conf = min(0.30, total_vol / 10.0 * 0.30)

        confidence = min(0.95, bucket_conf * 0.50 + vol_conf + vix_boost + oil_boost + war_boost)

        # Only include if above minimum
        if confidence < 0.25:
            continue

        # ── Profit optimization scoring ────────────────────────────────────────
        # Estimate expected move % based on current vol regime and instrument type
        # Leverage multipliers: 2x/3x ETFs amplify both gains and losses
        LEVERAGE = {
            "UCO": 2, "SCO": 2, "BOIL": 2, "KOLD": 2,
            "UVXY": 1.5, "SQQQ": 3, "SPXS": 3, "TBT": 2,
        }
        lev = LEVERAGE.get(sym, 1)

        # Base expected move from vol score (higher vol = bigger expected swing)
        base_move_pct = total_vol * 0.8  # e.g. vol=7 → ~5.6% base move
        if itype == "crypto":
            base_move_pct *= 1.5  # crypto amplifies vol moves

        # Adjust by directional confidence
        expected_gain_pct = base_move_pct * confidence * lev

        # Risk penalty: high leverage = higher risk of whipsaw
        risk_penalty = (lev - 1) * 0.3  # 0 for 1x, 0.3 for 2x, 0.6 for 3x

        # Session risk: extended hours has wider spreads (~0.3% penalty)
        session_penalty = 0.3 if session == "extended" else 0.0

        # Profit score = expected gain adjusted for risk
        profit_score = max(0, expected_gain_pct - risk_penalty - session_penalty)

        # Direction label (for limit order side)
        side = "buy"  # all instruments entered long (inverse ETFs are bought to go short underlying)

        opps.append({
            "sym":           sym,
            "type":          itype,
            "bias":          bias,
            "desc":          inst["desc"],
            "side":          side,
            "confidence":    round(confidence, 3),
            "bucket_max":    round(bucket_max, 1),
            "buckets":       bkts,
            "session":       session,
            "leverage":      lev,
            "expected_gain": round(expected_gain_pct, 1),
            "profit_score":  round(profit_score, 2),
        })

    # Sort by profit_score (expected return per $ after risk adjustment)
    opps.sort(key=lambda x: x["profit_score"], reverse=True)
    return opps


def format_opportunity_alert(vol: dict, opps: list, cash: float, session: str) -> str:
    regime   = vol["regime"]
    score    = vol["total_score"]
    notes    = " | ".join(vol["notes"]) if vol["notes"] else "no major moves"
    vix      = vol["vix"]
    ts       = datetime.now(timezone.utc).strftime("%H:%M UTC")

    regime_emoji = {"CRISIS": "🔴", "ELEVATED": "🟡", "MODERATE": "🟠", "CALM": "🟢"}.get(regime, "⚪")

    lines = [
        f"<b>GS VOLATILITY ALERT — {regime_emoji} {regime}</b>",
        f"Score: <b>{score:.1f}/10</b> | VIX: {vix:.1f} | {ts}",
        f"<i>{notes}</i>",
        f"Cash available: ${cash:.2f}",
        f"Session: {session.upper()}",
        "",
        "<b>Top Opportunities:</b>",
    ]

    for i, opp in enumerate(opps[:5], 1):
        conf_pct = int(opp["confidence"] * 100)
        bkt      = ", ".join(opp["buckets"][:2])
        size     = min(cash - MIN_CASH_RESERVE, MAX_SINGLE_POSITION)
        size     = max(0, size)
        approval = f"/tmp/gs_vol_approve_{opp['sym'].replace('/', '')}"
        lev_str  = f"{opp['leverage']}x lev" if opp["leverage"] > 1 else "1x"
        est_pnl  = size * opp["expected_gain"] / 100

        lines.append(
            f"{i}. <b>{opp['sym']}</b> {opp['desc']}"
        )
        lines.append(
            f"   Conf: {conf_pct}% | Expected gain: ~{opp['expected_gain']:.1f}% ({lev_str})"
        )
        lines.append(
            f"   Est P&L on ${size:.0f}: <b>+${est_pnl:.2f}</b> | Signal: {opp['bucket_max']:.1f}/10 [{bkt}]"
        )
        lines.append(f"   Approve: <code>touch {approval}</code>")

    lines += [
        "",
        "<b>Notes:</b>",
        "• Extended hours = limit orders only, wider spreads",
        "• Crypto = 24/7, no restriction",
        "• Size conservatively — 1 position max per signal",
        "• Stop-loss monitor active at $110 portfolio value",
    ]
    return "\n".join(lines)


# ─── Main loop ──────────────────────────────────────────────────────────────────
YAHOO_SYMS = ["^VIX", "CL=F", "BZ=F", "GC=F", "ES=F", "NQ=F", "NG=F", "TLT", "GLD"]

print("=" * 60)
print("  GS 24/7 VOLATILITY OPPORTUNITY MONITOR")
print(f"  Cycle: {CYCLE_SECS}s | Alert threshold: {VOL_SCORE_ALERT}/10")
print("=" * 60)

last_alert_ts  = 0
last_alert_sym = ""
alert_cooldown = 3600  # 1 hour between alerts for same instrument
cycle          = 0

while True:
    try:
        cycle += 1
        now_utc = datetime.now(timezone.utc)
        now_et  = now_utc.astimezone(timezone(timedelta(hours=-4)))
        session = session_type(now_et)

        print(f"\n[{now_utc.strftime('%H:%M UTC')}] Cycle #{cycle} | Session: {session.upper()}")

        # 1. Market data
        market = yahoo_quote(YAHOO_SYMS)
        vix_p  = market.get("^VIX", {}).get("price", 0)
        wti_p  = market.get("CL=F", {}).get("price", 0)
        wti_c  = market.get("CL=F", {}).get("change_pct", 0)
        gold_p = market.get("GC=F", {}).get("price", 0)
        print(f"  VIX={vix_p:.1f}  WTI=${wti_p:.1f}({wti_c:+.1f}%)  Gold=${gold_p:.0f}")

        # 2. GS signal
        signal = load_signal()
        war    = signal.get("war_intensity", 0) or signal.get("market_war_intensity", 0)
        print(f"  GS war_intensity={war:.1f}  signal_age={'fresh' if signal else 'STALE'}")

        # 3. Volatility score
        vol = compute_volatility_score(market, signal)
        print(f"  Vol score: {vol['total_score']:.1f}/10 [{vol['regime']}]  {' | '.join(vol['notes'][:3])}")

        # 4. Account state
        acct    = alp_get("/v2/account")
        cash    = float(acct.get("cash", 0))
        equity  = float(acct.get("equity", 0))
        print(f"  Account: cash=${cash:.2f}  equity=${equity:.2f}")

        positions = alp_get("/v2/positions")
        if isinstance(positions, list):
            held_syms = {p.get("symbol", "") for p in positions}
        else:
            held_syms = set()

        # 5. Crypto prices (always check 24/7) — BTC/USD from Alpaca crypto endpoint
        btc_p = 0
        try:
            btc_q = alp_get("/v1beta3/crypto/us/latest/trades?symbols=BTC%2FUSD", base=BDATA)
            t = btc_q.get("trades", {}).get("BTC/USD", {})
            btc_p = t.get("p", 0) if isinstance(t, dict) else 0
        except Exception:
            pass
        print(f"  BTC≈${btc_p:,.0f}")

        # 6. Score opportunities
        opps = score_instruments(vol, signal, session, held_syms)
        top_opps = [o for o in opps if o["confidence"] >= MIN_CONF_TO_ALERT]
        print(f"  Opportunities: {len(top_opps)} above {int(MIN_CONF_TO_ALERT*100)}% conf threshold")
        for o in top_opps[:3]:
            print(f"    {o['sym']:<10s} conf={int(o['confidence']*100)}%  bkt={o['bucket_max']:.1f}  [{o['type']}]")

        # 7. Check for approval files (execute approved orders)
        # --- max_positions enforcement (fix 2026-03-11) ---
        MAX_TOTAL_POSITIONS = 8  # from execution_mode.yaml day_trade.max_positions
        current_position_count = len(held_syms) if isinstance(held_syms, set) else 0

        for opp in opps:
            approval_file = Path(f"/tmp/gs_vol_approve_{opp['sym'].replace('/', '')}")
            if approval_file.exists():
                approval_file.unlink()

                # Enforce max_positions limit
                if current_position_count >= MAX_TOTAL_POSITIONS:
                    print(f"  [SKIP] {opp['sym']} -- max positions reached ({current_position_count}/{MAX_TOTAL_POSITIONS})")
                    continue

                size = min(cash - MIN_CASH_RESERVE, MAX_SINGLE_POSITION)
                if size < 5:
                    print(f"  [SKIP] {opp['sym']} — insufficient cash (${cash:.2f})")
                    continue

                print(f"\n  [APPROVED] Executing {opp['sym']} {opp['side']} ${size:.2f}...")

                if opp["type"] == "crypto":
                    # Crypto: market order, no extended_hours needed
                    order_body = {
                        "symbol":        opp["sym"],
                        "notional":      str(round(size, 2)),
                        "side":          opp["side"],
                        "type":          "market",
                        "time_in_force": "gtc",
                    }
                elif session == "extended":
                    # Extended hours: limit order required
                    # Get latest quote for limit price
                    q = alp_get(f"/v2/stocks/{opp['sym']}/quotes/latest", base=BDATA)
                    ask = q.get("quote", {}).get("ap", 0) if q else 0
                    if ask <= 0:
                        print(f"  [SKIP] {opp['sym']} — no quote available")
                        continue
                    limit_px = round(ask * 1.002, 2)  # 0.2% above ask
                    order_body = {
                        "symbol":          opp["sym"],
                        "notional":        str(round(size, 2)),
                        "side":            opp["side"],
                        "type":            "limit",
                        "limit_price":     str(limit_px),
                        "time_in_force":   "day",
                        "extended_hours":  True,
                    }
                else:
                    # Regular hours: market order
                    order_body = {
                        "symbol":        opp["sym"],
                        "notional":      str(round(size, 2)),
                        "side":          opp["side"],
                        "type":          "market",
                        "time_in_force": "day",
                    }

                result = alp_post("/v2/orders", order_body)
                status = result.get("status", "?")
                oid    = result.get("id", "?")[:12]
                print(f"  [ORDER] {opp['sym']} {status} id={oid}")
                send_telegram(
                    f"<b>ORDER EXECUTED</b>\n\n"
                    f"<b>{opp['sym']}</b> {opp['side'].upper()} ${size:.2f}\n"
                    f"Status: {status} | ID: {oid}\n"
                    f"Session: {session} | Conf: {int(opp['confidence']*100)}%\n"
                    f"Vol score at execution: {vol['total_score']:.1f}/10"
                )

        # 8. Decide whether to send alert
        now_ts = time.time()
        should_alert = (
            vol["total_score"] >= VOL_SCORE_ALERT
            and len(top_opps) > 0
            and cash >= (MIN_CASH_RESERVE + 10)
            and (now_ts - last_alert_ts) >= alert_cooldown
        )

        if should_alert:
            last_alert_ts  = now_ts
            last_alert_sym = top_opps[0]["sym"] if top_opps else ""
            alert_msg = format_opportunity_alert(vol, top_opps, cash, session)
            send_telegram_topic(alert_msg)
            print(f"\n  [ALERT SENT] Vol={vol['total_score']:.1f} top={last_alert_sym}")
        elif vol["total_score"] < 3.0:
            print(f"  [CALM] Vol={vol['total_score']:.1f} — no alert (below threshold {VOL_SCORE_ALERT})")

        # 9. Hourly status digest (on the hour)
        if now_utc.minute < (CYCLE_SECS // 60) and cycle > 1:
            digest = (
                f"<b>GS VOL MONITOR — Hourly Status</b>\n"
                f"Vol score: {vol['total_score']:.1f}/10 [{vol['regime']}]\n"
                f"VIX: {vix_p:.1f} | WTI: ${wti_p:.1f} ({wti_c:+.1f}%)\n"
                f"War intensity: {war:.1f}/10\n"
                f"Cash: ${cash:.2f} | Equity: ${equity:.2f}\n"
                f"Positions held: {len(held_syms)}\n"
                f"Session: {session.upper()}\n"
                f"Opportunities above threshold: {len(top_opps)}"
            )
            send_telegram_topic(digest)
            print("  [DIGEST] Hourly status sent to topic")

    except KeyboardInterrupt:
        print("\nMonitor stopped.")
        sys.exit(0)
    except Exception as e:
        print(f"  [ERROR] {e}")
        import traceback; traceback.print_exc()

    time.sleep(CYCLE_SECS)
