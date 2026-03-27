"""Cross-asset signal detection for Global Sentinel.

Detects signals from bonds, currencies, commodities that lead equity moves.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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

        self._last_scan = {
            "bond_equity_signals": bond_signals,
            "currency_signals": currency_signals,
            "commodity_cascade_signals": commodity_signals,
            "vix_term_structure": vix_structure,
            "futures_equity_basis": basis_signals,
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
                        "primary": "CL",
                        "secondary": " -> ".join(stages_hit),
                        "lag_status": lag,
                        "action": action,
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
                        "primary": "GLD",
                        "secondary": " -> ".join(stages_hit),
                        "lag_status": lag,
                        "action": f"{action} (gold cascade {direction})",
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
                        "primary": "NG",
                        "secondary": " -> ".join(stages_hit),
                        "lag_status": lag,
                        "action": f"{action} (nat gas cascade {direction})",
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
                    "futures_symbol": "ES",
                    "equity_symbol": "SPY",
                    "divergence_bps": round(divergence_bps, 1),
                    "action": action,
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
                        "futures_symbol": "CL",
                        "equity_symbol": "XLE",
                        "divergence_bps": round(divergence_bps, 1),
                        "action": "LONG XLE (lagging oil spike)",
                    })
                elif xle_chg > cl_chg + 0.5:
                    signals.append({
                        "futures_symbol": "CL",
                        "equity_symbol": "XLE",
                        "divergence_bps": round(divergence_bps, 1),
                        "action": "SHORT XLE (over-extended vs crude)",
                    })

        if signals:
            self._futures_history.append({
                "count": len(signals),
                "ts": time.time(),
            })

        return signals

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
