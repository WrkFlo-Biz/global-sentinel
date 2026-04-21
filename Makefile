.PHONY: test research-score-pipeline research-quantum-summary research-publish research-markdown-summary research-pipeline-full

test:
	PYTHONPATH=. python -m pytest tests/ -v

research-score-pipeline:
	PYTHONPATH=. python src/research/run_research_score_pipeline.py \
	  --request-json artifacts/incoming/request.json \
	  --trade-outcomes-json artifacts/incoming/trade_outcomes.json

research-quantum-summary:
	PYTHONPATH=. python src/reports/research_quantum_summary.py

research-publish:
	PYTHONPATH=. python src/research/publish_research_artifacts.py \
	  --container $(REPORTS_CONTAINER) \
	  --prefix research/$(ENV_NAME)

research-markdown-summary:
	PYTHONPATH=. python src/reports/research_quantum_markdown_summary.py

research-pipeline-full:
	PYTHONPATH=. python src/research/run_research_score_pipeline.py \
	  --request-json artifacts/incoming/request.json \
	  --trade-outcomes-json artifacts/incoming/trade_outcomes.json \
	  --snapshot-json artifacts/incoming/research_snapshot.json
	PYTHONPATH=. python src/reports/research_quantum_summary.py
	PYTHONPATH=. python src/reports/research_quantum_markdown_summary.py

.PHONY: research-executive-brief
research-executive-brief:
	PYTHONPATH=. python src/reports/research_executive_brief.py

.PHONY: research-summary-pack
research-summary-pack:
	PYTHONPATH=. python src/reports/research_quantum_summary.py
	PYTHONPATH=. python src/reports/research_quantum_markdown_summary.py
	PYTHONPATH=. python src/reports/research_executive_brief.py

.PHONY: build-regime-request
build-regime-request:
	PYTHONPATH=. python src/research/build_regime_conditioned_request.py \
	  --package-id demo-pkg \
	  --candidate-json artifacts/incoming/candidates.json \
	  --market-micro-json artifacts/incoming/market_micro.json \
	  --regime-state-json artifacts/incoming/regime_state.json \
	  --output-json artifacts/incoming/request.json

.PHONY: build-regime-request-derivs
build-regime-request-derivs:
	PYTHONPATH=. python src/research/build_regime_conditioned_request.py \
	  --package-id demo-pkg \
	  --candidate-json artifacts/incoming/candidates.json \
	  --market-micro-json artifacts/incoming/market_micro.json \
	  --regime-state-json artifacts/incoming/regime_state.json \
	  --include-derivatives \
	  --output-json artifacts/incoming/request.json

.PHONY: research-build-training-dataset
research-build-training-dataset:
	PYTHONPATH=. python src/research/qfinance_training_dataset_builder.py \
	  --encoded-candidates-json artifacts/incoming/encoded_candidates.json \
	  --regime-state-json artifacts/incoming/regime_state.json \
	  --trade-outcomes-json artifacts/incoming/trade_outcomes.json \
	  --research-score-json reports/research/research_score_latest.json \
	  --output-json reports/research/training_dataset.json

.PHONY: research-label-candidates
research-label-candidates:
	PYTHONPATH=. python src/research/alpha_candidate_labeler.py \
	  --dataset-json reports/research/training_dataset.json \
	  --output-json reports/research/training_labels.json

.PHONY: research-replay-backtest
research-replay-backtest:
	PYTHONPATH=. python src/research/replay_quantum_research_backtest.py

.PHONY: research-training-status
research-training-status:
	PYTHONPATH=. python src/reports/research_training_status_report.py

.PHONY: research-qfinance-loop
research-qfinance-loop:
	PYTHONPATH=. python src/research/build_regime_conditioned_request.py \
	  --package-id demo-pkg \
	  --candidate-json artifacts/incoming/candidates.json \
	  --market-micro-json artifacts/incoming/market_micro.json \
	  --regime-state-json artifacts/incoming/regime_state.json \
	  --include-derivatives \
	  --output-json artifacts/incoming/request.json
	PYTHONPATH=. python src/research/run_research_score_pipeline.py \
	  --request-json artifacts/incoming/request.json \
	  --trade-outcomes-json artifacts/incoming/trade_outcomes.json \
	  --snapshot-json artifacts/incoming/research_snapshot.json
	PYTHONPATH=. python src/research/qfinance_training_dataset_builder.py \
	  --encoded-candidates-json artifacts/incoming/encoded_candidates.json \
	  --regime-state-json artifacts/incoming/regime_state.json \
	  --trade-outcomes-json artifacts/incoming/trade_outcomes.json \
	  --research-score-json reports/research/research_score_latest.json \
	  --output-json reports/research/training_dataset.json
	PYTHONPATH=. python src/research/alpha_candidate_labeler.py \
	  --dataset-json reports/research/training_dataset.json \
	  --output-json reports/research/training_labels.json
	PYTHONPATH=. python src/reports/research_quantum_summary.py
	PYTHONPATH=. python src/reports/research_quantum_markdown_summary.py
	PYTHONPATH=. python src/reports/research_executive_brief.py
	PYTHONPATH=. python src/reports/research_training_status_report.py

.PHONY: research-init-online-state
research-init-online-state:
	PYTHONPATH=. python src/research/qfinance_online_learning_state.py \
	  --state-json reports/research/state/online_learning_state_prev.json \
	  --init

.PHONY: research-update-weights
research-update-weights:
	PYTHONPATH=. python src/research/update_research_model_weights.py \
	  --state-json reports/research/state/online_learning_state_prev.json \
	  --labeled-dataset-json reports/research/training_labels.json \
	  --output-json reports/research/state/online_learning_state_current.json

.PHONY: research-drift-report
research-drift-report:
	PYTHONPATH=. python src/reports/research_drift_report.py \
	  --previous-state-json reports/research/state/online_learning_state_prev.json \
	  --current-state-json reports/research/state/online_learning_state_current.json

.PHONY: research-online-learning-loop
research-online-learning-loop:
	PYTHONPATH=. python src/research/qfinance_online_learning_state.py \
	  --state-json reports/research/state/online_learning_state_prev.json \
	  --init
	cp reports/research/state/online_learning_state_prev.json reports/research/state/online_learning_state_current.json || true
	PYTHONPATH=. python src/research/update_research_model_weights.py \
	  --state-json reports/research/state/online_learning_state_prev.json \
	  --labeled-dataset-json reports/research/training_labels.json \
	  --output-json reports/research/state/online_learning_state_current.json
	PYTHONPATH=. python src/reports/research_drift_report.py \
	  --previous-state-json reports/research/state/online_learning_state_prev.json \
	  --current-state-json reports/research/state/online_learning_state_current.json
