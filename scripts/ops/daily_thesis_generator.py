#!/usr/bin/env python3
"""Daily LLM Thesis Generator — morning brief with trade ideas and conviction scores."""
import json, os, sys, datetime, urllib.request, urllib.error
from pathlib import Path

# --- Telegram topic routing ---
sys.path.insert(0, "/opt/global-sentinel") if "/opt/global-sentinel" not in sys.path else None
try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))

env = {}
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

AZURE_ENDPOINT = env.get("AZURE_OPENAI_ENDPOINT", "https://moses-8586-resource.services.ai.azure.com/")
AZURE_KEY = env.get("AZURE_OPENAI_API_KEY", env.get("AZURE_CLAUDE_API_KEY", ""))
AZURE_DEPLOYMENT = env.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
API_VERSION = env.get("AZURE_OPENAI_API_VERSION", "2024-05-01-preview")

TG_TOKEN = env.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = env.get("TELEGRAM_TOPIC_CHAT_ID", "")
TG_THREAD = env.get("TELEGRAM_DEFAULT_THREAD_ID", "74")

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}

def gather_context():
    ctx = {}
    ctx["latest_signal"] = load_json(REPO_ROOT / "data/quantum_feed/latest_signal.json")
    ctx["hmm_regime"] = load_json(REPO_ROOT / "data/quantum_feed/hmm_regime.json")
    ctx["strategy_recs"] = load_json(REPO_ROOT / "data/quantum_feed/strategy_recommendations.json")
    ctx["price_forecasts"] = load_json(REPO_ROOT / "data/quantum_feed/price_forecasts.json")
    ctx["polymarket"] = load_json(REPO_ROOT / "data/quantum_feed/polymarket_geopolitical.json")
    ctx["optimal_portfolio"] = load_json(REPO_ROOT / "data/quantum_feed/optimal_portfolio.json")
    ctx["quantum_regime"] = load_json(REPO_ROOT / "data/quantum_feed/quantum_regime_prediction.json")
    # Get yesterday's paper trade results
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    ctx["paper_trades"] = load_json(REPO_ROOT / f"reports/paper_trades/day_trade_{yesterday}.json")
    return ctx

def call_llm(prompt):
    url = f"{AZURE_ENDPOINT}openai/deployments/{AZURE_DEPLOYMENT}/chat/completions?api-version={API_VERSION}"
    body = json.dumps({
        "messages": [
            {"role": "system", "content": "You are a senior trading strategist. Generate a concise, actionable morning thesis for day trading. Focus on highest-conviction plays with specific entry/exit levels. Be direct — no fluff."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 1500,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "api-key": AZURE_KEY,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"LLM error: {e}"

def send_telegram(msg):
    if _send_topic:
        try:
            _send_topic(msg[:4000] if isinstance(msg, str) else str(msg)[:4000], topic="research")
            return
        except Exception:
            pass
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        payload = json.dumps({
            "chat_id": TG_CHAT, "text": msg[:4096], "parse_mode": "HTML",
            "message_thread_id": int(TG_THREAD), "disable_notification": False
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def run():
    print(f"[{iso_now()}] Generating daily thesis...")
    ctx = gather_context()

    prompt = f"""Generate a morning trading thesis based on this data:

REGIME: {json.dumps(ctx.get('hmm_regime', {}), indent=1)[:500]}
QUANTUM SIGNAL: {json.dumps(ctx.get('latest_signal', {}), indent=1)[:500]}
POLYMARKET: {json.dumps(ctx.get('polymarket', {}), indent=1)[:500]}
STRATEGY RECS: {json.dumps(ctx.get('strategy_recs', {}), indent=1)[:500]}
PRICE FORECASTS: {json.dumps(ctx.get('price_forecasts', {}), indent=1)[:500]}
YESTERDAY'S PAPER TRADES: {json.dumps(ctx.get('paper_trades', {}), indent=1)[:300]}

Provide:
1. Market regime assessment (1 sentence)
2. Top 3 trade ideas with: ticker, direction, entry price, stop loss, target, conviction (1-10)
3. Key risks today (2-3 bullets)
4. Sector rotation view (1 sentence)
5. Binary catalysts to watch today
"""

    thesis = call_llm(prompt)
    print(f"[{iso_now()}] Thesis generated")

    output = {
        "timestamp": iso_now(),
        "date": datetime.date.today().isoformat(),
        "thesis": thesis,
        "context_summary": {k: bool(v) for k, v in ctx.items()},
    }

    out_path = REPO_ROOT / "data/quantum_feed/daily_thesis.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"[{iso_now()}] Saved to {out_path}")

    tg_msg = f"<b>Morning Thesis — {datetime.date.today().isoformat()}</b>\n\n{thesis[:3500]}"
    send_telegram(tg_msg)
    print(f"[{iso_now()}] Sent to Telegram")

if __name__ == "__main__":
    run()
