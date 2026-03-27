"""Label training dataset rows for supervised learning.

Assigns alpha labels (strong_positive/positive/neutral/negative/strong_negative)
and execution quality labels based on realized returns, slippage, and fill rate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


class AlphaCandidateLabeler:

    def label_rows(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        out_rows: List[Dict[str, Any]] = []

        for row in dataset.get("rows", []):
            realized = row.get("realized_return_bps")
            slippage = row.get("realized_slippage_bps")
            fill_rate = row.get("fill_rate")

            label = "neutral"
            if realized is not None:
                realized = float(realized)
                if realized >= 75:
                    label = "strong_positive"
                elif realized >= 15:
                    label = "positive"
                elif realized <= -75:
                    label = "strong_negative"
                elif realized <= -15:
                    label = "negative"

            quality = "unknown"
            if fill_rate is not None:
                fr = float(fill_rate)
                if fr >= 0.95:
                    quality = "high_fill_quality"
                elif fr >= 0.70:
                    quality = "medium_fill_quality"
                else:
                    quality = "low_fill_quality"

            if slippage is not None:
                sl = float(slippage)
                if sl >= 20:
                    quality = "poor_execution_quality"
                elif sl <= 5 and quality != "unknown":
                    quality = "good_execution_quality"

            new_row = dict(row)
            new_row["alpha_label"] = label
            new_row["execution_quality_label"] = quality
            out_rows.append(new_row)

        return {
            "schema_version": "alpha_candidate_labels.v1",
            "row_count": len(out_rows),
            "rows": out_rows,
        }


def parse_args():
    p = argparse.ArgumentParser(description="Label alpha candidates")
    p.add_argument("--dataset-json", required=True)
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def main():
    args = parse_args()
    ds = json.loads(Path(args.dataset_json).read_text(encoding="utf-8"))
    labeled = AlphaCandidateLabeler().label_rows(ds)

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(labeled, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
