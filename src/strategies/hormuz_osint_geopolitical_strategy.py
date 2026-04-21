"""OSINT-enhanced Strait of Hormuz oil chokehold geopolitical strategy.

Signal sources consumed from market_data and osint_data kwargs:
  - GDELT event scores for Iran / Gulf / Hormuz conflict keywords
  - Exa search bridge headlines with escalation scoring
  - MaritimeBridgeV2 AIS vessel disruption index for Hormuz chokepoint
  - WTI-Brent spread widening as a forward supply-risk indicator
  - GS scorecard geopolitical_tension and v6_oil_regime components

Long signals (escalation / supply disruption):
  USO, XLE, XOP, OXY, DVN  → energy producers squeeze on Hormuz closure
  GLD, SLV                  → safe-haven bid on Middle East escalation
  LMT, RTX, NOC             → defense premium on kinetic escalation

Short signals (traffic disruption / cost spike):
  DAL, UAL, AAL, ALK        → airline fuel cost and route disruption
  ZIM, SBLK, EGLE           → shipping rerouting / volume loss
  CCL, RCL                  → cruise / leisure demand shock

Candidate dict follows the GS standard schema identical to
commodity_regime_rotation_strategy.py.

Transcript refinement from tab 17 (`bilawal.ai`):
  - dark-vessel AIS gaps during transit are a key hidden-risk signal
  - per-barrel toll charges through the chokepoint create an additional
    petrochemical / fertilizer cost shock
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------

LONG_ENERGY: list[str] = ["USO", "XLE", "XOP", "OXY", "DVN"]
LONG_DEFENSE: list[str] = ["LMT", "RTX", "NOC"]
LONG_SAFEHAVEN: list[str] = ["GLD", "SLV"]

SHORT_AIRLINES: list[str] = ["DAL", "UAL", "AAL", "ALK"]
SHORT_SHIPPING: list[str] = ["ZIM", "SBLK", "EGLE"]
SHORT_LEISURE: list[str] = ["CCL", "RCL"]

ALL_WATCHED: list[str] = (
    LONG_ENERGY + LONG_DEFENSE + LONG_SAFEHAVEN
    + SHORT_AIRLINES + SHORT_SHIPPING + SHORT_LEISURE
)

# ---------------------------------------------------------------------------
# Default parameters
# ---------------------------------------------------------------------------

PARAMS: dict[str, Any] = {
    # Minimum composite Hormuz escalation score (0–1) to generate any signal
    "min_escalation_score": 0.38,
    # WTI-Brent spread (USD) above which supply risk premium is flagged
    "wti_brent_spread_threshold": 2.50,
    # Maritime disruption index threshold (0–1) from MaritimeBridgeV2
    "maritime_disruption_threshold": 0.45,
    # Dark-vessel AIS event threshold
    "dark_vessel_threshold": 3.0,
    # Toll charge threshold in USD / barrel
    "toll_per_barrel_threshold": 1.0,
    # GDELT conflict event score threshold
    "gdelt_conflict_threshold": 0.40,
    # Max candidates returned per call
    "max_candidates": 6,
    # Base notional
    "base_notional_usd": 900.0,
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


# ---------------------------------------------------------------------------
# Escalation scoring
# ---------------------------------------------------------------------------


def _gdelt_score(osint_data: dict[str, Any]) -> float:
    """Extract normalised GDELT Hormuz conflict score (0–1)."""
    gdelt = osint_data.get("gdelt", {})
    # GDELT bridge may provide a pre-computed 'hormuz_conflict_score'
    raw = _safe_float(gdelt.get("hormuz_conflict_score") or gdelt.get("conflict_score"))
    if raw > 0:
        return min(raw, 1.0)

    # Fallback: count Iran / Hormuz / Gulf keyword hits in article list
    articles = gdelt.get("articles", [])
    if not articles:
        return 0.0
    keywords = {"hormuz", "iran", "irgc", "strait", "gulf", "blockade", "chokepoint",
                "tanker", "oil supply", "missile", "attack"}
    hits = sum(
        1
        for a in articles
        if any(kw in str(a.get("title", "")).lower() + str(a.get("body", "")).lower()
               for kw in keywords)
    )
    return min(hits / max(len(articles), 1) * 2.5, 1.0)


def _exa_escalation_score(osint_data: dict[str, Any]) -> float:
    """Extract escalation signal from Exa search bridge results (0–1)."""
    exa = osint_data.get("exa", {})
    raw = _safe_float(exa.get("escalation_score") or exa.get("hormuz_score"))
    if raw > 0:
        return min(raw, 1.0)

    results = exa.get("results", [])
    if not results:
        return 0.0
    escalation_keywords = {
        "attack", "seized", "mine", "missile", "blockade", "closure",
        "escalat", "military", "strike", "weapon", "war", "conflict",
    }
    score_sum = 0.0
    for r in results:
        text = (str(r.get("title", "")) + " " + str(r.get("summary", ""))).lower()
        hit_count = sum(1 for kw in escalation_keywords if kw in text)
        score_sum += min(hit_count / 4.0, 1.0)
    return min(score_sum / max(len(results), 1), 1.0)


def _maritime_disruption_score(osint_data: dict[str, Any]) -> float:
    """Extract Hormuz vessel disruption index from MaritimeBridgeV2 (0–1)."""
    maritime = osint_data.get("maritime", {})
    # MaritimeBridgeV2 populates chokepoint disruption per key
    hormuz = maritime.get("hormuz", {})
    raw = _safe_float(
        hormuz.get("disruption_score")
        or hormuz.get("disruption_index")
        or maritime.get("hormuz_disruption_score")
    )
    if raw > 0:
        return min(raw, 1.0)

    # Fallback: vessel count anomaly (transit_count vs baseline)
    transit = _safe_float(hormuz.get("transit_count_24h"))
    baseline = _safe_float(hormuz.get("baseline_transit_count"), default=150.0)
    if transit > 0 and baseline > 0:
        # 50% reduction = score 1.0
        reduction = max(0.0, (baseline - transit) / baseline)
        return min(reduction * 2.0, 1.0)

    return 0.0


def _dark_vessel_score(osint_data: dict[str, Any], params: dict[str, Any]) -> float:
    maritime = osint_data.get("maritime", {})
    hormuz = maritime.get("hormuz", {})
    explicit = _safe_float(
        hormuz.get("dark_vessel_score")
        or maritime.get("dark_vessel_score")
    )
    if explicit > 0:
        return min(explicit, 1.0)

    count = _safe_float(
        hormuz.get("dark_vessel_count_24h")
        or maritime.get("dark_vessel_count_24h")
    )
    if count <= 0:
        return 0.0
    return min(count / max(params["dark_vessel_threshold"], 1.0) * 0.5, 1.0)


def _toll_cost_score(osint_data: dict[str, Any], params: dict[str, Any]) -> float:
    maritime = osint_data.get("maritime", {})
    hormuz = maritime.get("hormuz", {})
    toll = _safe_float(
        hormuz.get("toll_usd_per_barrel")
        or maritime.get("toll_usd_per_barrel")
    )
    if toll <= 0:
        return 0.0
    return min(toll / max(params["toll_per_barrel_threshold"], 0.1) * 0.25, 1.0)


def _wti_brent_spread(market_data: dict[str, Any]) -> float:
    """Compute WTI-Brent spread. Positive = Brent premium (normal)."""
    brent_data = market_data.get("BRENT", market_data.get("BNO", {}))
    wti_data = market_data.get("WTI", market_data.get("USO", {}))
    brent_px = _safe_float(brent_data.get("price"))
    wti_px = _safe_float(wti_data.get("price"))
    # Use explicit spread field if provided
    spread = _safe_float(market_data.get("_wti_brent_spread"))
    if spread != 0.0:
        return spread
    if brent_px > 0 and wti_px > 0:
        return brent_px - wti_px
    return 0.0


def _scorecard_geo_tension(scorecard: dict[str, Any] | None) -> float:
    if not scorecard:
        return 0.0
    components = scorecard.get("component_scores", {})
    geo = _safe_float(components.get("geopolitical_tension"))
    oil_shock = _safe_float(components.get("commodity_shock"))
    return min((geo * 0.65 + oil_shock * 0.35), 1.0)


def _composite_escalation(
    osint_data: dict[str, Any],
    market_data: dict[str, Any],
    scorecard: dict[str, Any] | None,
    params: dict[str, Any],
) -> float:
    """Weighted composite Hormuz escalation score (0–1)."""
    gdelt = _gdelt_score(osint_data)
    exa   = _exa_escalation_score(osint_data)
    ais   = _maritime_disruption_score(osint_data)
    dark  = _dark_vessel_score(osint_data, params)
    toll  = _toll_cost_score(osint_data, params)
    geo   = _scorecard_geo_tension(scorecard)

    # WTI-Brent spread contribution
    spread = _wti_brent_spread(market_data)
    spread_signal = min(max(spread - params["wti_brent_spread_threshold"], 0.0) / 5.0, 0.25)

    # Oil regime boost from scorecard
    oil_regime = str((scorecard or {}).get("v6_oil_regime", "")).upper()
    regime_boost = 0.12 if oil_regime in {"SHOCK", "DISLOCATION", "CRISIS"} else 0.0

    composite = (
        gdelt       * 0.28
        + exa       * 0.22
        + ais       * 0.25
        + dark      * 0.08
        + toll      * 0.07
        + geo       * 0.15
        + spread_signal
        + regime_boost
    )
    return min(composite, 1.0)


# ---------------------------------------------------------------------------
# Main strategy class
# ---------------------------------------------------------------------------


class HormuzOsintGeopoliticalStrategy:
    """OSINT-enhanced Hormuz geopolitical strategy.

    Inputs expected in *market_data*:
        Per-symbol dicts with keys: price, prior_close / change_pct,
        volume, avg_volume, relative_volume.

    Inputs expected in *osint_data* (passed via **kwargs):
        gdelt    : dict from GDELTBridge (articles list, conflict_score, …)
        exa      : dict from ExaSearchBridge (results list, escalation_score, …)
        maritime : dict from MaritimeBridgeV2 (hormuz disruption sub-dict)
    """

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self._params = {**PARAMS, **(params or {})}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_candidate(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        escalation: float,
        ais_score: float,
        entry_label: str,
        sym_data: dict[str, Any],
    ) -> dict[str, Any]:
        confidence = min(confidence, 0.92)
        notional = self._params["base_notional_usd"] * (0.75 + confidence * 0.45)
        rvol = _relative_volume(sym_data)
        chg = _pct_change(sym_data)
        dark_vessel = _dark_vessel_score({"maritime": {"hormuz": sym_data.get("_hormuz_maritime", {})}}, self._params)
        toll_cost = _toll_cost_score({"maritime": {"hormuz": sym_data.get("_hormuz_maritime", {})}}, self._params)

        # Tighter stops on volatile energy / defense names
        if symbol in LONG_ENERGY:
            stop_loss_pct, take_profit_pct = -2.8, 5.5
        elif symbol in SHORT_AIRLINES + SHORT_SHIPPING + SHORT_LEISURE:
            stop_loss_pct, take_profit_pct = -2.4, 4.8
        else:
            stop_loss_pct, take_profit_pct = -2.2, 4.2

        return {
            "strategy": "hormuz_osint_geopolitical",
            "symbol": symbol,
            "direction": direction,
            "holding_period": "1d-3d",
            "notional_usd": round(notional, 2),
            "confidence_score": round(confidence, 3),
            "confidence": round(confidence, 3),
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "tier": "tier_1",
            "tier_size_multiplier": round(min(0.85, 0.42 + confidence * 0.38), 2),
            "account": "day_trade",
            "entry_signal": entry_label,
            "rationale": (
                f"Hormuz escalation={escalation:.2f} AIS_disruption={ais_score:.2f} "
                f"price_chg={chg:+.2f}% rvol={rvol:.2f}x"
            ),
            "metadata": {
                "source": "hormuz_osint_geopolitical",
                "escalation_score": round(escalation, 3),
                "ais_disruption": round(ais_score, 3),
                "dark_vessel_score": round(dark_vessel, 3),
                "toll_cost_score": round(toll_cost, 3),
                "relative_volume": round(rvol, 3),
                "price_change_pct": round(chg, 3),
            },
        }

    # ------------------------------------------------------------------
    # Long leg: energy / defense / safe-haven
    # ------------------------------------------------------------------

    def _long_candidates(
        self,
        escalation: float,
        ais_score: float,
        market_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        ideas: list[dict[str, Any]] = []

        # --- Energy longs ---
        for symbol in LONG_ENERGY:
            sym_data = market_data.get(symbol, {})
            if not sym_data:
                continue
            chg = _pct_change(sym_data)
            rvol = _relative_volume(sym_data)

            # Prefer symbols already moving in the correct direction
            momentum_bonus = max(chg / 100.0, 0.0) * 0.18
            ais_bonus = ais_score * 0.12

            confidence = (
                0.40
                + escalation * 0.28
                + momentum_bonus
                + ais_bonus
                + min(rvol, 2.5) * 0.04
            )
            if confidence < 0.50:
                continue

            ideas.append(self._build_candidate(
                symbol, "long", confidence, escalation, ais_score,
                f"Hormuz supply disruption → long {symbol} (energy squeeze)",
                sym_data,
            ))

        # --- Defense longs (only on kinetic escalation) ---
        if escalation >= 0.55:
            for symbol in LONG_DEFENSE:
                sym_data = market_data.get(symbol, {})
                if not sym_data:
                    continue
                rvol = _relative_volume(sym_data)
                confidence = 0.38 + escalation * 0.25 + min(rvol, 2.0) * 0.04
                if confidence < 0.48:
                    continue
                ideas.append(self._build_candidate(
                    symbol, "long", confidence, escalation, ais_score,
                    f"Kinetic escalation → defense premium on {symbol}",
                    sym_data,
                ))

        # --- Safe-haven longs ---
        for symbol in LONG_SAFEHAVEN:
            sym_data = market_data.get(symbol, {})
            if not sym_data:
                continue
            chg = _pct_change(sym_data)
            rvol = _relative_volume(sym_data)
            confidence = (
                0.36
                + escalation * 0.22
                + max(chg / 100.0, 0.0) * 0.14
                + min(rvol, 2.0) * 0.04
            )
            if confidence < 0.48:
                continue
            ideas.append(self._build_candidate(
                symbol, "long", confidence, escalation, ais_score,
                f"Safe-haven demand on Hormuz escalation → long {symbol}",
                sym_data,
            ))

        return ideas

    # ------------------------------------------------------------------
    # Short leg: airlines / shipping / leisure
    # ------------------------------------------------------------------

    def _short_candidates(
        self,
        escalation: float,
        ais_score: float,
        market_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        ideas: list[dict[str, Any]] = []

        short_groups: list[tuple[list[str], str]] = [
            (SHORT_AIRLINES,  "airline fuel cost spike / route closure"),
            (SHORT_SHIPPING,  "shipping rerouting cost + volume loss"),
            (SHORT_LEISURE,   "leisure demand shock from conflict zone"),
        ]

        for group, label in short_groups:
            for symbol in group:
                sym_data = market_data.get(symbol, {})
                if not sym_data:
                    continue
                chg = _pct_change(sym_data)
                rvol = _relative_volume(sym_data)

                # Prefer symbols already weakening (bearish momentum alignment)
                momentum_bonus = max(-chg / 100.0, 0.0) * 0.16

                confidence = (
                    0.38
                    + escalation * 0.24
                    + ais_score * 0.10
                    + momentum_bonus
                    + min(rvol, 2.5) * 0.04
                )
                if confidence < 0.48:
                    continue

                ideas.append(self._build_candidate(
                    symbol, "short", confidence, escalation, ais_score,
                    f"Hormuz disruption → short {symbol} ({label})",
                    sym_data,
                ))

        return ideas

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def scan_watchlist(
        self,
        market_data: dict[str, dict[str, Any]] | None = None,
        scorecard: dict[str, Any] | None = None,
        osint_data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if market_data is None:
            return []

        osint = osint_data or {}
        escalation = _composite_escalation(osint, market_data, scorecard, self._params)

        logger.debug(
            "HormuzOSINT composite_escalation=%.3f (min=%.2f)",
            escalation,
            self._params["min_escalation_score"],
        )

        if escalation < self._params["min_escalation_score"]:
            return []

        ais_score = _maritime_disruption_score(osint)
        dark_score = _dark_vessel_score(osint, self._params)
        toll_score = _toll_cost_score(osint, self._params)

        ideas: list[dict[str, Any]] = []
        maritime_overlay = dict((osint.get("maritime") or {}).get("hormuz", {}))
        maritime_overlay["_dark_vessel_score"] = dark_score
        maritime_overlay["_toll_cost_score"] = toll_score
        enriched_market_data = {
            symbol: ({**sym_data, "_hormuz_maritime": maritime_overlay} if isinstance(sym_data, dict) else sym_data)
            for symbol, sym_data in market_data.items()
        }
        ideas.extend(self._long_candidates(escalation, ais_score, enriched_market_data))

        # Only generate short signals when AIS confirms traffic disruption
        if ais_score >= self._params["maritime_disruption_threshold"] or escalation >= 0.60:
            ideas.extend(self._short_candidates(escalation, ais_score, enriched_market_data))

        ideas.sort(key=lambda x: x["confidence_score"], reverse=True)
        return ideas[: self._params["max_candidates"]]


# ---------------------------------------------------------------------------
# GS-standard evaluate function
# ---------------------------------------------------------------------------


def evaluate_hormuz_osint_geopolitical(
    strat: dict[str, Any] | None = None,
    market_data: dict[str, dict[str, Any]] | None = None,
    scorecard: dict[str, Any] | None = None,
    osint_data: dict[str, Any] | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """Entry point called by GS strategy orchestrator."""
    strategy = HormuzOsintGeopoliticalStrategy(
        params=dict((strat or {}).get("params", {}))
    )
    return strategy.scan_watchlist(
        market_data=market_data,
        scorecard=scorecard,
        osint_data=osint_data,
    )
