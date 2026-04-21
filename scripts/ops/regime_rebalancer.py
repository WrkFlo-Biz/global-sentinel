#!/usr/bin/env python3
"""Regime-Aware Auto-Rebalancing — runs every 30 min during market hours.

Monitors HMM regime + quantum regime prediction and auto-adjusts strategy
allocation weights when regime shifts are confirmed (2 consecutive checks).
"""
import json, os, datetime, urllib.request
from pathlib import Path
import sys

# --- Telegram topic routing ---
sys.path.insert(0, "/opt/global-sentinel") if "/opt/global-sentinel" not in sys.path else None
try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO_ROOT / "data" / "quantum_feed"

# Load .env
env = {}
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v

# ---------- Regime allocation profiles ----------
REGIME_PROFILES = {
    "calm": {
        "allocation_weights": {
            "orb": 0.20, "ict_smc": 0.20, "momentum": 0.25,
            "scalping": 0.15, "overnight_gap": 0.15, "ensemble_rl": 0.05,
        },
        "kelly_regime_size_multiplier": 1.0,
    },
    "transition": {
        "allocation_weights": {
            "orb": 0.10, "ict_smc": 0.25, "momentum": 0.20,
            "scalping": 0.10, "overnight_gap": 0.30, "ensemble_rl": 0.05,
        },
        "kelly_regime_size_multiplier": 0.6,
    },
    "crisis": {
        "allocation_weights": {
            "orb": 0.05, "ict_smc": 0.15, "momentum": 0.10,
            "scalping": 0.05, "overnight_gap": 0.25, "ensemble_rl": 0.05,
            "cash": 0.35,
        },
        "kelly_regime_size_multiplier": 0.3,
    },
}

STATE_FILE = QF / "regime_rebalancer_state.json"
HISTORY_FILE = QF / "regime_history.jsonl"
WEIGHTS_FILE = QF / "strategy_correlation_weights.json"
KELLY_FILE = QF / "kelly_sizing.json"


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))


def append_jsonl(path, record):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def send_telegram(msg):
    if _send_topic:
        try:
            _send_topic(msg[:4000] if isinstance(msg, str) else str(msg)[:4000], topic="macro")
            return
        except Exception:
            pass
    try:
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        payload = json.dumps({
            "chat_id": "7091381625",
            "text": msg[:4000],
            "parse_mode": "HTML", "message_thread_id": 74,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram send failed: {e}")


def get_current_regime():
    """Read HMM + quantum regime. HMM is primary, quantum is secondary signal."""
    hmm = load_json(QF / "hmm_regime.json")
    quantum = load_json(QF / "quantum_regime_prediction.json")

    hmm_regime = hmm.get("current_regime", "unknown")
    hmm_probs = hmm.get("regime_probabilities", {})

    q_pred = quantum.get("quantum_prediction", {})
    quantum_regime = q_pred.get("regime", "unknown")
    quantum_probs = q_pred.get("probabilities", {})

    # Use HMM as primary. If quantum disagrees AND has high confidence, note it.
    primary_regime = hmm_regime
    agreement = hmm_regime == quantum_regime

    return {
        "primary_regime": primary_regime,
        "hmm_regime": hmm_regime,
        "hmm_probabilities": hmm_probs,
        "quantum_regime": quantum_regime,
        "quantum_probabilities": quantum_probs,
        "agreement": agreement,
    }


def load_state():
    return load_json(STATE_FILE) or {
        "last_regime": None,
        "pending_regime": None,
        "pending_count": 0,
        "active_regime": None,
        "last_rebalance": None,
    }


def save_state(state):
    state["updated"] = iso_now()
    save_json(STATE_FILE, state)


def apply_rebalance(regime_name, regime_info):
    """Update strategy_correlation_weights.json and kelly_sizing.json."""
    profile = REGIME_PROFILES.get(regime_name, REGIME_PROFILES["transition"])

    # Update correlation weights file
    weights = load_json(WEIGHTS_FILE)
    old_weights = weights.get("allocation_weights", {})
    weights["allocation_weights"] = profile["allocation_weights"]
    weights["regime_adjustments"] = {
        "current_regime": regime_name,
        "regime_size_multiplier": profile["kelly_regime_size_multiplier"],
        "breakout_penalty": 0.3 if regime_name == "crisis" else (0.5 if regime_name == "transition" else 1.0),
        "trend_boost": 0.8 if regime_name == "crisis" else (1.2 if regime_name == "transition" else 1.0),
        "rebalanced_by": "regime_rebalancer",
        "rebalanced_at": iso_now(),
    }
    weights["updated"] = iso_now()
    save_json(WEIGHTS_FILE, weights)

    # Update kelly sizing with regime multiplier
    kelly = load_json(KELLY_FILE)
    kelly["regime_size_multiplier"] = profile["kelly_regime_size_multiplier"]
    kelly["regime"] = regime_name
    kelly["regime_updated"] = iso_now()
    save_json(KELLY_FILE, kelly)

    return old_weights


def run():
    print(f"[{iso_now()}] Regime rebalancer running...")

    regime_info = get_current_regime()
    current_regime = regime_info["primary_regime"]
    print(f"  HMM regime: {regime_info['hmm_regime']}, Quantum regime: {regime_info['quantum_regime']}, Agreement: {regime_info['agreement']}")

    if current_regime == "unknown":
        print("  No regime data available, skipping.")
        return

    state = load_state()
    prev_active = state.get("active_regime")

    # Log to history every run
    history_entry = {
        "timestamp": iso_now(),
        "hmm_regime": regime_info["hmm_regime"],
        "quantum_regime": regime_info["quantum_regime"],
        "agreement": regime_info["agreement"],
        "hmm_probabilities": regime_info["hmm_probabilities"],
        "quantum_probabilities": regime_info["quantum_probabilities"],
        "active_regime": prev_active,
    }
    append_jsonl(HISTORY_FILE, history_entry)

    # Anti-thrash: require 2 consecutive checks (1 hour) before rebalancing
    if current_regime != prev_active:
        if state.get("pending_regime") == current_regime:
            state["pending_count"] = state.get("pending_count", 0) + 1
        else:
            # New pending regime
            state["pending_regime"] = current_regime
            state["pending_count"] = 1

        if state["pending_count"] >= 2:
            # Confirmed regime shift — rebalance
            print(f"  CONFIRMED regime shift: {prev_active} -> {current_regime} (persisted {state['pending_count']} checks)")
            old_weights = apply_rebalance(current_regime, regime_info)

            profile = REGIME_PROFILES[current_regime]
            new_weights = profile["allocation_weights"]
            multiplier = profile["kelly_regime_size_multiplier"]

            # Build Telegram message
            msg = f"<b>REGIME SHIFT: {prev_active} \u2192 {current_regime}</b>\n\n"
            msg += f"HMM: {regime_info['hmm_regime']} (p={regime_info['hmm_probabilities'].get(current_regime, '?'):.4f})\n"
            msg += f"Quantum: {regime_info['quantum_regime']} ({'agrees' if regime_info['agreement'] else 'disagrees'})\n\n"
            msg += "<b>New Allocations:</b>\n"
            for strat, wt in sorted(new_weights.items(), key=lambda x: -x[1]):
                old_wt = old_weights.get(strat, 0)
                arrow = "\u2191" if wt > old_wt else ("\u2193" if wt < old_wt else "\u2192")
                msg += f"  {strat}: {old_wt*100:.0f}% {arrow} {wt*100:.0f}%\n"
            msg += f"\nKelly multiplier: {multiplier}x\n"

            if current_regime == "crisis":
                msg += "\n\u26a0\ufe0f Reducing breakout strategies, increasing cash buffer."
            elif current_regime == "calm":
                msg += "\n\u2705 Markets calming. Broadening strategy allocation."
            else:
                msg += "\n\u26a1 Transition regime. Favoring overnight gap + ICT/SMC."

            send_telegram(msg)

            state["active_regime"] = current_regime
            state["last_rebalance"] = iso_now()
            state["pending_regime"] = None
            state["pending_count"] = 0
        else:
            print(f"  Pending regime shift: {prev_active} -> {current_regime} (check {state['pending_count']}/2, need 1 more)")
    else:
        # Same regime, reset pending
        state["pending_regime"] = None
        state["pending_count"] = 0
        print(f"  Regime stable: {current_regime}")

    state["last_regime"] = current_regime
    save_state(state)
    print(f"  Done. Active regime: {state.get('active_regime')}")


if __name__ == "__main__":
    run()
