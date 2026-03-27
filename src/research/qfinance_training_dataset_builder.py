"""Build row-wise training datasets from encoded candidates + trade outcomes.

Joins encoded candidate features with realized trade telemetry
and optional research scores for downstream labeling and training.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


class QFinanceTrainingDatasetBuilder:

    def build(
        self,
        *,
        encoded_candidates: List[Dict[str, Any]],
        regime_state: Dict[str, Any],
        trade_outcomes: Dict[str, Any],
        research_score: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        trade_map = {
            str(r.get("symbol")): r
            for r in (trade_outcomes.get("trades") or [])
            if r.get("symbol")
        }

        rows: List[Dict[str, Any]] = []
        for c in encoded_candidates:
            sym = str(c.get("symbol", ""))
            out = trade_map.get(sym, {})

            row = {
                "symbol": sym,
                "sector": c.get("sector"),
                "theme": c.get("theme"),
                "base_score": c.get("base_score"),
                "event_score": c.get("event_score"),
                "quality_score": c.get("quality_score"),
                "anomaly_score": c.get("anomaly_score"),
                "liquidity_score": c.get("liquidity_score"),
                "volatility_penalty": c.get("volatility_penalty"),
                "regime_alignment": c.get("regime_alignment"),
                "preopt_feature_score": c.get("preopt_feature_score"),
                "regime_shift_probability": regime_state.get("regime_shift_probability"),
                "macro_state": regime_state.get("macro_state"),
                "geopolitical_state": regime_state.get("geopolitical_state"),
                "trade_executed": out.get("trade_executed"),
                "direction": out.get("direction"),
                "realized_return_bps": out.get("realized_return_bps"),
                "realized_slippage_bps": out.get("realized_slippage_bps"),
                "fill_rate": out.get("fill_rate"),
                "quantum_influenced": out.get("quantum_influenced"),
                "research_score_used": out.get("research_score_used"),
                "attached_research_score": (research_score or {}).get("research_score"),
                "attached_recommended_influence": (research_score or {}).get("recommended_influence"),
            }
            rows.append(row)

        return {
            "schema_version": "qfinance_training_dataset.v1",
            "row_count": len(rows),
            "rows": rows,
        }


def parse_args():
    p = argparse.ArgumentParser(description="Build QFinance training dataset")
    p.add_argument("--encoded-candidates-json", required=True)
    p.add_argument("--regime-state-json", required=True)
    p.add_argument("--trade-outcomes-json", required=True)
    p.add_argument("--research-score-json", required=False)
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def main():
    args = parse_args()

    encoded_candidates = json.loads(Path(args.encoded_candidates_json).read_text(encoding="utf-8"))
    regime_state = json.loads(Path(args.regime_state_json).read_text(encoding="utf-8"))
    trade_outcomes = json.loads(Path(args.trade_outcomes_json).read_text(encoding="utf-8"))
    research_score = None
    if args.research_score_json:
        research_score = json.loads(Path(args.research_score_json).read_text(encoding="utf-8"))

    ds = QFinanceTrainingDatasetBuilder().build(
        encoded_candidates=encoded_candidates,
        regime_state=regime_state,
        trade_outcomes=trade_outcomes,
        research_score=research_score,
    )

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ds, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
