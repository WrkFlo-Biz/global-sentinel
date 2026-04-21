"""Cross-asset signal detection for Global Sentinel.

Detects signals from bonds, currencies, commodities that lead equity moves.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


EVENT_PROPAGATION_LIBRARY: dict[str, dict[str, Any]] = {
    "bond_equity_divergence": {
        "transmission_path": ["bonds", "yields", "equities", "futures", "options"],
        "strategy_tags": ["gold_safe_haven", "wall_street_vol", "petro_inflation"],
        "baskets": [
            {"asset_class": "bond", "instrument_type": "etf", "symbols": ["TLT", "IEF"], "bias": "long", "rationale": "duration bid as growth expectations fade"},
            {"asset_class": "yield", "instrument_type": "cash_yield", "symbols": ["US10Y"], "bias": "short", "rationale": "10Y yield compression confirms risk-off"},
            {"asset_class": "equity", "instrument_type": "etf", "symbols": ["SPY", "QQQ"], "bias": "short", "rationale": "equities lagging bond market repricing"},
            {"asset_class": "future", "instrument_type": "index_future", "symbols": ["ES", "NQ"], "bias": "short", "rationale": "index futures catch up to bond signal"},
            {"asset_class": "option", "instrument_type": "put_option", "symbols": ["SPY_put", "QQQ_put"], "bias": "long", "rationale": "defined-risk hedge into equity catch-down"},
        ],
    },
    "credit_spread_widening": {
        "transmission_path": ["bonds", "yields", "equities", "options"],
        "strategy_tags": ["wall_street_vol", "gold_safe_haven", "em_capital_flight"],
        "baskets": [
            {"asset_class": "bond", "instrument_type": "etf", "symbols": ["TLT"], "bias": "long", "rationale": "flight to quality as credit spreads widen"},
            {"asset_class": "yield", "instrument_type": "cash_yield", "symbols": ["HYG_OAS", "US10Y"], "bias": "long_credit_spread", "rationale": "credit stress is the leading condition"},
            {"asset_class": "equity", "instrument_type": "etf", "symbols": ["HYG", "XLF", "SPY"], "bias": "short", "rationale": "credit-sensitive equities underperform"},
            {"asset_class": "option", "instrument_type": "put_option", "symbols": ["HYG_put", "SPY_put"], "bias": "long", "rationale": "express widening credit stress with convexity"},
        ],
    },
    "dxy_em_pressure": {
        "transmission_path": ["fx", "equities", "futures", "options"],
        "strategy_tags": ["em_capital_flight", "asia_energy_cascade", "china_oil_import_shock"],
        "baskets": [
            {"asset_class": "fx", "instrument_type": "dollar_index", "symbols": ["DXY", "UUP"], "bias": "long", "rationale": "broad USD strength pressures imported-risk assets"},
            {"asset_class": "equity", "instrument_type": "etf", "symbols": ["EEM", "INDA", "EWY"], "bias": "short", "rationale": "EM importers and high-beta Asia lag dollar squeeze"},
            {"asset_class": "future", "instrument_type": "fx_future", "symbols": ["DX"], "bias": "long", "rationale": "futures reinforce dollar regime"},
            {"asset_class": "option", "instrument_type": "put_option", "symbols": ["EEM_put", "INDA_put"], "bias": "long", "rationale": "lagged EM repricing favors downside convexity"},
        ],
    },
    "jpy_carry_unwind": {
        "transmission_path": ["fx", "equities", "bonds", "futures", "options"],
        "strategy_tags": ["wall_street_vol", "gold_safe_haven", "asia_energy_cascade"],
        "baskets": [
            {"asset_class": "fx", "instrument_type": "currency_etf", "symbols": ["FXY"], "bias": "long", "rationale": "yen strength confirms carry unwind"},
            {"asset_class": "equity", "instrument_type": "etf", "symbols": ["IWM", "QQQ"], "bias": "short", "rationale": "high-beta equities are most exposed to carry unwind"},
            {"asset_class": "bond", "instrument_type": "etf", "symbols": ["TLT"], "bias": "long", "rationale": "rates rally in unwind regimes"},
            {"asset_class": "future", "instrument_type": "index_future", "symbols": ["ES", "NQ"], "bias": "short", "rationale": "futures reprice before cash"},
            {"asset_class": "option", "instrument_type": "call_option", "symbols": ["VIX_call"], "bias": "long", "rationale": "carry unwind tends to steepen vol response"},
        ],
    },
    "cad_oil_confirmation": {
        "transmission_path": ["fx", "futures", "equities", "options"],
        "strategy_tags": ["oil_momentum_intraday", "refining_crack_spread", "canadian_oil_premium", "shipping_rate_explosion"],
        "baskets": [
            {"asset_class": "fx", "instrument_type": "currency_etf", "symbols": ["FXC"], "bias": "long", "rationale": "CAD strength confirms commodity bid"},
            {"asset_class": "future", "instrument_type": "energy_future", "symbols": ["CL", "BZ"], "bias": "long", "rationale": "crude futures lead the cycle"},
            {"asset_class": "equity", "instrument_type": "etf", "symbols": ["XLE", "OIH", "XOP"], "bias": "long", "rationale": "energy equities lag commodity confirmation"},
            {"asset_class": "option", "instrument_type": "call_option", "symbols": ["XLE_call"], "bias": "long", "rationale": "options express continued oil beta"},
        ],
    },
    "oil_airline_fuel_cascade": {
        "transmission_path": ["futures", "equities", "fx", "options"],
        "strategy_tags": ["airline_short", "jet_fuel_squeeze", "supply_shock_pairs", "us_premarket_gap"],
        "baskets": [
            {"asset_class": "future", "instrument_type": "energy_future", "symbols": ["CL", "RB"], "bias": "long", "rationale": "crude and gasoline lead the fuel-cost cascade"},
            {"asset_class": "equity", "instrument_type": "etf", "symbols": ["JETS", "UAL", "AAL"], "bias": "short", "rationale": "airlines absorb fuel shock with lag"},
            {"asset_class": "fx", "instrument_type": "currency_etf", "symbols": ["FXC"], "bias": "long", "rationale": "commodity FX confirms oil shock persistence"},
            {"asset_class": "option", "instrument_type": "put_option", "symbols": ["JETS_put", "UAL_put"], "bias": "long", "rationale": "downside convexity fits lagged airline squeeze"},
        ],
    },
    "gold_safe_haven_cascade": {
        "transmission_path": ["equities", "futures", "options"],
        "strategy_tags": ["gold_safe_haven", "petro_inflation", "inflation_hedge"],
        "baskets": [
            {"asset_class": "equity", "instrument_type": "etf", "symbols": ["GLD", "GDX", "SLV"], "bias": "long", "rationale": "bullion leads miners and silver"},
            {"asset_class": "future", "instrument_type": "metal_future", "symbols": ["GC", "SI"], "bias": "long", "rationale": "metals futures confirm safe-haven impulse"},
            {"asset_class": "option", "instrument_type": "call_option", "symbols": ["GLD_call", "GDX_call"], "bias": "long", "rationale": "defined-risk participation in haven bid"},
        ],
    },
    "nat_gas_food_chain_cascade": {
        "transmission_path": ["futures", "equities", "options"],
        "strategy_tags": ["fertilizer_food_chain", "petro_inflation"],
        "baskets": [
            {"asset_class": "future", "instrument_type": "energy_future", "symbols": ["NG"], "bias": "long", "rationale": "nat-gas drives fertilizer input costs"},
            {"asset_class": "equity", "instrument_type": "etf", "symbols": ["MOS", "NTR", "DBA"], "bias": "long", "rationale": "fertilizer and food chain follow the gas shock"},
            {"asset_class": "option", "instrument_type": "call_option", "symbols": ["MOS_call", "DBA_call"], "bias": "long", "rationale": "convex exposure to the food-input chain"},
        ],
    },
    "vix_backwardation": {
        "transmission_path": ["futures", "options", "equities", "bonds"],
        "strategy_tags": ["vix_spike_scalp", "wall_street_vol", "gold_safe_haven"],
        "baskets": [
            {"asset_class": "future", "instrument_type": "vol_future", "symbols": ["VX"], "bias": "long", "rationale": "front-end vol stress leads cash volatility"},
            {"asset_class": "option", "instrument_type": "call_option", "symbols": ["UVXY_call", "SPY_put"], "bias": "long", "rationale": "vol convexity responds first"},
            {"asset_class": "equity", "instrument_type": "etf", "symbols": ["UVXY", "SQQQ"], "bias": "long", "rationale": "inverse and vol ETFs capture acute fear"},
            {"asset_class": "bond", "instrument_type": "etf", "symbols": ["TLT"], "bias": "long", "rationale": "duration gets the risk-off bid"},
        ],
    },
    "es_spy_basis_divergence": {
        "transmission_path": ["futures", "equities", "options"],
        "strategy_tags": ["us_premarket_gap", "oil_gap_persistence", "wall_street_vol"],
        "baskets": [
            {"asset_class": "future", "instrument_type": "index_future", "symbols": ["ES"], "bias": "lead_signal", "rationale": "overnight futures lead cash reprice"},
            {"asset_class": "equity", "instrument_type": "etf", "symbols": ["SPY"], "bias": "follow_signal", "rationale": "cash index lags basis divergence"},
            {"asset_class": "option", "instrument_type": "put_option", "symbols": ["SPY_put"], "bias": "long", "rationale": "basis gaps favor short-dated hedges"},
        ],
    },
    "cl_xle_basis_divergence": {
        "transmission_path": ["futures", "equities", "options", "fx"],
        "strategy_tags": ["oil_momentum_intraday", "refining_crack_spread", "canadian_oil_premium"],
        "baskets": [
            {"asset_class": "future", "instrument_type": "energy_future", "symbols": ["CL"], "bias": "lead_signal", "rationale": "crude future leads equity energy complex"},
            {"asset_class": "equity", "instrument_type": "etf", "symbols": ["XLE", "OIH"], "bias": "follow_signal", "rationale": "energy ETFs lag underlying crude"},
            {"asset_class": "option", "instrument_type": "call_option", "symbols": ["XLE_call"], "bias": "long", "rationale": "follow-through often lands in options next"},
            {"asset_class": "fx", "instrument_type": "currency_etf", "symbols": ["FXC"], "bias": "long", "rationale": "commodity FX confirms the commodity complex"},
        ],
    },
}


class CrossAssetSignals:
    """Detects cross-asset signals that lead equity moves."""

    # Thresholds
    DXY_STRENGTH_THRESHOLD = 106.0
    DIVERGENCE_BPS_THRESHOLD = 15.0
    VIX_BACKWARDATION_RATIO = 1.0  # front/back > 1.0 = backwardation

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
        # History tracking for lag detection and cascade timing
        self._bond_history: list[dict[str, Any]] = []
        self._currency_history: list[dict[str, Any]] = []
        self._commodity_history: list[dict[str, Any]] = []
        self._vix_history: list[dict[str, Any]] = []
        self._futures_history: list[dict[str, Any]] = []
        self._last_scan: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def scan(
        self,
        bridge_results: dict[str, Any] | None = None,
        market_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run full cross-asset signal scan.

        Args:
            bridge_results: Optional output from ingestion bridges.
            market_data: Dict keyed by symbol with price/change data.
                Expected keys include TLT, SPY, HYG, DXY, USDJPY, USDCAD,
                CL, GLD, SLV, NG, VIX_F1, VIX_F2, ES, XLE, INDA, etc.

        Returns:
            Dictionary with bond_equity_signals, currency_signals,
            commodity_cascade_signals, vix_term_structure, and
            futures_equity_basis.
        """
        if market_data is None:
            market_data = {}

        # Merge any relevant bridge data into market_data
        if bridge_results:
            for key in ("market_snapshot", "prices", "quotes"):
                if key in bridge_results and isinstance(bridge_results[key], dict):
                    for sym, val in bridge_results[key].items():
                        market_data.setdefault(sym, val)

        bond_signals = self._bond_equity_divergence(market_data)
        currency_signals = self._currency_signals(market_data)
        commodity_signals = self._commodity_cascade(market_data)
        vix_structure = self._vix_term_structure(market_data)
        basis_signals = self._futures_equity_basis(market_data)
        propagation_router = self._build_propagation_router(
            bond_signals=bond_signals,
            currency_signals=currency_signals,
            commodity_signals=commodity_signals,
            vix_structure=vix_structure,
            basis_signals=basis_signals,
        )

        self._last_scan = {
            "bond_equity_signals": bond_signals,
            "currency_signals": currency_signals,
            "commodity_cascade_signals": commodity_signals,
            "vix_term_structure": vix_structure,
            "futures_equity_basis": basis_signals,
            "cross_asset_propagation_map": propagation_router["cross_asset_propagation_map"],
            "event_to_basket_routes": propagation_router["event_to_basket_routes"],
            "ranked_baskets": propagation_router["ranked_baskets"],
            "asset_class_links": propagation_router["asset_class_links"],
            "propagation_router_summary": propagation_router["summary"],
            "timestamp": time.time(),
        }
        return self._last_scan

    # ------------------------------------------------------------------
    # Sub-methods
    # ------------------------------------------------------------------

    def _bond_equity_divergence(self, market_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Detect bond-equity divergences.

        - If 10Y yield drops (TLT up) but SPY hasn't fallen -> SHORT signal.
        - If credit spreads widen (HYG down, TLT up) -> risk-off approaching.
        """
        signals: list[dict[str, Any]] = []

        tlt = self._extract(market_data, "TLT")
        spy = self._extract(market_data, "SPY")
        hyg = self._extract(market_data, "HYG")

        # TLT up (yields dropping) but SPY flat/up -> divergence
        if tlt and spy:
            tlt_chg = tlt.get("change_pct", 0.0)
            spy_chg = spy.get("change_pct", 0.0)
            if tlt_chg > 0.3 and spy_chg > -0.1:
                confidence = min(0.85, 0.5 + abs(tlt_chg - spy_chg) * 0.1)
                signals.append({
                    "signal": "bond_equity_divergence",
                    "description": (
                        f"TLT +{tlt_chg:.2f}% (yields dropping) but SPY "
                        f"{spy_chg:+.2f}% hasn't repriced risk-off yet"
                    ),
                    "action": "SHORT SPY / hedge long book",
                    "confidence": round(confidence, 2),
                })
                self._bond_history.append({
                    "type": "yield_divergence",
                    "tlt_chg": tlt_chg,
                    "spy_chg": spy_chg,
                    "ts": time.time(),
                })

        # Credit spread widening: HYG down + TLT up
        if hyg and tlt:
            hyg_chg = hyg.get("change_pct", 0.0)
            tlt_chg = tlt.get("change_pct", 0.0)
            if hyg_chg < -0.2 and tlt_chg > 0.2:
                spread_move = abs(hyg_chg) + abs(tlt_chg)
                confidence = min(0.90, 0.55 + spread_move * 0.08)
                signals.append({
                    "signal": "credit_spread_widening",
                    "description": (
                        f"HYG {hyg_chg:+.2f}% + TLT {tlt_chg:+.2f}%: "
                        f"credit spreads widening, risk-off approaching"
                    ),
                    "action": "Reduce risk exposure, add hedges",
                    "confidence": round(confidence, 2),
                })
                self._bond_history.append({
                    "type": "credit_spread",
                    "hyg_chg": hyg_chg,
                    "tlt_chg": tlt_chg,
                    "ts": time.time(),
                })

        return signals

    def _currency_signals(self, market_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Detect currency-driven equity signals.

        - USD strength (DXY > 106) -> EM weakness follows 1-2 day lag.
        - JPY strength -> risk-off (yen carry trade unwinding).
        - CAD strength -> oil confirmation (commodity currency).
        """
        signals: list[dict[str, Any]] = []

        dxy = self._extract(market_data, "DXY")
        usdjpy = self._extract(market_data, "USDJPY")
        usdcad = self._extract(market_data, "USDCAD")

        # DXY strength -> EM lag
        if dxy:
            dxy_price = dxy.get("price", dxy.get("last", 0.0))
            dxy_chg = dxy.get("change_pct", 0.0)
            if dxy_price > self.DXY_STRENGTH_THRESHOLD:
                confidence = min(0.80, 0.50 + (dxy_price - self.DXY_STRENGTH_THRESHOLD) * 0.05)
                signals.append({
                    "signal": "dxy_em_pressure",
                    "description": (
                        f"DXY at {dxy_price:.1f} (>{self.DXY_STRENGTH_THRESHOLD}): "
                        f"EM weakness expected with 1-2 day lag"
                    ),
                    "action": "SHORT INDA/EEM, lag ~2d",
                    "confidence": round(confidence, 2),
                })
            if dxy_chg > 0.5:
                signals.append({
                    "signal": "dxy_breakout",
                    "description": f"DXY surging {dxy_chg:+.2f}%: broad dollar strength",
                    "action": "SHORT EM equities, commodity exporters",
                    "confidence": round(min(0.75, 0.45 + dxy_chg * 0.06), 2),
                })

        # JPY strength -> risk-off via carry unwind
        if usdjpy:
            usdjpy_chg = usdjpy.get("change_pct", 0.0)
            # USDJPY dropping = JPY strengthening
            if usdjpy_chg < -0.4:
                confidence = min(0.82, 0.50 + abs(usdjpy_chg) * 0.08)
                signals.append({
                    "signal": "jpy_carry_unwind",
                    "description": (
                        f"USDJPY {usdjpy_chg:+.2f}%: yen strengthening, "
                        f"carry trade unwinding -> risk-off"
                    ),
                    "action": "Reduce risk, expect vol spike",
                    "confidence": round(confidence, 2),
                })

        # CAD strength -> oil confirmation
        if usdcad:
            usdcad_chg = usdcad.get("change_pct", 0.0)
            # USDCAD dropping = CAD strengthening
            if usdcad_chg < -0.3:
                signals.append({
                    "signal": "cad_oil_confirmation",
                    "description": (
                        f"USDCAD {usdcad_chg:+.2f}%: CAD strength confirms "
                        f"oil bid, commodity cycle intact"
                    ),
                    "action": "LONG XLE/OIH if not positioned",
                    "confidence": round(min(0.70, 0.40 + abs(usdcad_chg) * 0.06), 2),
                })

        if signals:
            self._currency_history.append({
                "count": len(signals),
                "ts": time.time(),
            })

        return signals

    def _commodity_cascade(self, market_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Detect commodity cascade patterns.

        Cascades propagate with predictable lag:
        - Oil -> gasoline -> jet fuel -> airlines
        - Gold -> miners -> silver
        - Nat gas -> fertilizer -> food commodities

        Goal: get in at stage 2/3 BEFORE cascade completes.
        """
        signals: list[dict[str, Any]] = []

        # --- Oil cascade: CL -> gasoline (UGA) -> airlines (JETS) ---
        cl = self._extract(market_data, "CL")
        uga = self._extract(market_data, "UGA")
        jets = self._extract(market_data, "JETS")

        if cl:
            cl_chg = cl.get("change_pct", 0.0)
            if abs(cl_chg) > 1.5:
                stages_hit = ["CL"]
                lag = "stage_1"
                if uga:
                    uga_chg = uga.get("change_pct", 0.0)
                    if abs(uga_chg) > 0.5:
                        stages_hit.append("UGA")
                        lag = "stage_2"
                if jets:
                    jets_chg = jets.get("change_pct", 0.0)
                    if abs(jets_chg) > 0.3:
                        stages_hit.append("JETS")
                        lag = "stage_3"

                if lag in ("stage_1", "stage_2"):
                    direction = "up" if cl_chg > 0 else "down"
                    action = (
                        f"{'SHORT' if direction == 'up' else 'LONG'} JETS "
                        f"(airlines lag oil {direction})"
                    )
                    signals.append({
                        "signal": "oil_airline_fuel_cascade",
                        "primary": "CL",
                        "secondary": " -> ".join(stages_hit),
                        "lag_status": lag,
                        "action": action,
                        "confidence": round(0.76 if lag == "stage_1" else 0.68, 2),
                    })

        # --- Gold cascade: GLD -> GDX (miners) -> SLV ---
        gld = self._extract(market_data, "GLD")
        gdx = self._extract(market_data, "GDX")
        slv = self._extract(market_data, "SLV")

        if gld:
            gld_chg = gld.get("change_pct", 0.0)
            if abs(gld_chg) > 0.8:
                stages_hit = ["GLD"]
                lag = "stage_1"
                if gdx:
                    gdx_chg = gdx.get("change_pct", 0.0)
                    if abs(gdx_chg) > 0.5:
                        stages_hit.append("GDX")
                        lag = "stage_2"
                if slv:
                    slv_chg = slv.get("change_pct", 0.0)
                    if abs(slv_chg) > 0.3:
                        stages_hit.append("SLV")
                        lag = "stage_3"

                if lag in ("stage_1", "stage_2"):
                    direction = "up" if gld_chg > 0 else "down"
                    next_target = "GDX" if lag == "stage_1" else "SLV"
                    action = f"LONG {next_target}" if direction == "up" else f"SHORT {next_target}"
                    signals.append({
                        "signal": "gold_safe_haven_cascade",
                        "primary": "GLD",
                        "secondary": " -> ".join(stages_hit),
                        "lag_status": lag,
                        "action": f"{action} (gold cascade {direction})",
                        "confidence": round(0.72 if lag == "stage_1" else 0.64, 2),
                    })

        # --- Nat gas cascade: NG -> fertilizer (MOS/NTR) -> food (DBA) ---
        ng = self._extract(market_data, "NG")
        mos = self._extract(market_data, "MOS")
        dba = self._extract(market_data, "DBA")

        if ng:
            ng_chg = ng.get("change_pct", 0.0)
            if abs(ng_chg) > 2.0:
                stages_hit = ["NG"]
                lag = "stage_1"
                if mos:
                    mos_chg = mos.get("change_pct", 0.0)
                    if abs(mos_chg) > 0.5:
                        stages_hit.append("MOS")
                        lag = "stage_2"
                if dba:
                    dba_chg = dba.get("change_pct", 0.0)
                    if abs(dba_chg) > 0.3:
                        stages_hit.append("DBA")
                        lag = "stage_3"

                if lag in ("stage_1", "stage_2"):
                    direction = "up" if ng_chg > 0 else "down"
                    next_target = "MOS" if lag == "stage_1" else "DBA"
                    action = f"LONG {next_target}" if direction == "up" else f"SHORT {next_target}"
                    signals.append({
                        "signal": "nat_gas_food_chain_cascade",
                        "primary": "NG",
                        "secondary": " -> ".join(stages_hit),
                        "lag_status": lag,
                        "action": f"{action} (nat gas cascade {direction})",
                        "confidence": round(0.70 if lag == "stage_1" else 0.62, 2),
                    })

        if signals:
            self._commodity_history.append({
                "cascades": len(signals),
                "ts": time.time(),
            })

        return signals

    def _vix_term_structure(self, market_data: dict[str, Any]) -> dict[str, Any]:
        """Analyze VIX term structure.

        - Backwardation (front > back) = acute fear, vol spike coming.
        - Contango (front < back) = priced but not panicking.
        - Transition contango -> backwardation = trade signal for UVXY.
        """
        vix_f1 = self._extract(market_data, "VIX_F1")
        vix_f2 = self._extract(market_data, "VIX_F2")

        if not vix_f1 or not vix_f2:
            return {
                "structure": "unknown",
                "front_month": None,
                "back_month": None,
                "signal": "Insufficient VIX futures data",
            }

        front = vix_f1.get("price", vix_f1.get("last", 0.0))
        back = vix_f2.get("price", vix_f2.get("last", 0.0))

        if back == 0:
            return {
                "structure": "unknown",
                "front_month": front,
                "back_month": back,
                "signal": "Invalid back-month price",
            }

        ratio = front / back
        structure = "backwardation" if ratio > self.VIX_BACKWARDATION_RATIO else "contango"

        # Check for transition from history
        signal = ""
        was_contango = False
        if self._vix_history:
            last_structure = self._vix_history[-1].get("structure", "unknown")
            was_contango = last_structure == "contango"

        if structure == "backwardation":
            if was_contango:
                signal = "TRANSITION contango->backwardation: LONG UVXY, vol spike imminent"
            else:
                signal = f"Backwardation ({ratio:.3f}): acute fear, expect vol spike"
        else:
            spread_pct = (back - front) / front * 100 if front > 0 else 0
            if spread_pct < 2.0:
                signal = f"Flat contango ({spread_pct:.1f}%): approaching flip, watch closely"
            else:
                signal = f"Contango ({spread_pct:.1f}% spread): market priced, no panic"

        self._vix_history.append({
            "structure": structure,
            "ratio": ratio,
            "front": front,
            "back": back,
            "ts": time.time(),
        })

        return {
            "structure": structure,
            "front_month": front,
            "back_month": back,
            "signal": signal,
        }

    def _futures_equity_basis(self, market_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Detect futures-equity basis divergences.

        - If ES futures diverge from SPY -> overnight flow signal.
        - If CL futures spike but XLE flat -> XLE lagging, buy.
        """
        signals: list[dict[str, Any]] = []

        # ES vs SPY
        es = self._extract(market_data, "ES")
        spy = self._extract(market_data, "SPY")

        if es and spy:
            es_chg = es.get("change_pct", 0.0)
            spy_chg = spy.get("change_pct", 0.0)
            divergence_bps = abs(es_chg - spy_chg) * 100
            if divergence_bps > self.DIVERGENCE_BPS_THRESHOLD:
                direction = "up" if es_chg > spy_chg else "down"
                action = (
                    f"LONG SPY (futures leading {direction})"
                    if es_chg > spy_chg
                    else f"SHORT SPY (futures leading {direction})"
                )
                signals.append({
                    "signal": "es_spy_basis_divergence",
                    "futures_symbol": "ES",
                    "equity_symbol": "SPY",
                    "divergence_bps": round(divergence_bps, 1),
                    "action": action,
                    "confidence": round(min(0.86, 0.45 + (divergence_bps / 100.0) * 0.2), 2),
                })

        # CL vs XLE
        cl = self._extract(market_data, "CL")
        xle = self._extract(market_data, "XLE")

        if cl and xle:
            cl_chg = cl.get("change_pct", 0.0)
            xle_chg = xle.get("change_pct", 0.0)
            divergence_bps = abs(cl_chg - xle_chg) * 100
            if divergence_bps > self.DIVERGENCE_BPS_THRESHOLD:
                if cl_chg > xle_chg + 0.5:
                    signals.append({
                        "signal": "cl_xle_basis_divergence",
                        "futures_symbol": "CL",
                        "equity_symbol": "XLE",
                        "divergence_bps": round(divergence_bps, 1),
                        "action": "LONG XLE (lagging oil spike)",
                        "confidence": round(min(0.82, 0.42 + (divergence_bps / 100.0) * 0.2), 2),
                    })
                elif xle_chg > cl_chg + 0.5:
                    signals.append({
                        "signal": "cl_xle_basis_divergence",
                        "futures_symbol": "CL",
                        "equity_symbol": "XLE",
                        "divergence_bps": round(divergence_bps, 1),
                        "action": "SHORT XLE (over-extended vs crude)",
                        "confidence": round(min(0.82, 0.42 + (divergence_bps / 100.0) * 0.2), 2),
                    })

        if signals:
            self._futures_history.append({
                "count": len(signals),
                "ts": time.time(),
            })

        return signals

    def _build_propagation_router(
        self,
        *,
        bond_signals: list[dict[str, Any]],
        currency_signals: list[dict[str, Any]],
        commodity_signals: list[dict[str, Any]],
        vix_structure: dict[str, Any],
        basis_signals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        signal_rows: list[dict[str, Any]] = []
        for bucket, rows in (
            ("bond_equity_signals", bond_signals),
            ("currency_signals", currency_signals),
            ("commodity_cascade_signals", commodity_signals),
            ("futures_equity_basis", basis_signals),
        ):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                signal_name = str(row.get("signal") or "").strip()
                if not signal_name:
                    continue
                signal_rows.append({
                    "source_bucket": bucket,
                    "signal": signal_name,
                    "payload": row,
                })

        if str(vix_structure.get("structure", "")).lower() == "backwardation":
            ratio = self._safe_ratio(
                vix_structure.get("front_month"),
                vix_structure.get("back_month"),
            )
            signal_rows.append({
                "source_bucket": "vix_term_structure",
                "signal": "vix_backwardation",
                "payload": {
                    "signal": "vix_backwardation",
                    "confidence": round(min(0.9, 0.55 + max(0.0, ratio - 1.0) * 1.5), 2),
                    "ratio": round(ratio, 4),
                    "action": vix_structure.get("signal"),
                },
            })

        cross_asset_propagation_map: list[dict[str, Any]] = []
        event_to_basket_routes: list[dict[str, Any]] = []
        ranked_baskets: list[dict[str, Any]] = []
        edge_rollup: dict[tuple[str, str], dict[str, Any]] = {}

        for row in signal_rows:
            signal_name = row["signal"]
            source_bucket = row["source_bucket"]
            payload = row["payload"]
            template = EVENT_PROPAGATION_LIBRARY.get(signal_name)
            if not template:
                continue

            confidence = self._route_confidence(payload)
            transmission_path = list(template.get("transmission_path") or [])
            strategy_tags = list(template.get("strategy_tags") or [])
            baskets = list(template.get("baskets") or [])
            local_links = self._build_asset_links(
                signal=signal_name,
                transmission_path=transmission_path,
                confidence=confidence,
            )
            self._merge_asset_link_rollup(edge_rollup, local_links)

            routed_baskets: list[dict[str, Any]] = []
            for basket in baskets:
                routed = dict(basket)
                basket_score = self._basket_route_score(
                    signal=signal_name,
                    confidence=confidence,
                    basket=routed,
                    transmission_path=transmission_path,
                )
                routed.update({
                    "signal": signal_name,
                    "source_bucket": source_bucket,
                    "route_score": basket_score,
                    "confidence": round(confidence, 4),
                })
                routed_baskets.append(routed)

            routed_baskets.sort(
                key=lambda item: (
                    -float(item.get("route_score", 0.0)),
                    -float(item.get("confidence", 0.0)),
                    str(item.get("asset_class", "")),
                )
            )
            for i, basket in enumerate(routed_baskets, start=1):
                basket["rank_within_signal"] = i
                flat_row = {
                    "signal": signal_name,
                    "source_bucket": source_bucket,
                    "transmission_path": transmission_path,
                    "strategy_tags": strategy_tags,
                    "rank_within_signal": i,
                    **basket,
                }
                event_to_basket_routes.append(flat_row)
                ranked_baskets.append(flat_row)

            cross_asset_propagation_map.append({
                "signal": signal_name,
                "source_bucket": source_bucket,
                "confidence": round(confidence, 4),
                "transmission_path": transmission_path,
                "strategy_tags": strategy_tags,
                "asset_class_links": local_links,
                "routed_baskets": routed_baskets,
                "trigger_context": {
                    key: payload.get(key)
                    for key in ("action", "description", "lag_status", "divergence_bps", "ratio")
                    if key in payload
                },
            })

        ranked_baskets.sort(
            key=lambda item: (
                -float(item.get("route_score", 0.0)),
                -float(item.get("confidence", 0.0)),
                str(item.get("signal", "")),
            )
        )
        for i, basket in enumerate(ranked_baskets, start=1):
            basket["global_rank"] = i

        asset_class_links = sorted(
            (
                {
                    "from_asset_class": edge["from_asset_class"],
                    "to_asset_class": edge["to_asset_class"],
                    "edge_weight": round(edge["weight_sum"], 4),
                    "avg_confidence": round(edge["confidence_sum"] / max(edge["count"], 1), 4),
                    "signal_count": edge["count"],
                    "signals": sorted(edge["signals"]),
                }
                for edge in edge_rollup.values()
            ),
            key=lambda row: (-float(row["edge_weight"]), -int(row["signal_count"]), row["from_asset_class"]),
        )

        return {
            "cross_asset_propagation_map": cross_asset_propagation_map,
            "event_to_basket_routes": event_to_basket_routes,
            "ranked_baskets": ranked_baskets,
            "asset_class_links": asset_class_links,
            "summary": {
                "signal_count": len(cross_asset_propagation_map),
                "routed_basket_count": len(ranked_baskets),
                "asset_link_count": len(asset_class_links),
                "top_signal": ranked_baskets[0]["signal"] if ranked_baskets else None,
                "top_asset_class": ranked_baskets[0].get("asset_class") if ranked_baskets else None,
            },
        }

    def _build_asset_links(
        self,
        *,
        signal: str,
        transmission_path: list[str],
        confidence: float,
    ) -> list[dict[str, Any]]:
        links: list[dict[str, Any]] = []
        normalized = [self._normalize_asset_class(x) for x in transmission_path if str(x).strip()]
        if len(normalized) < 2:
            return links
        for index in range(len(normalized) - 1):
            src = normalized[index]
            dst = normalized[index + 1]
            if not src or not dst:
                continue
            links.append({
                "signal": signal,
                "from_asset_class": src,
                "to_asset_class": dst,
                "path_index": index,
                "edge_weight": round(max(0.05, confidence * (1.0 - index * 0.08)), 4),
                "confidence": round(confidence, 4),
            })
        return links

    @staticmethod
    def _merge_asset_link_rollup(
        rollup: dict[tuple[str, str], dict[str, Any]],
        links: list[dict[str, Any]],
    ) -> None:
        for row in links:
            key = (str(row.get("from_asset_class")), str(row.get("to_asset_class")))
            existing = rollup.setdefault(
                key,
                {
                    "from_asset_class": key[0],
                    "to_asset_class": key[1],
                    "count": 0,
                    "weight_sum": 0.0,
                    "confidence_sum": 0.0,
                    "signals": set(),
                },
            )
            existing["count"] += 1
            existing["weight_sum"] += float(row.get("edge_weight", 0.0))
            existing["confidence_sum"] += float(row.get("confidence", 0.0))
            existing["signals"].add(str(row.get("signal", "")))

    def _basket_route_score(
        self,
        *,
        signal: str,
        confidence: float,
        basket: dict[str, Any],
        transmission_path: list[str],
    ) -> float:
        class_weight = {
            "option": 1.0,
            "future": 0.95,
            "equity": 0.90,
            "fx": 0.86,
            "bond": 0.84,
            "yield": 0.82,
        }
        bias_bonus = {
            "lead_signal": 0.09,
            "follow_signal": 0.04,
            "long_credit_spread": 0.05,
        }
        asset_class = self._normalize_asset_class(basket.get("asset_class"))
        path = [self._normalize_asset_class(x) for x in transmission_path if str(x).strip()]
        stage_idx = path.index(asset_class) if asset_class in path else len(path)
        stage_bonus = max(0.0, 0.12 - stage_idx * 0.02)
        symbols = basket.get("symbols") or []
        breadth_bonus = min(len(symbols), 3) * 0.02
        bias = str(basket.get("bias") or "").lower()
        score = (
            confidence * 0.70
            + class_weight.get(asset_class, 0.78) * 0.20
            + stage_bonus
            + bias_bonus.get(bias, 0.0)
            + breadth_bonus
        )
        if signal in {"vix_backwardation", "credit_spread_widening"} and asset_class in {"option", "future"}:
            score += 0.03
        return round(min(1.5, max(0.0, score)), 4)

    @staticmethod
    def _route_confidence(signal_payload: dict[str, Any]) -> float:
        confidence = signal_payload.get("confidence")
        if isinstance(confidence, (int, float)):
            return max(0.05, min(float(confidence), 0.99))

        lag_status = str(signal_payload.get("lag_status", "")).lower()
        if lag_status == "stage_1":
            return 0.74
        if lag_status == "stage_2":
            return 0.66
        if lag_status == "stage_3":
            return 0.58

        divergence_bps = signal_payload.get("divergence_bps")
        if isinstance(divergence_bps, (int, float)):
            return max(0.45, min(0.88, 0.45 + float(divergence_bps) / 200.0))

        return 0.60

    @staticmethod
    def _normalize_asset_class(value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "bonds": "bond",
            "bond": "bond",
            "yields": "yield",
            "yield": "yield",
            "equities": "equity",
            "equity": "equity",
            "fx": "fx",
            "currency": "fx",
            "currencies": "fx",
            "futures": "future",
            "future": "future",
            "options": "option",
            "option": "option",
        }
        return aliases.get(text, text)

    @staticmethod
    def _safe_ratio(numerator: Any, denominator: Any) -> float:
        try:
            num = float(numerator)
            den = float(denominator)
            if den == 0:
                return 0.0
            return num / den
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format_telegram(self) -> str:
        """Format last scan results for Telegram digest."""
        if not self._last_scan:
            return ""

        parts: list[str] = []

        # Bond/equity signals
        for sig in self._last_scan.get("bond_equity_signals", []):
            if sig["signal"] == "bond_equity_divergence":
                parts.append("TLT-SPY divergence \u2192 risk-off")
            elif sig["signal"] == "credit_spread_widening":
                parts.append("HYG-TLT spread \u2192 credit stress")

        # Currency signals
        for sig in self._last_scan.get("currency_signals", []):
            if sig["signal"] == "dxy_em_pressure":
                parts.append("DXY breakout \u2192 short INDA lag 2d")
            elif sig["signal"] == "dxy_breakout":
                parts.append("DXY surge \u2192 EM pressure")
            elif sig["signal"] == "jpy_carry_unwind":
                parts.append("JPY carry unwind \u2192 risk-off")
            elif sig["signal"] == "cad_oil_confirmation":
                parts.append("CAD strength \u2192 oil confirmed")

        # Commodity cascades
        for sig in self._last_scan.get("commodity_cascade_signals", []):
            parts.append(f"{sig['primary']} cascade {sig['lag_status']} \u2192 {sig['action'].split('(')[0].strip()}")

        # VIX
        vix = self._last_scan.get("vix_term_structure", {})
        if vix.get("structure") == "backwardation":
            parts.append("VIX backwardation \u2192 vol spike")
        elif "TRANSITION" in vix.get("signal", ""):
            parts.append("VIX flip \u2192 LONG UVXY")

        # Futures basis
        for sig in self._last_scan.get("futures_equity_basis", []):
            parts.append(
                f"{sig['futures_symbol']}-{sig['equity_symbol']} "
                f"diverge {sig['divergence_bps']:.0f}bp"
            )

        if not parts:
            return ""

        return "\U0001f517 Cross: " + " | ".join(parts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract(market_data: dict[str, Any], symbol: str) -> dict[str, Any] | None:
        """Extract symbol data from market_data, handling nested or flat formats."""
        val = market_data.get(symbol)
        if val is None:
            return None
        if isinstance(val, dict):
            return val
        # If scalar (just a price), wrap it
        if isinstance(val, (int, float)):
            return {"price": float(val), "last": float(val), "change_pct": 0.0}
        return None
