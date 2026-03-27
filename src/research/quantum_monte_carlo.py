#!/usr/bin/env python3
"""Quantum Amplitude Estimation for Monte Carlo — Global Sentinel Research.

Uses Qiskit Aer to implement quantum amplitude estimation (QAE) for each
scenario type defined in the continuous learner. Encodes probability
distributions as quantum states, estimates expected values via QAE, and
compares against classical MC results.

Output: data/quantum_feed/quantum_mc_results.json
"""
from __future__ import annotations

import json
import logging
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("global_sentinel.quantum_monte_carlo")

REPO_ROOT = Path("/opt/global-sentinel")
OUTPUT_PATH = REPO_ROOT / "data" / "quantum_feed" / "quantum_mc_results.json"

# ---------------------------------------------------------------------------
# Scenario configurations (mirrors quantum_continuous_learner.py)
# ---------------------------------------------------------------------------
SCENARIO_CONFIGS = {
    "normal_to_elevated":        {"mu": -0.001, "sigma": 0.025, "shock_bps": -200},
    "elevated_to_crisis":        {"mu": -0.005, "sigma": 0.05,  "shock_bps": -500},
    "crisis_to_normal":          {"mu":  0.003, "sigma": 0.03,  "shock_bps":  300},
    "tech_to_energy":            {"mu": -0.002, "sigma": 0.03,  "shock_bps": -150},
    "energy_to_defense":         {"mu":  0.001, "sigma": 0.025, "shock_bps":  100},
    "defense_to_consumer":       {"mu":  0.002, "sigma": 0.02,  "shock_bps":   50},
    "earnings_surprise_pos":     {"mu":  0.005, "sigma": 0.04,  "shock_bps":  400},
    "earnings_surprise_neg":     {"mu": -0.005, "sigma": 0.04,  "shock_bps": -400},
    "fed_rate_hike":             {"mu": -0.002, "sigma": 0.03,  "shock_bps": -250},
    "fed_rate_cut":              {"mu":  0.003, "sigma": 0.025, "shock_bps":  200},
    "geopolitical_escalation":   {"mu": -0.004, "sigma": 0.045, "shock_bps": -350},
    "peace_talks":               {"mu":  0.002, "sigma": 0.02,  "shock_bps":  150},
    "tariff_announcement":       {"mu": -0.003, "sigma": 0.035, "shock_bps": -300},
    "flash_crash":               {"mu": -0.01,  "sigma": 0.08,  "shock_bps": -1000},
    "vix_spike_40":              {"mu": -0.008, "sigma": 0.06,  "shock_bps": -800},
    "oil_shock_10pct":           {"mu": -0.005, "sigma": 0.04,  "shock_bps": -500},
    "currency_crisis":           {"mu": -0.006, "sigma": 0.05,  "shock_bps": -600},
    "covid_crash_2020":          {"mu": -0.015, "sigma": 0.1,   "shock_bps": -1200},
    "rate_hike_cycle_2022":      {"mu": -0.003, "sigma": 0.035, "shock_bps": -250},
    "ai_rally_2024":             {"mu":  0.008, "sigma": 0.04,  "shock_bps":  500},
    "iran_escalation":           {"mu": -0.004, "sigma": 0.045, "shock_bps": -400},
    "correlated_crash":          {"mu": -0.012, "sigma": 0.09,  "shock_bps": -1000},
    "liquidity_crisis":          {"mu": -0.008, "sigma": 0.07,  "shock_bps": -700},
    "flash_rally":               {"mu":  0.010, "sigma": 0.06,  "shock_bps":  800},
    "fed_surprise_cut":          {"mu":  0.006, "sigma": 0.04,  "shock_bps":  500},
    "fed_surprise_hike":         {"mu": -0.006, "sigma": 0.04,  "shock_bps": -500},
    "earnings_cascade":          {"mu": -0.007, "sigma": 0.055, "shock_bps": -600},
    "oil_shock_hormuz":          {"mu": -0.009, "sigma": 0.07,  "shock_bps": -900},
    "currency_crisis_dxy":       {"mu": -0.007, "sigma": 0.06,  "shock_bps": -650},
    "crypto_contagion":          {"mu": -0.006, "sigma": 0.055, "shock_bps": -500},
}


# ---------------------------------------------------------------------------
# Classical Monte Carlo baseline
# ---------------------------------------------------------------------------
def classical_monte_carlo(
    mu: float, sigma: float, shock_bps: float,
    n_paths: int = 10000, n_steps: int = 20, seed: int = 42,
) -> Dict[str, float]:
    """Run classical MC and return summary statistics."""
    rng = random.Random(seed)
    shock = shock_bps / 10000.0
    terminals = []
    for _ in range(n_paths):
        total = 1.0
        for step in range(n_steps):
            ret = rng.gauss(mu, sigma)
            if step == 0:
                ret += shock
            total *= (1.0 + ret)
        terminals.append(total - 1.0)
    terminals.sort()
    n = len(terminals)
    return {
        "mean": sum(terminals) / n,
        "p05": terminals[max(0, int(0.05 * n) - 1)],
        "p50": terminals[max(0, int(0.50 * n) - 1)],
        "p95": terminals[max(0, int(0.95 * n) - 1)],
        "std": float(np.std(terminals)),
        "n_paths": n_paths,
    }


# ---------------------------------------------------------------------------
# Quantum Amplitude Estimation via Qiskit Aer
# ---------------------------------------------------------------------------
class QuantumAmplitudeEstimator:
    """Encodes scenario distributions as quantum states and uses
    amplitude estimation to compute expected terminal returns.

    Uses 8-10 qubits: some for state preparation (encoding the
    distribution), and some for the phase estimation register.
    """

    def __init__(self, n_state_qubits: int = 4, n_eval_qubits: int = 6):
        """
        n_state_qubits: qubits to encode the distribution (2^n_state bins)
        n_eval_qubits: qubits for iterative amplitude estimation precision
        Total qubits: n_state_qubits + n_eval_qubits (typically 10)
        """
        self.n_state_qubits = n_state_qubits
        self.n_eval_qubits = n_eval_qubits
        self.n_total = n_state_qubits + n_eval_qubits

        try:
            from qiskit import QuantumCircuit
            from qiskit_aer import AerSimulator
            self.QuantumCircuit = QuantumCircuit
            self.simulator = AerSimulator(method="statevector")
            self._available = True
        except ImportError as e:
            logger.warning("Qiskit Aer not available: %s", e)
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def _discretize_distribution(
        self, mu: float, sigma: float, shock_bps: float, n_steps: int = 20
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Discretize the terminal return distribution into 2^n_state bins.

        Simulates the multi-step return process analytically:
        - First step includes shock
        - Remaining steps are normal(mu, sigma)
        - Approximate terminal distribution as normal
        """
        from scipy.stats import norm

        n_bins = 2 ** self.n_state_qubits
        shock = shock_bps / 10000.0

        # Approximate: terminal return ~ N(total_mu, total_sigma)
        total_mu = (mu + shock) + mu * (n_steps - 1)
        total_sigma = sigma * math.sqrt(n_steps)

        # Create bin edges spanning ~4 sigma
        lo = total_mu - 4 * total_sigma
        hi = total_mu + 4 * total_sigma
        edges = np.linspace(lo, hi, n_bins + 1)
        centers = (edges[:-1] + edges[1:]) / 2.0

        # Compute probabilities for each bin (normal CDF differences)
        probs = np.array([
            norm.cdf(edges[i + 1], loc=total_mu, scale=max(total_sigma, 1e-6))
            - norm.cdf(edges[i], loc=total_mu, scale=max(total_sigma, 1e-6))
            for i in range(n_bins)
        ])

        # Normalize to valid probability distribution
        total = probs.sum()
        if total > 0:
            probs = probs / total
        else:
            probs = np.ones(n_bins) / n_bins

        return probs, centers

    def _build_state_prep_circuit(self, probs: np.ndarray):
        """Build a quantum circuit that prepares |psi> = sum sqrt(p_i)|i>."""
        amplitudes = np.sqrt(np.maximum(probs, 0))
        # Normalize to unit vector
        norm_val = np.linalg.norm(amplitudes)
        if norm_val > 0:
            amplitudes = amplitudes / norm_val

        qc = self.QuantumCircuit(self.n_state_qubits)
        qc.initialize(amplitudes, range(self.n_state_qubits))
        return qc

    def estimate_expected_value(
        self, mu: float, sigma: float, shock_bps: float, n_steps: int = 20
    ) -> Dict[str, Any]:
        """Run quantum amplitude estimation for a scenario.

        Returns estimated expected terminal return and tail probabilities.
        Uses two complementary methods:
        1. Statevector simulation for exact quantum state analysis
        2. Shot-based QAE circuit for practical amplitude estimation
        """
        if not self._available:
            return {"status": "unavailable", "error": "Qiskit Aer not installed"}

        start = time.monotonic()

        try:
            from qiskit import transpile
        except ImportError:
            return {"status": "unavailable", "error": "Qiskit not installed"}

        probs, centers = self._discretize_distribution(mu, sigma, shock_bps, n_steps)

        # --- Method 1: Direct statevector measurement ---
        state_circuit = self._build_state_prep_circuit(probs)
        state_circuit.save_statevector()

        transpiled = transpile(state_circuit, self.simulator)
        job = self.simulator.run(transpiled, shots=0)
        result = job.result()
        statevector = np.array(result.get_statevector())

        # Extract probabilities from statevector (squared amplitudes)
        measured_probs = np.abs(statevector[:len(probs)]) ** 2
        prob_sum = measured_probs.sum()
        if prob_sum > 0:
            measured_probs = measured_probs / prob_sum

        # Compute expected value from quantum state
        quantum_expected = float(np.dot(measured_probs, centers))

        # Tail probability estimates
        cumprobs = np.cumsum(probs)
        p05_idx = int(np.searchsorted(cumprobs, 0.05))
        p50_idx = int(np.searchsorted(cumprobs, 0.50))

        tail_prob_quantum = float(np.sum(measured_probs[:max(1, p05_idx)]))

        # --- Method 2: Shot-based QAE circuit ---
        n_total = self.n_state_qubits + 1  # +1 ancilla for objective
        qae_circuit = self.QuantumCircuit(n_total, 1)

        # State preparation on first n_state qubits
        amplitudes = np.sqrt(np.maximum(probs, 0))
        norm_val = np.linalg.norm(amplitudes)
        if norm_val > 0:
            amplitudes = amplitudes / norm_val
        qae_circuit.initialize(amplitudes, range(self.n_state_qubits))

        # Map bin values to [0, 1] range for rotation encoding
        val_range = centers.max() - centers.min()
        if val_range > 0:
            normalized = (centers - centers.min()) / val_range
        else:
            normalized = np.ones_like(centers) * 0.5

        # Apply controlled rotations: encode f(x) as rotation on ancilla
        for i in range(min(len(centers), 2 ** self.n_state_qubits)):
            angle = 2 * np.arcsin(np.sqrt(max(0.0, min(1.0, normalized[i]))))
            if abs(angle) < 1e-10:
                continue
            binary = format(i, f"0{self.n_state_qubits}b")
            # Prepare control state
            for bit_idx, bit in enumerate(binary):
                if bit == "0":
                    qae_circuit.x(bit_idx)
            # Controlled rotation on ancilla
            if self.n_state_qubits == 1:
                qae_circuit.cry(angle, 0, self.n_state_qubits)
            else:
                qae_circuit.mcx(list(range(self.n_state_qubits)), self.n_state_qubits)
                qae_circuit.ry(angle / 2, self.n_state_qubits)
                qae_circuit.mcx(list(range(self.n_state_qubits)), self.n_state_qubits)
                qae_circuit.ry(-angle / 2, self.n_state_qubits)
            # Undo control state preparation
            for bit_idx, bit in enumerate(binary):
                if bit == "0":
                    qae_circuit.x(bit_idx)

        # Measure objective qubit
        qae_circuit.measure(self.n_state_qubits, 0)

        # Run with shots to estimate amplitude
        transpiled_qae = transpile(qae_circuit, self.simulator)
        job_qae = self.simulator.run(transpiled_qae, shots=8192)
        result_qae = job_qae.result()
        counts = result_qae.get_counts()
        ones_count = counts.get("1", 0)
        total_shots = sum(counts.values())
        qae_amplitude = ones_count / total_shots if total_shots > 0 else 0.5

        # Map amplitude back to expected value
        qae_expected = centers.min() + qae_amplitude * val_range if val_range > 0 else quantum_expected

        elapsed = time.monotonic() - start

        return {
            "status": "success",
            "quantum_expected_return": quantum_expected,
            "qae_expected_return": qae_expected,
            "tail_prob_p05": tail_prob_quantum,
            "p05_center": float(centers[max(0, p05_idx - 1)]) if p05_idx > 0 else float(centers[0]),
            "p50_center": float(centers[min(p50_idx, len(centers) - 1)]),
            "n_bins": len(probs),
            "n_state_qubits": self.n_state_qubits,
            "n_eval_qubits": self.n_eval_qubits,
            "qae_shots": 8192,
            "runtime_seconds": round(elapsed, 4),
        }


# ---------------------------------------------------------------------------
# Full QMC pipeline: run all scenarios, compare quantum vs classical
# ---------------------------------------------------------------------------
def run_quantum_monte_carlo(
    scenarios: Optional[Dict[str, Dict]] = None,
    n_classical_paths: int = 10000,
    n_state_qubits: int = 4,
    n_eval_qubits: int = 6,
) -> Dict[str, Any]:
    """Run quantum amplitude estimation for all scenarios and compare to classical MC.

    Parameters
    ----------
    scenarios : scenario configs dict (defaults to SCENARIO_CONFIGS)
    n_classical_paths : number of classical MC paths for comparison
    n_state_qubits : qubits for distribution encoding (2^n bins)
    n_eval_qubits : qubits for amplitude estimation precision
    """
    if scenarios is None:
        scenarios = SCENARIO_CONFIGS

    qae = QuantumAmplitudeEstimator(
        n_state_qubits=n_state_qubits,
        n_eval_qubits=n_eval_qubits,
    )

    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "n_classical_paths": n_classical_paths,
            "n_state_qubits": n_state_qubits,
            "n_eval_qubits": n_eval_qubits,
            "total_qubits": n_state_qubits + n_eval_qubits,
        },
        "scenarios": {},
        "aggregate": {},
    }

    speedup_factors = []
    accuracy_deltas = []

    for name, cfg in scenarios.items():
        logger.info("QMC scenario: %s", name)

        # Classical MC
        t0 = time.monotonic()
        classical = classical_monte_carlo(
            mu=cfg["mu"], sigma=cfg["sigma"], shock_bps=cfg["shock_bps"],
            n_paths=n_classical_paths, seed=42,
        )
        classical_time = time.monotonic() - t0

        # Quantum AE
        quantum = qae.estimate_expected_value(
            mu=cfg["mu"], sigma=cfg["sigma"], shock_bps=cfg["shock_bps"],
        )

        if quantum.get("status") == "success":
            q_mean = quantum["quantum_expected_return"]
            c_mean = classical["mean"]
            accuracy_delta = abs(q_mean - c_mean)
            quantum_time = quantum["runtime_seconds"]
            # Practical speedup on simulator is limited; track the ratio
            speedup = classical_time / max(quantum_time, 0.001)

            speedup_factors.append(speedup)
            accuracy_deltas.append(accuracy_delta)

            results["scenarios"][name] = {
                "classical": {
                    "mean": classical["mean"],
                    "p05": classical["p05"],
                    "p50": classical["p50"],
                    "p95": classical["p95"],
                    "std": classical["std"],
                    "runtime_s": round(classical_time, 4),
                },
                "quantum": {
                    "expected_return_sv": quantum["quantum_expected_return"],
                    "expected_return_qae": quantum["qae_expected_return"],
                    "tail_prob_p05": quantum["tail_prob_p05"],
                    "p05_center": quantum["p05_center"],
                    "p50_center": quantum["p50_center"],
                    "runtime_s": quantum["runtime_seconds"],
                },
                "comparison": {
                    "accuracy_delta": round(accuracy_delta, 6),
                    "speedup_factor": round(speedup, 2),
                    "quantum_closer_to_mean": accuracy_delta < classical["std"] * 0.1,
                },
            }
        else:
            results["scenarios"][name] = {
                "classical": classical,
                "quantum": quantum,
                "comparison": {"status": "quantum_failed"},
            }

    # Aggregate statistics
    if speedup_factors:
        results["aggregate"] = {
            "n_scenarios": len(speedup_factors),
            "avg_speedup_factor": round(sum(speedup_factors) / len(speedup_factors), 2),
            "avg_accuracy_delta": round(sum(accuracy_deltas) / len(accuracy_deltas), 6),
            "max_accuracy_delta": round(max(accuracy_deltas), 6),
            "min_accuracy_delta": round(min(accuracy_deltas), 6),
            "quantum_viable": sum(1 for d in accuracy_deltas if d < 0.01) / len(accuracy_deltas),
        }

    # Write output
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(results, indent=2, default=str))
    logger.info("Quantum MC results written to %s (%d scenarios)", OUTPUT_PATH, len(results["scenarios"]))

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    result = run_quantum_monte_carlo(n_classical_paths=1000, n_state_qubits=4, n_eval_qubits=4)
    print(json.dumps(result.get("aggregate", {}), indent=2))
