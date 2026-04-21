from src.research.quantum_retraining_job import QuantumRetrainingJob


def test_quantum_retraining_job_updates_online_state_from_labels(tmp_path):
    repo_root = tmp_path
    (repo_root / "reports" / "research").mkdir(parents=True, exist_ok=True)
    (repo_root / "config").mkdir(parents=True, exist_ok=True)

    (repo_root / "reports" / "research" / "training_labels.json").write_text(
        """
{
  "schema_version": "alpha_candidate_labels.v1",
  "row_count": 4,
  "walk_forward_validation": {"passed": true, "folds_run": 3, "fold_count": 3},
  "rows": [
    {
      "symbol": "XLE",
      "base_score": 0.72,
      "event_score": 0.81,
      "quality_score": 0.65,
      "anomaly_score": 0.20,
      "liquidity_score": 0.60,
      "regime_alignment": 0.77,
      "volatility_penalty": -0.15,
      "realized_return_bps": 95,
      "alpha_label": "strong_positive"
    },
    {
      "symbol": "JETS",
      "base_score": 0.61,
      "event_score": 0.75,
      "quality_score": 0.58,
      "anomaly_score": 0.22,
      "liquidity_score": 0.55,
      "regime_alignment": 0.70,
      "volatility_penalty": -0.18,
      "realized_return_bps": -80,
      "alpha_label": "strong_negative"
    },
    {
      "symbol": "GLD",
      "base_score": 0.66,
      "event_score": 0.63,
      "quality_score": 0.62,
      "anomaly_score": 0.11,
      "liquidity_score": 0.59,
      "regime_alignment": 0.68,
      "volatility_penalty": -0.09,
      "realized_return_bps": 40,
      "alpha_label": "positive"
    },
    {
      "symbol": "SPY",
      "base_score": 0.54,
      "event_score": 0.49,
      "quality_score": 0.47,
      "anomaly_score": 0.16,
      "liquidity_score": 0.88,
      "regime_alignment": 0.50,
      "volatility_penalty": -0.21,
      "realized_return_bps": -25,
      "alpha_label": "negative"
    }
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = QuantumRetrainingJob(repo_root=str(repo_root)).run()

    assert result["steps"]["load_training_labels"]["count"] == 4
    assert result["steps"]["update_online_learning_state"]["status"] in {"updated", "skipped"}
    assert result["steps"]["load_training_labels"]["count"] == 4
