#!/usr/bin/env python3
"""Weekly System Performance Report — sent Sunday 8PM ET."""
import json, os, datetime, glob, urllib.request
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO_ROOT / "data/quantum_feed"

env = {}
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def load_json(path):
    try: return json.loads(Path(path).read_text())
    except: return {}

def get_live_pnl():
    try:
        key = env.get("ALPACA_API_KEY_LIVE", "")
        secret = env.get("ALPACA_SECRET_KEY_LIVE", "")
        req = urllib.request.Request("https://api.alpaca.markets/v2/account")
        req.add_header("APCA-API-KEY-ID", key)
        req.add_header("APCA-API-SECRET-KEY", secret)
        d = json.loads(urllib.request.urlopen(req, timeout=10).read())
        return float(d.get("equity", 0))
    except: return 0

def get_paper_trades():
    trades = []
    for f in sorted(glob.glob(str(REPO_ROOT / "reports/paper_trades/*.json")))[-7:]:
        try: trades.append(json.loads(Path(f).read_text()))
        except: pass
    return trades

def send_telegram(msg):
    try:
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        payload = json.dumps({"chat_id": "7091381625", "text": msg[:4000], "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except: pass

def run():
    print(f"[{iso_now()}] Generating weekly report...")
    equity = get_live_pnl()
    paper = get_paper_trades()
    kelly = load_json(QF / "kelly_sizing.json")
    quantum = load_json(QF / "strategy_recommendations.json")
    alpha = load_json(QF / "alpha_validation.json")
    feedback = load_json(QF / "signal_quality_weights.json")
    corr = load_json(QF / "strategy_correlation_weights.json")
    params = load_json(QF / "optimized_params.json")

    paper_pnl = sum(t.get("total_pnl", 0) for t in paper)
    paper_trades = sum(t.get("num_trades", 0) for t in paper)
    paper_winners = sum(t.get("winners", 0) for t in paper)

    report = {
        "timestamp": iso_now(),
        "week": datetime.date.today().strftime("%Y-W%W"),
        "live_account": {"equity": equity},
        "paper_trading": {"total_pnl": paper_pnl, "trades": paper_trades, "winners": paper_winners,
                          "win_rate": round(paper_winners / max(1, paper_trades), 3)},
        "quantum": {"weight": quantum.get("quantum_weight", "?"), "best_strategy": quantum.get("best_overall_strategy", "?")},
        "kelly_sizing": kelly.get("strategies", {}),
        "signal_quality": feedback.get("signal_scores", {}),
        "correlation_weights": corr.get("allocation_weights", {}),
        "optimized_params": params,
    }

    out_dir = REPO_ROOT / "reports/weekly"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"week_{report['week']}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))

    msg = f"<b>Weekly Report — {report['week']}</b>\n\n"
    msg += f"Live Equity: ${equity:.2f}\n"
    msg += f"Paper P&L: ${paper_pnl:.2f} ({paper_trades} trades, {round(paper_winners/max(1,paper_trades)*100)}% win)\n"
    msg += f"Quantum Weight: {quantum.get('quantum_weight', '?')}\n"
    msg += f"Best Strategy: {quantum.get('best_overall_strategy', '?')}\n"
    if params:
        msg += f"\nOptimized: stop={params.get('stop_loss_pct','?'):.1f}% take={params.get('take_profit_pct','?'):.0f}%"
    send_telegram(msg)
    print(f"Report saved + Telegram sent")

if __name__ == "__main__":
    run()
