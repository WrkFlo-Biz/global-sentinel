#!/usr/bin/env python3
"""Daily LLM Thesis Generator — morning brief with trade ideas and conviction scores."""

from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", Path(__file__).resolve().parents[2]))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

from src.core.control_state_snapshot import read_control_state_snapshot
from src.inference.foundry_client import FoundryResponse, send_request

env = {}
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()

TG_TOKEN = env.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = env.get("TELEGRAM_TOPIC_CHAT_ID", "")
TG_THREAD = env.get("TELEGRAM_DEFAULT_THREAD_ID", "74")


def iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _coerce_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def gather_context():
    ctx = {}
    ctx["latest_signal"] = load_json(REPO_ROOT / "data/quantum_feed/latest_signal.json")
    ctx["hmm_regime"] = load_json(REPO_ROOT / "data/quantum_feed/hmm_regime.json")
    ctx["strategy_recs"] = load_json(REPO_ROOT / "data/quantum_feed/strategy_recommendations.json")
    ctx["price_forecasts"] = load_json(REPO_ROOT / "data/quantum_feed/price_forecasts.json")
    ctx["polymarket"] = load_json(REPO_ROOT / "data/quantum_feed/polymarket_geopolitical.json")
    ctx["optimal_portfolio"] = load_json(REPO_ROOT / "data/quantum_feed/optimal_portfolio.json")
    ctx["quantum_regime"] = load_json(REPO_ROOT / "data/quantum_feed/quantum_regime_prediction.json")
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    ctx["paper_trades"] = load_json(REPO_ROOT / f"reports/paper_trades/day_trade_{yesterday}.json")
    return ctx


def build_operating_context(ctx) -> dict[str, object]:
    hmm_regime = ctx.get("hmm_regime") if isinstance(ctx.get("hmm_regime"), dict) else {}
    latest_signal = ctx.get("latest_signal") if isinstance(ctx.get("latest_signal"), dict) else {}
    control_snapshot = read_control_state_snapshot(REPO_ROOT)
    return {
        "mode": (
            hmm_regime.get("operating_mode")
            or hmm_regime.get("mode")
            or latest_signal.get("operating_mode")
            or latest_signal.get("mode")
            or "NORMAL"
        ),
        "regime_shift_probability": _coerce_float(
            hmm_regime.get("regime_shift_probability")
            or latest_signal.get("regime_shift_probability")
            or 0.0
        ),
        "manual_veto": control_snapshot["manual_veto"],
        "kill_switch": control_snapshot["kill_switch"],
        "execution_sensitivity": "research_only",
    }


def call_llm(
    prompt: str,
    operating_context: dict[str, object],
    trace_context: dict[str, str],
) -> FoundryResponse:
    return send_request(
        intent_type="daily_thesis",
        target_role="summarizer",
        operating_context=operating_context,
        latency_class="batch",
        trace_context=trace_context,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior trading strategist. Generate a concise, actionable "
                    "morning thesis for day trading. Focus on highest-conviction plays "
                    "with specific entry/exit levels. Be direct — no fluff."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )


def send_telegram(msg: str) -> None:
    if _send_topic:
        try:
            _send_topic(msg[:4000] if isinstance(msg, str) else str(msg)[:4000], topic="research")
            return
        except Exception:
            pass
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        payload = json.dumps(
            {
                "chat_id": TG_CHAT,
                "text": msg[:4096],
                "parse_mode": "HTML",
                "message_thread_id": int(TG_THREAD),
                "disable_notification": False,
            }
        ).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        print(f"Telegram error: {exc}")


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

    thesis_date = datetime.date.today().isoformat()
    thesis_run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = REPO_ROOT / "data/quantum_feed/daily_thesis.json"
    trace_context = {
        "trace_id": f"daily-thesis-{thesis_run_id}",
        "intent_id": f"daily-thesis-{thesis_run_id}",
        "package_id": f"daily-thesis-{thesis_run_id}",
        "report_path": str(out_path),
    }

    try:
        response = call_llm(prompt, build_operating_context(ctx), trace_context)
        thesis = response.output
        route = response.route
        trace_id = response.trace_id
        policy_annotations = response.policy_annotations
    except Exception as exc:
        thesis = f"LLM error: {exc}"
        route = {}
        trace_id = trace_context["trace_id"]
        policy_annotations = {"error": str(exc)}

    print(f"[{iso_now()}] Thesis generated")

    output = {
        "timestamp": iso_now(),
        "date": thesis_date,
        "thesis": thesis,
        "context_summary": {key: bool(value) for key, value in ctx.items()},
        "route": route,
        "trace_id": trace_id,
        "policy_annotations": policy_annotations,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"[{iso_now()}] Saved to {out_path}")

    tg_msg = f"<b>Morning Thesis — {thesis_date}</b>\n\n{thesis[:3500]}"
    send_telegram(tg_msg)
    print(f"[{iso_now()}] Sent to Telegram")


if __name__ == "__main__":
    run()
