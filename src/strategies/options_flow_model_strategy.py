"""Order-flow based options strategy.

Signal model:
  1. Unusual volume: option volume / open_interest ratio vs rolling baseline.
     Flag when ratio exceeds +2 std deviations (institutional accumulation).
  2. Dark pool prints: large block trades below bid (dark pool buying) signal
     stealth institutional positioning ahead of a directional move.
  3. Put/call flow imbalance: net dollar flow (calls - puts) z-scored against
     the rolling 20-period mean and standard deviation.
  4. Sweep detection: multi-exchange options sweeps on a single symbol within
     a short window indicate urgency — directional conviction is high.

Each check contributes a sub-score; the composite determines confidence.
Signals are directional: a call-skewed flow imbalance → long equity;
a put-skewed flow → short equity or put position.

Candidate dict follows GS standard schema (identical to commodity strategy).

Transcript refinement from tab 15 (`the1to1trader`):
  - directional bias from an hourly impulsive move with a retained HTF gap
  - liquidity sweep inside that gap
  - 5-minute break/displacement of the 13 EMA as the entry trigger
  - tight stop below the entry candle with 1:1 acceptable when confluences stack
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Watchlist — high-liquidity options universe
# ---------------------------------------------------------------------------

OPTIONS_WATCHLIST: list[str] = [
    "SPY", "QQQ", "IWM",
    "AAPL", "NVDA", "TSLA", "META", "AMZN", "GOOGL", "MSFT",
    "AMD", "INTC", "NFLX",
    "XLE", "XOP", "USO", "GLD",
    "JPM", "GS", "BAC",
]

# ---------------------------------------------------------------------------
# Default parameters
# ---------------------------------------------------------------------------

PARAMS: dict[str, Any] = {
    # Z-score threshold for flow imbalance to trigger a signal
    "flow_zscore_threshold": 2.0,
    # Minimum volume/OI ratio (absolute) to flag unusual volume
    "min_vol_oi_ratio": 0.30,
    # Z-score of vol/OI ratio to flag unusual activity
    "unusual_vol_zscore": 2.0,
    # Dark pool relative size: block_volume / avg_dark_volume > threshold
    "dark_pool_block_threshold": 2.5,
    # Minimum sweep count in last session to add sweep bonus
    "min_sweep_count": 3,
    # Rolling window length for mean/std (periods)
    "rolling_window": 20,
    # Minimum composite confidence to emit a candidate
    "min_confidence": 0.50,
    # Max candidates per scan
    "max_candidates": 5,
    # Base notional
    "base_notional_usd": 850.0,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _pct_change(sym_data: dict[str, Any]) -> float:
    if "change_pct" in sym_data:
        return _safe_float(sym_data["change_pct"])
    price = _safe_float(sym_data.get("price"))
    prior = _safe_float(sym_data.get("prior_close"))
    if price > 0 and prior > 0:
        return (price - prior) / prior * 100.0
    return 0.0


def _relative_volume(sym_data: dict[str, Any]) -> float:
    if "relative_volume" in sym_data:
        return _safe_float(sym_data["relative_volume"], 1.0)
    vol = _safe_float(sym_data.get("volume"))
    avg = _safe_float(sym_data.get("avg_volume"))
    if vol > 0 and avg > 0:
        return vol / avg
    return 1.0


def _zscore(value: float, mean: float, std: float) -> float:
    if std <= 0:
        return 0.0
    return (value - mean) / std


# ---------------------------------------------------------------------------
# Flow sub-scorers
# ---------------------------------------------------------------------------


def _unusual_volume_score(
    sym_data: dict[str, Any], params: dict[str, Any]
) -> tuple[float, str]:
    """Return (score 0-1, direction 'call'|'put'|'neutral')."""
    # Pre-computed unusual volume signal from options_greeks_bridge / flow feed
    uv_score = _safe_float(sym_data.get("unusual_volume_score"))
    if uv_score > 0:
        direction = sym_data.get("unusual_volume_direction", "neutral")
        return min(uv_score, 1.0), str(direction)

    # Compute from raw fields
    call_vol = _safe_float(sym_data.get("call_volume"))
    put_vol  = _safe_float(sym_data.get("put_volume"))
    call_oi  = _safe_float(sym_data.get("call_open_interest"), default=1.0)
    put_oi   = _safe_float(sym_data.get("put_open_interest"), default=1.0)

    call_ratio = call_vol / max(call_oi, 1.0)
    put_ratio  = put_vol  / max(put_oi,  1.0)

    # Rolling stats from history list (if provided)
    call_history: list[float] = sym_data.get("call_vol_oi_history", [])
    put_history:  list[float] = sym_data.get("put_vol_oi_history",  [])

    def _ratio_zscore(ratio: float, history: list[float]) -> float:
        if len(history) < 2:
            return ratio / max(params["min_vol_oi_ratio"], 0.01) - 1.0
        mean = sum(history) / len(history)
        variance = sum((x - mean) ** 2 for x in history) / len(history)
        std = math.sqrt(variance) if variance > 0 else 0.01
        return _zscore(ratio, mean, std)

    call_z = _ratio_zscore(call_ratio, call_history[-params["rolling_window"]:])
    put_z  = _ratio_zscore(put_ratio,  put_history[-params["rolling_window"]:])

    threshold = params["unusual_vol_zscore"]
    if call_z >= threshold and call_z > put_z:
        score = min((call_z - threshold) / threshold * 0.50 + 0.40, 1.0)
        return score, "call"
    if put_z >= threshold and put_z > call_z:
        score = min((put_z - threshold) / threshold * 0.50 + 0.40, 1.0)
        return score, "put"
    if call_ratio >= params["min_vol_oi_ratio"]:
        return min(call_ratio * 0.3, 0.35), "call"
    if put_ratio >= params["min_vol_oi_ratio"]:
        return min(put_ratio * 0.3, 0.35), "put"

    return 0.0, "neutral"


def _dark_pool_score(
    sym_data: dict[str, Any], params: dict[str, Any]
) -> tuple[float, str]:
    """Return (score 0-1, direction 'long'|'short'|'neutral')."""
    # Pre-computed dark pool signal
    dp_score = _safe_float(sym_data.get("dark_pool_score"))
    if dp_score > 0:
        dp_dir = sym_data.get("dark_pool_direction", "neutral")
        return min(dp_score, 1.0), str(dp_dir)

    dp_buy_vol  = _safe_float(sym_data.get("dark_pool_buy_volume"))
    dp_sell_vol = _safe_float(sym_data.get("dark_pool_sell_volume"))
    avg_dp_vol  = _safe_float(sym_data.get("avg_dark_pool_volume"), default=1.0)

    total_dp = dp_buy_vol + dp_sell_vol
    if total_dp <= 0 or avg_dp_vol <= 0:
        return 0.0, "neutral"

    block_ratio = total_dp / avg_dp_vol
    if block_ratio < params["dark_pool_block_threshold"]:
        return 0.0, "neutral"

    # Net directional bias of the dark pool block
    net = dp_buy_vol - dp_sell_vol
    imbalance = net / max(total_dp, 1.0)  # -1 to +1
    score = min((block_ratio / params["dark_pool_block_threshold"] - 1.0) * 0.35 + 0.30, 0.80)

    if imbalance > 0.15:
        return score, "long"
    if imbalance < -0.15:
        return score, "short"
    return score * 0.5, "neutral"


def _flow_imbalance_score(
    sym_data: dict[str, Any], params: dict[str, Any]
) -> tuple[float, str]:
    """Z-score net dollar flow (calls - puts) vs rolling mean (0–1, direction)."""
    # Pre-computed field from options_greeks_bridge or flow feed
    fi_z = _safe_float(sym_data.get("flow_imbalance_zscore"))
    if fi_z != 0.0:
        direction = "call" if fi_z > 0 else "put"
        score = min(abs(fi_z) / (params["flow_zscore_threshold"] * 2.0) * 0.70, 0.85)
        return (score, direction) if abs(fi_z) >= params["flow_zscore_threshold"] else (0.0, "neutral")

    # Compute from raw dollar flow fields
    call_dollar = _safe_float(sym_data.get("call_dollar_flow"))
    put_dollar  = _safe_float(sym_data.get("put_dollar_flow"))
    net_flow    = call_dollar - put_dollar

    history: list[float] = sym_data.get("net_flow_history", [])
    window = params["rolling_window"]
    if len(history) >= 2:
        recent = history[-window:]
        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)
        std = math.sqrt(variance) if variance > 0 else 1.0
        z = _zscore(net_flow, mean, std)
    else:
        # No history: use absolute flow magnitude as a rough proxy
        total = call_dollar + put_dollar
        if total <= 0:
            return 0.0, "neutral"
        z = (net_flow / total) * 3.0  # scale to approximate z-score range

    threshold = params["flow_zscore_threshold"]
    if z >= threshold:
        score = min((z - threshold) / threshold * 0.45 + 0.40, 0.85)
        return score, "call"
    if z <= -threshold:
        score = min((abs(z) - threshold) / threshold * 0.45 + 0.40, 0.85)
        return score, "put"
    return 0.0, "neutral"


def _sweep_score(sym_data: dict[str, Any], params: dict[str, Any]) -> tuple[float, str]:
    """Multi-exchange sweep detection bonus (0–0.25, direction)."""
    sweep_count = int(_safe_float(sym_data.get("sweep_count")))
    sweep_dir   = str(sym_data.get("sweep_direction", "neutral"))
    if sweep_count < params["min_sweep_count"]:
        return 0.0, "neutral"
    score = min((sweep_count - params["min_sweep_count"]) / 5.0 * 0.18 + 0.08, 0.25)
    return score, sweep_dir


def _flow_model_checklist(
    sym_data: dict[str, Any],
    direction: str,
) -> tuple[float, list[str]]:
    """Transcript-derived 3-step checklist from the reel's flow model."""
    score = 0.0
    notes: list[str] = []

    htf_dir = str(
        sym_data.get("higher_timeframe_gap_direction")
        or sym_data.get("hourly_gap_direction")
        or ""
    ).strip().lower()
    if (direction == "long" and htf_dir in {"up", "long", "bull", "bullish"}) or (
        direction == "short" and htf_dir in {"down", "short", "bear", "bearish"}
    ):
        score += 0.08
        notes.append("htf_gap_bias")

    if sym_data.get("liquidity_swept") is True:
        score += 0.08
        notes.append("liquidity_sweep")

    ema_break = sym_data.get("ema_13_break") or sym_data.get("ema13_break")
    if isinstance(ema_break, str):
        ema_break = ema_break.lower()
    if (direction == "long" and ema_break in {True, "up", "bull", "bullish"}) or (
        direction == "short" and ema_break in {"down", "bear", "bearish"}
    ):
        score += 0.09
        notes.append("ema13_displacement")

    return min(score, 0.22), notes


# ---------------------------------------------------------------------------
# Main strategy class
# ---------------------------------------------------------------------------


class OptionsFlowModelStrategy:
    """Order-flow based options signal generator.

    Inputs expected per symbol in *market_data*:
        Standard fields: price, prior_close / change_pct, volume, avg_volume
        Options-flow fields (any non-zero subset sufficient):
          call_volume, put_volume, call_open_interest, put_open_interest,
          call_vol_oi_history, put_vol_oi_history   (list[float])
          call_dollar_flow, put_dollar_flow, net_flow_history (list[float])
          dark_pool_buy_volume, dark_pool_sell_volume, avg_dark_pool_volume
          flow_imbalance_zscore, unusual_volume_score, dark_pool_score
          sweep_count, sweep_direction
    """

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self._params = {**PARAMS, **(params or {})}

    def _evaluate_symbol(
        self,
        symbol: str,
        sym_data: dict[str, Any],
        scorecard: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        # --- Sub-scores ---
        uv_score, uv_dir     = _unusual_volume_score(sym_data, self._params)
        dp_score, dp_dir     = _dark_pool_score(sym_data, self._params)
        fi_score, fi_dir     = _flow_imbalance_score(sym_data, self._params)
        sw_score, sw_dir     = _sweep_score(sym_data, self._params)

        # --- Direction vote (majority of non-neutral signals) ---
        direction_votes: dict[str, float] = {"call": 0.0, "put": 0.0, "long": 0.0, "short": 0.0}
        for score, d in [(uv_score, uv_dir), (dp_score, dp_dir),
                         (fi_score, fi_dir), (sw_score, sw_dir)]:
            if d in direction_votes:
                direction_votes[d] += score

        # Normalise call/long → long, put/short → short
        bull = direction_votes.get("call", 0.0) + direction_votes.get("long", 0.0)
        bear = direction_votes.get("put",  0.0) + direction_votes.get("short", 0.0)

        if bull == 0 and bear == 0:
            return None

        direction = "long" if bull >= bear else "short"
        directional_agreement = max(bull, bear) / max(bull + bear, 1e-9)
        checklist_score, checklist_notes = _flow_model_checklist(sym_data, direction)

        # --- Composite confidence ---
        confidence = (
            uv_score * 0.30
            + dp_score * 0.30
            + fi_score * 0.28
            + sw_score * 0.12
        )
        confidence += checklist_score
        # Directional agreement bonus (signals pointing the same way)
        confidence += (directional_agreement - 0.5) * 0.10
        confidence = min(confidence, 0.92)

        # Scorecard volatility regime bonus
        if scorecard:
            components = scorecard.get("component_scores", {})
            market_vol = _safe_float(components.get("market_volatility"))
            if market_vol > 0.50:
                confidence += 0.03

        if confidence < self._params["min_confidence"]:
            return None

        # --- Build candidate ---
        chg  = _pct_change(sym_data)
        rvol = _relative_volume(sym_data)
        notional = self._params["base_notional_usd"] * (0.78 + confidence * 0.40)

        # Momentum alignment check (flow direction matches price action)
        momentum_aligned = (direction == "long" and chg > 0) or (direction == "short" and chg < 0)
        alignment_note   = "momentum-aligned" if momentum_aligned else "counter-trend"

        active_signals = []
        if uv_score > 0: active_signals.append(f"unusual_vol={uv_score:.2f}({uv_dir})")
        if dp_score > 0: active_signals.append(f"dark_pool={dp_score:.2f}({dp_dir})")
        if fi_score > 0: active_signals.append(f"flow_imbal={fi_score:.2f}({fi_dir})")
        if sw_score > 0: active_signals.append(f"sweeps={sw_score:.2f}({sw_dir})")
        if checklist_notes: active_signals.append(f"flow_model={','.join(checklist_notes)}")

        return {
            "strategy": "options_flow_model",
            "symbol": symbol,
            "direction": direction,
            "holding_period": "intraday-1d",
            "notional_usd": round(notional, 2),
            "confidence_score": round(confidence, 3),
            "confidence": round(confidence, 3),
            "stop_loss_pct": -1.2 if "ema13_displacement" in checklist_notes else -2.0,
            "take_profit_pct": 1.2 if "ema13_displacement" in checklist_notes else 3.8,
            "tier": "tier_1",
            "tier_size_multiplier": round(min(0.80, 0.40 + confidence * 0.36), 2),
            "account": "day_trade",
            "entry_signal": (
                f"Flow imbalance >{self._params['flow_zscore_threshold']}σ on {symbol} "
                f"— {direction.upper()} ({alignment_note})"
            ),
            "rationale": (
                f"Composite flow score={confidence:.2f} | "
                + " | ".join(active_signals)
                + f" | agreement={directional_agreement:.0%}"
            ),
            "metadata": {
                "source": "options_flow_model",
                "unusual_volume_score": round(uv_score, 3),
                "unusual_volume_direction": uv_dir,
                "dark_pool_score": round(dp_score, 3),
                "dark_pool_direction": dp_dir,
                "flow_imbalance_score": round(fi_score, 3),
                "flow_imbalance_direction": fi_dir,
                "sweep_score": round(sw_score, 3),
                "sweep_direction": sw_dir,
                "directional_agreement_pct": round(directional_agreement * 100, 1),
                "relative_volume": round(rvol, 3),
                "price_change_pct": round(chg, 3),
                "momentum_aligned": momentum_aligned,
                "flow_model_checklist": checklist_notes,
            },
        }

    def scan_watchlist(
        self,
        market_data: dict[str, dict[str, Any]] | None = None,
        scorecard: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if market_data is None:
            return []

        ideas: list[dict[str, Any]] = []
        for symbol in OPTIONS_WATCHLIST:
            sym_data = market_data.get(symbol)
            if not sym_data:
                continue
            candidate = self._evaluate_symbol(symbol, sym_data, scorecard)
            if candidate:
                ideas.append(candidate)

        ideas.sort(key=lambda x: x["confidence_score"], reverse=True)
        return ideas[: self._params["max_candidates"]]


# ---------------------------------------------------------------------------
# GS-standard evaluate function
# ---------------------------------------------------------------------------


def evaluate_options_flow_model(
    strat: dict[str, Any] | None = None,
    market_data: dict[str, dict[str, Any]] | None = None,
    scorecard: dict[str, Any] | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """Entry point called by GS strategy orchestrator."""
    strategy = OptionsFlowModelStrategy(
        params=dict((strat or {}).get("params", {}))
    )
    return strategy.scan_watchlist(market_data=market_data, scorecard=scorecard)
