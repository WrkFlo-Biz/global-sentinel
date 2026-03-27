#!/usr/bin/env python3
"""Strong Classical Baseline — Markowitz mean-variance via CVXPY.

Quantum backends must outperform THIS to claim meaningful advantage.
Uses same request/response format as Qiskit and QPanda3 backends.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np
    import cvxpy as cp

    CVXPY_AVAILABLE = True
except ImportError:
    CVXPY_AVAILABLE = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_id(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


class ClassicalStrongBaseline:
    """Markowitz mean-variance portfolio optimization via CVXPY MILP."""

    def optimize(self, request: dict) -> dict:
        start = time.monotonic()
        candidates = request.get("candidates") or request.get("candidate_universe") or []
        constraints = request.get("constraints", {})
        config = request.get("config", {})
        n = len(candidates)

        if n == 0 or not CVXPY_AVAILABLE:
            return self._fallback(
                start, "no_candidates" if n == 0 else "cvxpy_not_available"
            )

        expected_returns = np.array([
            c.get("expected_return", c.get("score", 0.0)) for c in candidates
        ])
        risk_factor = config.get("risk_factor", 0.5)
        budget = min(constraints.get("budget", max(1, n // 2)), n)
        cov = self._build_covariance(candidates, n)

        try:
            max_sector_pct = constraints.get("max_sector_pct", 1.0)
            sectors: Dict[str, list] = {}
            for i, c in enumerate(candidates):
                sectors.setdefault(c.get("sector", "unknown"), []).append(i)
            max_per = max(1, int(budget * max_sector_pct))

            installed_solvers = set(cp.installed_solvers())
            prob = None
            sel = None
            solver_used = None

            # Prefer exact mixed-integer backends when available.
            mi_solver = next(
                (name for name in ("GLPK_MI", "CBC", "SCIP", "ECOS_BB") if name in installed_solvers),
                None,
            )
            if mi_solver:
                x_binary = cp.Variable(n, boolean=True)
                cons = [cp.sum(x_binary) == budget]
                if max_sector_pct < 1.0:
                    for idxs in sectors.values():
                        if len(idxs) > 1:
                            cons.append(cp.sum([x_binary[i] for i in idxs]) <= max_per)
                prob = cp.Problem(
                    cp.Maximize(expected_returns @ x_binary - risk_factor * cp.quad_form(x_binary, cov)),
                    cons,
                )
                try:
                    prob.solve(solver=mi_solver, verbose=False)
                    solver_used = mi_solver
                    if prob.status in ("optimal", "optimal_inaccurate") and x_binary.value is not None:
                        sel = [int(round(float(v))) for v in x_binary.value]
                except Exception as exc:
                    logger.info("Mixed-integer solver %s unavailable for this problem: %s", mi_solver, exc)
                    prob = None

            # Fall back to relaxed convex solve, then project to a discrete basket.
            if sel is None:
                x_relaxed = cp.Variable(n)
                cons = [cp.sum(x_relaxed) == budget, x_relaxed >= 0, x_relaxed <= 1]
                if max_sector_pct < 1.0:
                    for idxs in sectors.values():
                        if len(idxs) > 1:
                            cons.append(cp.sum([x_relaxed[i] for i in idxs]) <= max_per)
                prob = cp.Problem(
                    cp.Maximize(expected_returns @ x_relaxed - risk_factor * cp.quad_form(x_relaxed, cov)),
                    cons,
                )
                qp_solver = next(
                    (name for name in ("OSQP", "ECOS", "SCS") if name in installed_solvers),
                    None,
                )
                if qp_solver:
                    prob.solve(solver=qp_solver, verbose=False)
                    solver_used = qp_solver
                else:
                    prob.solve(verbose=False)
                    solver_used = "cvxpy_default"
                weights = x_relaxed.value if x_relaxed.value is not None else [0.0] * n
                sel = self._project_relaxed_solution(
                    weights=weights,
                    candidates=candidates,
                    budget=budget,
                    max_per_sector=max_per,
                )

            elapsed = time.monotonic() - start

            selected = [
                candidates[i].get("symbol", f"C{i}")
                for i in range(n) if sel[i] == 1
            ]
            artifact_id = _artifact_id(
                {
                    "backend": "classical_strong",
                    "selection": sel,
                    "timestamp_utc": _utc_now(),
                }
            )

            return {
                "backend": "classical_strong",
                "algorithm": "markowitz_milp",
                "status": prob.status,
                "selected_candidates": selected,
                "selected_indices": [i for i in range(n) if sel[i] == 1],
                "selection_vector": sel,
                "objective_value": float(prob.value) if prob.value is not None else None,
                "num_assets_input": n,
                "num_assets_selected": sum(sel),
                "budget": budget,
                "risk_factor": risk_factor,
                "execution_metadata": {
                    "not_for_direct_execution": True,
                    "quantum_direct_execution_forbidden": True,
                    "bounded_secondary_signal_only": True,
                    "backend": "classical_strong",
                    "algorithm": "markowitz_milp",
                    "solver": solver_used,
                    "status": prob.status,
                    "runtime_seconds": round(elapsed, 4),
                    "timestamp_utc": _utc_now(),
                    "artifact_id": artifact_id,
                    "num_assets": n,
                    "budget": budget,
                    "risk_factor": risk_factor,
                },
            }
        except Exception as exc:
            logger.warning("CVXPY solve failed: %s", exc, exc_info=True)
            return self._fallback(start, str(exc))

    def _build_covariance(self, candidates: list, n: int):
        vols = np.array([c.get("volatility", 0.2) for c in candidates])
        cov = np.diag(vols ** 2)
        for i in range(n):
            for j in range(i + 1, n):
                corr = 0.3 if candidates[i].get("sector") == candidates[j].get("sector") else 0.1
                cov[i][j] = corr * vols[i] * vols[j]
                cov[j][i] = cov[i][j]
        return cov

    def _fallback(self, start: float, reason: str) -> dict:
        elapsed = time.monotonic() - start
        return {
            "backend": "classical_strong", "algorithm": "markowitz_milp",
            "status": "error", "reason": reason,
            "selected_candidates": [], "selected_indices": [],
            "selection_vector": [], "objective_value": None,
            "execution_metadata": {
                "not_for_direct_execution": True,
                "quantum_direct_execution_forbidden": True,
                "bounded_secondary_signal_only": True,
                "backend": "classical_strong", "status": "error",
                "reason": reason,
                "runtime_seconds": round(elapsed, 4),
                "timestamp_utc": _utc_now(),
                "artifact_id": _artifact_id(
                    {"backend": "classical_strong", "reason": reason, "timestamp_utc": _utc_now()}
                ),
            },
        }

    def _project_relaxed_solution(
        self,
        *,
        weights,
        candidates: list,
        budget: int,
        max_per_sector: int,
    ) -> list:
        ranked = sorted(
            enumerate(weights),
            key=lambda item: float(item[1]),
            reverse=True,
        )
        sel = [0] * len(candidates)
        sector_counts: Dict[str, int] = {}
        for idx, _weight in ranked:
            sector = candidates[idx].get("sector", "unknown")
            if sector_counts.get(sector, 0) >= max_per_sector:
                continue
            sel[idx] = 1
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            if sum(sel) >= budget:
                break
        return sel


if __name__ == "__main__":
    test = {
        "candidates": [
            {"symbol": "AAPL", "expected_return": 0.08, "volatility": 0.25, "sector": "tech"},
            {"symbol": "MSFT", "expected_return": 0.07, "volatility": 0.22, "sector": "tech"},
            {"symbol": "XOM", "expected_return": 0.05, "volatility": 0.30, "sector": "energy"},
            {"symbol": "JPM", "expected_return": 0.06, "volatility": 0.20, "sector": "financials"},
        ],
        "constraints": {"budget": 2, "max_sector_pct": 0.5},
        "config": {"risk_factor": 0.5},
    }
    opt = ClassicalStrongBaseline()
    r = opt.optimize(test)
    print(json.dumps(r, indent=2, default=str))
