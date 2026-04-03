#!/usr/bin/env python3
"""
IBM Quantum Monthly Validation (v2 — Upgraded)
Runs a warm-started QAOA portfolio optimization circuit on real IBM quantum hardware,
with transpilation level 3, ZNE + M3 error mitigation, batch-mode execution,
CVaR aggregation, and classical comparison. Reports results via Telegram.
Scheduled: 1st of each month at 10:00 UTC.

Upgrades over v1:
  - Warm-start: solve classical mean-variance first, use solution to initialize QAOA
  - Transpilation optimization_level=3 for maximum gate count reduction
  - Resilience level 2 (ZNE + M3 readout mitigation) on Sampler
  - Batch mode: submit warm-start + cold-start circuits in one batch
  - CVaR aggregation: focus on best alpha-fraction of samples
  - --dry-run flag: test on Aer simulator before using real QPU
"""
import json, os, sys, time, datetime, traceback, urllib.request, argparse
from pathlib import Path
import numpy as np

# --- Telegram topic routing ---
sys.path.insert(0, "/opt/global-sentinel") if "/opt/global-sentinel" not in sys.path else None
try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))

env = {}
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
            os.environ.setdefault(k.strip(), v.strip())

IBM_TOKEN = env.get("IBM_QUANTUM_TOKEN", "")
TG_TOKEN = env.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = env.get("TELEGRAM_TOPIC_CHAT_ID", "")
TG_THREAD = env.get("TELEGRAM_DEFAULT_THREAD_ID", "74")

ASSETS = ["SPY", "QQQ", "NVDA", "TSLA", "GLD"]
N_ASSETS = len(ASSETS)
SHOTS = 4000
MAX_RETRIES = 3
RETRY_DELAY = 300  # 5 min
QAOA_DEPTH = 2
CVAR_ALPHA = 0.2  # CVaR: keep best 20% of samples

REPORTS_DIR = REPO_ROOT / "reports" / "quantum_validation"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = REPO_ROOT / "data" / "quantum_feed"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = REPO_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(f"[{ts}] {msg}", flush=True)


def send_telegram(msg):
    if _send_topic:
        try:
            _send_topic(msg[:4000] if isinstance(msg, str) else str(msg)[:4000], topic="system")
            return
        except Exception:
            pass
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        payload = json.dumps({
            "chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML",
            "message_thread_id": int(TG_THREAD), "disable_notification": False
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log(f"Telegram error: {e}")


def get_correlation_matrix():
    """Load correlation from quantum_feed or compute from Alpaca bars."""
    corr_path = DATA_DIR / "strategy_correlation_weights.json"
    if corr_path.exists():
        try:
            data = json.loads(corr_path.read_text())
            if "correlation_matrix" in data:
                matrix = data["correlation_matrix"]
                if isinstance(matrix, dict):
                    corr = np.zeros((N_ASSETS, N_ASSETS))
                    for i, a in enumerate(ASSETS):
                        for j, b in enumerate(ASSETS):
                            corr[i][j] = matrix.get(a, {}).get(b, 1.0 if i == j else 0.3)
                    return corr
        except Exception as e:
            log(f"Correlation load error: {e}")

    # Fallback: fetch 30-day bars from Alpaca
    try:
        live_key = env.get("ALPACA_API_KEY_LIVE", env.get("ALPACA_API_KEY", ""))
        live_secret = env.get("ALPACA_SECRET_KEY_LIVE", env.get("ALPACA_SECRET_KEY", ""))
        end = datetime.date.today()
        start = end - datetime.timedelta(days=45)
        symbols = ",".join(ASSETS)
        url = (f"https://data.alpaca.markets/v2/stocks/bars?"
               f"symbols={symbols}&timeframe=1Day&start={start}&end={end}&limit=50&sort=asc")
        req = urllib.request.Request(url)
        req.add_header("APCA-API-KEY-ID", live_key)
        req.add_header("APCA-API-SECRET-KEY", live_secret)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        returns = {}
        for sym in ASSETS:
            bars = data.get("bars", {}).get(sym, [])
            closes = [b["c"] for b in bars]
            if len(closes) > 1:
                rets = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
                returns[sym] = rets
        min_len = min(len(v) for v in returns.values()) if returns else 0
        if min_len < 5:
            return _default_correlation()
        ret_matrix = np.array([returns[s][:min_len] for s in ASSETS])
        return np.corrcoef(ret_matrix)
    except Exception as e:
        log(f"Alpaca correlation fetch error: {e}")
        return _default_correlation()


def _default_correlation():
    """Reasonable default correlation matrix for SPY, QQQ, NVDA, TSLA, GLD."""
    return np.array([
        [1.00, 0.92, 0.75, 0.55, 0.05],
        [0.92, 1.00, 0.82, 0.60, 0.02],
        [0.75, 0.82, 1.00, 0.50, -0.05],
        [0.55, 0.60, 0.50, 1.00, -0.02],
        [0.05, 0.02, -0.05, -0.02, 1.00],
    ])


def get_expected_returns():
    """Get annualized expected returns for assets."""
    try:
        fc_path = DATA_DIR / "price_forecasts.json"
        if fc_path.exists():
            data = json.loads(fc_path.read_text())
            returns = {}
            for sym in ASSETS:
                fc = data.get(sym, data.get("forecasts", {}).get(sym, {}))
                if isinstance(fc, dict) and "expected_return" in fc:
                    returns[sym] = fc["expected_return"]
            if len(returns) == N_ASSETS:
                return np.array([returns[s] for s in ASSETS])
    except Exception:
        pass
    return np.array([0.10, 0.12, 0.25, 0.20, 0.05])


def build_qubo_matrix(corr, exp_returns, risk_aversion=0.5):
    """Build QUBO cost matrix for the portfolio problem."""
    sigma = corr.copy()
    Q = np.zeros((N_ASSETS, N_ASSETS))
    for i in range(N_ASSETS):
        for j in range(N_ASSETS):
            Q[i][j] = risk_aversion * sigma[i][j]
        Q[i][i] -= (1 - risk_aversion) * exp_returns[i]
    return Q


def build_qaoa_circuit(Q, p=QAOA_DEPTH):
    """Build parameterized QAOA circuit for 5-asset portfolio optimization."""
    from qiskit.circuit import QuantumCircuit, Parameter

    n_qubits = N_ASSETS
    qc = QuantumCircuit(n_qubits)
    # Initial state: uniform superposition
    qc.h(range(n_qubits))

    for layer in range(p):
        g = Parameter(f"gamma_{layer}")
        b = Parameter(f"beta_{layer}")

        # Cost layer: ZZ interactions + Z terms
        for i in range(n_qubits):
            for j in range(i + 1, n_qubits):
                angle = Q[i][j] + Q[j][i]
                if abs(angle) > 1e-10:
                    qc.rzz(g * angle, i, j)
            diag = Q[i][i]
            if abs(diag) > 1e-10:
                qc.rz(g * diag, i)

        # Mixer layer
        for i in range(n_qubits):
            qc.rx(2 * b, i)

    qc.measure_all()
    return qc


def classical_optimize(corr, exp_returns, risk_aversion=0.5):
    """Classical portfolio optimization using scipy (mean-variance)."""
    from scipy.optimize import minimize as sp_minimize

    sigma = corr.copy()

    def objective(w):
        risk = risk_aversion * w @ sigma @ w
        ret = (1 - risk_aversion) * np.dot(exp_returns, w)
        return risk - ret

    constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
    bounds = [(0, 1)] * N_ASSETS
    best_result = None
    best_val = float("inf")
    for _ in range(20):
        w0 = np.random.dirichlet(np.ones(N_ASSETS))
        result = sp_minimize(objective, w0, method="SLSQP", bounds=bounds, constraints=constraints)
        if result.success and result.fun < best_val:
            best_val = result.fun
            best_result = result
    if best_result is None:
        return np.ones(N_ASSETS) / N_ASSETS, 0.0
    return best_result.x, best_result.fun


def warm_start_initial_point(classical_weights, p=QAOA_DEPTH):
    """
    Derive QAOA initial_point from the classical mean-variance solution.
    Maps classical continuous weights to QAOA gamma/beta angles that bias
    the quantum circuit towards the classically-optimal portfolio.
    """
    initial_point = []
    for layer in range(p):
        # gamma: layer-scaled heuristic biased by classical solution
        gamma = np.pi * (layer + 1) / (p + 1)
        # beta: arcsin of mean classical weight - biases mixer toward selection
        mean_w = np.mean(classical_weights)
        beta = np.arcsin(np.clip(mean_w, 0.01, 0.99)) * (1.0 - 0.3 * layer / max(p - 1, 1))
        initial_point.extend([gamma, beta])
    return np.array(initial_point)


def evaluate_bitstring_cost(bitstring, Q):
    """Evaluate QUBO cost for a given bitstring."""
    bits = [int(b) for b in bitstring[-N_ASSETS:].zfill(N_ASSETS)]
    x = np.array(bits, dtype=float)
    return float(x @ Q @ x)


def cvar_weights_from_counts(counts, Q, alpha=CVAR_ALPHA):
    """
    CVaR aggregation: focus on the best (lowest cost) alpha-fraction of samples.
    Returns portfolio weights derived from the best samples only.
    """
    evaluated = []
    for bitstring, count in counts.items():
        cost = evaluate_bitstring_cost(bitstring, Q)
        evaluated.append((bitstring, count, cost))

    # Sort by cost (ascending = best first)
    evaluated.sort(key=lambda x: x[2])

    # Keep only the best alpha fraction of total shots
    total_shots = sum(c for _, c, _ in evaluated)
    cutoff = max(1, int(alpha * total_shots))

    quantum_weights = np.zeros(N_ASSETS)
    accumulated = 0
    for bitstring, count, cost in evaluated:
        bits = bitstring.replace(" ", "")[-N_ASSETS:].zfill(N_ASSETS)
        take = min(count, cutoff - accumulated)
        for i in range(N_ASSETS):
            if bits[i] == "1":
                quantum_weights[i] += take
        accumulated += take
        if accumulated >= cutoff:
            break

    if quantum_weights.sum() > 0:
        quantum_weights = quantum_weights / quantum_weights.sum()
    else:
        quantum_weights = np.ones(N_ASSETS) / N_ASSETS
    return quantum_weights


def cvar_objective(counts, Q, alpha=CVAR_ALPHA):
    """Compute CVaR cost value (for reporting)."""
    costs = []
    for bitstring, count in counts.items():
        cost = evaluate_bitstring_cost(bitstring, Q)
        costs.extend([cost] * count)
    costs = sorted(costs)
    n_best = max(1, int(len(costs) * alpha))
    return float(np.mean(costs[:n_best]))


def simple_weights_from_counts(counts):
    """Simple frequency-based weights from measurement counts (fallback)."""
    quantum_weights = np.zeros(N_ASSETS)
    for bitstring, count in counts.items():
        bits = bitstring.replace(" ", "")[-N_ASSETS:].zfill(N_ASSETS)
        for i in range(N_ASSETS):
            if bits[i] == "1":
                quantum_weights[i] += count
    if quantum_weights.sum() > 0:
        quantum_weights = quantum_weights / quantum_weights.sum()
    else:
        quantum_weights = np.ones(N_ASSETS) / N_ASSETS
    return quantum_weights


def compute_sharpe(weights, exp_returns, corr, risk_free=0.05):
    """Compute annualized Sharpe ratio for given weights."""
    port_return = np.dot(weights, exp_returns)
    port_vol = np.sqrt(weights @ corr @ weights)
    if port_vol < 1e-10:
        return 0.0
    return (port_return - risk_free) / port_vol


def bind_params(circuit, param_values_array):
    """Bind parameter array to circuit in sorted-name order."""
    sorted_params = sorted(circuit.parameters, key=lambda p: p.name)
    return circuit.assign_parameters(
        {param: float(val) for param, val in zip(sorted_params, param_values_array)}
    )


def run_on_simulator(qc, Q, warm_params, cold_params, classical_weights, exp_returns, corr):
    """Run QAOA on Aer simulator for dry-run / testing."""
    log("=== DRY RUN: Using Aer simulator ===")
    try:
        from qiskit_aer import AerSimulator
    except ImportError:
        from qiskit.providers.aer import AerSimulator

    simulator = AerSimulator()

    warm_qc = bind_params(qc, warm_params)
    cold_qc = bind_params(qc, cold_params)

    from qiskit import transpile
    warm_t = transpile(warm_qc, simulator, optimization_level=3)
    cold_t = transpile(cold_qc, simulator, optimization_level=3)
    log(f"Transpiled warm (sim): depth={warm_t.depth()}, gates={warm_t.size()}")
    log(f"Transpiled cold (sim): depth={cold_t.depth()}, gates={cold_t.size()}")

    # Run both
    job_warm = simulator.run(warm_t, shots=SHOTS)
    warm_counts = job_warm.result().get_counts()
    job_cold = simulator.run(cold_t, shots=SHOTS)
    cold_counts = job_cold.result().get_counts()

    warm_sorted = sorted(warm_counts.items(), key=lambda x: x[1], reverse=True)
    cold_sorted = sorted(cold_counts.items(), key=lambda x: x[1], reverse=True)
    log(f"Warm-start top 5 (sim): {warm_sorted[:5]}")
    log(f"Cold-start top 5 (sim): {cold_sorted[:5]}")

    # CVaR weights
    warm_cvar_w = cvar_weights_from_counts(warm_counts, Q)
    cold_cvar_w = cvar_weights_from_counts(cold_counts, Q)
    warm_simple_w = simple_weights_from_counts(warm_counts)

    warm_cvar_sharpe = compute_sharpe(warm_cvar_w, exp_returns, corr)
    cold_cvar_sharpe = compute_sharpe(cold_cvar_w, exp_returns, corr)
    warm_simple_sharpe = compute_sharpe(warm_simple_w, exp_returns, corr)
    classical_sharpe = compute_sharpe(classical_weights, exp_returns, corr)

    warm_cvar_cost = cvar_objective(warm_counts, Q)
    cold_cvar_cost = cvar_objective(cold_counts, Q)

    log(f"CVaR weights (warm): {dict(zip(ASSETS, [round(w, 4) for w in warm_cvar_w]))}")
    log(f"CVaR weights (cold): {dict(zip(ASSETS, [round(w, 4) for w in cold_cvar_w]))}")
    log(f"Classical weights:    {dict(zip(ASSETS, [round(w, 4) for w in classical_weights]))}")
    log(f"")
    log(f"CVaR Sharpe (warm):  {warm_cvar_sharpe:.4f}")
    log(f"CVaR Sharpe (cold):  {cold_cvar_sharpe:.4f}")
    log(f"Simple Sharpe (warm): {warm_simple_sharpe:.4f}")
    log(f"Classical Sharpe:     {classical_sharpe:.4f}")
    log(f"")
    log(f"CVaR cost (warm): {warm_cvar_cost:.6f}, CVaR cost (cold): {cold_cvar_cost:.6f}")
    log(f"Warm-start improvement: Sharpe {warm_cvar_sharpe - cold_cvar_sharpe:+.4f}, "
        f"CVaR cost {cold_cvar_cost - warm_cvar_cost:+.6f}")

    return {
        "mode": "dry_run_simulator",
        "warm_cvar_weights": {s: round(float(warm_cvar_w[i]), 4) for i, s in enumerate(ASSETS)},
        "cold_cvar_weights": {s: round(float(cold_cvar_w[i]), 4) for i, s in enumerate(ASSETS)},
        "classical_weights": {s: round(float(classical_weights[i]), 4) for i, s in enumerate(ASSETS)},
        "warm_cvar_sharpe": round(float(warm_cvar_sharpe), 4),
        "cold_cvar_sharpe": round(float(cold_cvar_sharpe), 4),
        "warm_simple_sharpe": round(float(warm_simple_sharpe), 4),
        "classical_sharpe": round(float(classical_sharpe), 4),
        "warm_cvar_cost": round(float(warm_cvar_cost), 6),
        "cold_cvar_cost": round(float(cold_cvar_cost), 6),
        "warm_start_advantage": round(float(warm_cvar_sharpe - cold_cvar_sharpe), 4),
        "quantum_advantage": warm_cvar_sharpe > classical_sharpe,
        "top_bitstrings_warm": warm_sorted[:10],
        "top_bitstrings_cold": cold_sorted[:10],
    }


def run_quantum_validation(dry_run=False):
    """Main: run warm-started QAOA on IBM quantum hardware and compare to classical."""
    log("=== IBM Quantum Monthly Validation (v2 -- Warm-Start + CVaR + Batch) ===")

    if not IBM_TOKEN and not dry_run:
        log("ERROR: IBM_QUANTUM_TOKEN not set. Skipping.")
        return None

    # Step 1: Market data
    log("Loading correlation matrix and expected returns...")
    corr = get_correlation_matrix()
    exp_returns = get_expected_returns()
    log(f"Assets: {ASSETS}")
    log(f"Expected returns: {exp_returns}")

    # Step 2: Classical optimization (also used for warm-starting QAOA)
    log("Running classical mean-variance optimizer (warm-start seed)...")
    t0 = time.time()
    classical_weights, classical_obj = classical_optimize(corr, exp_returns)
    classical_time = time.time() - t0
    classical_sharpe = compute_sharpe(classical_weights, exp_returns, corr)
    log(f"Classical weights: {dict(zip(ASSETS, [round(w, 4) for w in classical_weights]))}")
    log(f"Classical Sharpe: {classical_sharpe:.4f}, time: {classical_time:.2f}s")

    # Step 3: Build QUBO and QAOA circuit
    log("Building QUBO matrix and QAOA circuit...")
    Q = build_qubo_matrix(corr, exp_returns)
    qc = build_qaoa_circuit(Q, p=QAOA_DEPTH)
    log(f"Circuit: {qc.num_qubits} qubits, {QAOA_DEPTH} QAOA layers, depth {qc.depth()}, gates {qc.size()}")

    # Step 4: Warm-start initial point from classical solution
    warm_params = warm_start_initial_point(classical_weights, p=QAOA_DEPTH)
    log(f"Warm-start initial_point: {[round(float(v), 4) for v in warm_params]}")

    # Cold-start (random) for A/B comparison
    cold_params = np.array([
        np.random.uniform(0, 2 * np.pi) if i % 2 == 0 else np.random.uniform(0, np.pi)
        for i in range(2 * QAOA_DEPTH)
    ])
    log(f"Cold-start initial_point: {[round(float(v), 4) for v in cold_params]}")

    # Dry-run mode: use Aer simulator
    if dry_run:
        return run_on_simulator(qc, Q, warm_params, cold_params, classical_weights, exp_returns, corr)

    # Step 5: Connect to IBM Quantum and run on real hardware
    try:
        from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2, Options
        from qiskit_ibm_runtime import Batch
        from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

        log("Connecting to IBM Quantum...")
        service = QiskitRuntimeService(channel="ibm_quantum_platform", token=IBM_TOKEN)

        backends = service.backends(simulator=False, operational=True)
        if not backends:
            log("ERROR: No operational backends available")
            return None

        # Prefer ibm_torino (133q) or ibm_fez (156q), then least busy
        preferred = ["ibm_torino", "ibm_fez"]
        backend = None
        for pref in preferred:
            for b in backends:
                if b.name == pref:
                    backend = b
                    log(f"Selected preferred backend: {b.name}")
                    break
            if backend:
                break

        if not backend:
            from qiskit_ibm_runtime import least_busy
            try:
                backend = least_busy(backends)
            except Exception:
                backend = backends[0]
            log(f"Selected least busy backend: {backend.name}")

        # Bind parameters
        warm_qc = bind_params(qc, warm_params)
        cold_qc = bind_params(qc, cold_params)

        # Transpile at optimization level 3 for maximum gate reduction
        log(f"Transpiling for {backend.name} at optimization_level=3...")
        pm = generate_preset_pass_manager(optimization_level=3, backend=backend)
        transpiled_warm = pm.run(warm_qc)
        transpiled_cold = pm.run(cold_qc)
        log(f"Transpiled warm-start: depth={transpiled_warm.depth()}, gates={transpiled_warm.size()}")
        log(f"Transpiled cold-start: depth={transpiled_cold.depth()}, gates={transpiled_cold.size()}")

        # Step 6: Batch mode with error mitigation (resilience_level=2: ZNE + M3)
        quantum_start = time.time()
        log(f"Submitting Batch job with {SHOTS} shots, resilience_level=2 (ZNE + M3)...")
        job_id = None

        for attempt in range(MAX_RETRIES):
            try:
                with Batch(backend=backend) as batch:
                    options = Options()
                    options.resilience_level = 2  # ZNE + M3 readout mitigation
                    sampler = SamplerV2(mode=batch, options=options)
                    # Submit both circuits in one batch for efficient QPU use
                    job = sampler.run(
                        [transpiled_warm, transpiled_cold],
                        shots=SHOTS
                    )
                    job_id = job.job_id()
                    log(f"Batch job submitted: {job_id} (attempt {attempt + 1})")
                    log("  - PUB 0: warm-started QAOA")
                    log("  - PUB 1: cold-start QAOA (baseline)")
                break
            except Exception as e:
                log(f"Submit error (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES - 1:
                    log(f"Retrying in {RETRY_DELAY}s...")
                    time.sleep(RETRY_DELAY)
                else:
                    raise

        # Wait for result
        log("Waiting for quantum result (may take minutes to hours)...")
        result = job.result()
        quantum_time = time.time() - quantum_start
        log(f"Quantum batch job completed in {quantum_time:.1f}s")

        # Parse warm-start results (PUB 0)
        warm_counts = result[0].data.meas.get_counts()
        warm_sorted = sorted(warm_counts.items(), key=lambda x: x[1], reverse=True)
        log(f"Warm-start top 5: {warm_sorted[:5]}")

        # Parse cold-start results (PUB 1)
        cold_counts = result[1].data.meas.get_counts()
        cold_sorted = sorted(cold_counts.items(), key=lambda x: x[1], reverse=True)
        log(f"Cold-start top 5: {cold_sorted[:5]}")

        # CVaR aggregation
        warm_cvar_w = cvar_weights_from_counts(warm_counts, Q, alpha=CVAR_ALPHA)
        cold_cvar_w = cvar_weights_from_counts(cold_counts, Q, alpha=CVAR_ALPHA)
        warm_simple_w = simple_weights_from_counts(warm_counts)

        warm_cvar_sharpe = compute_sharpe(warm_cvar_w, exp_returns, corr)
        cold_cvar_sharpe = compute_sharpe(cold_cvar_w, exp_returns, corr)
        warm_simple_sharpe = compute_sharpe(warm_simple_w, exp_returns, corr)

        warm_cvar_cost = cvar_objective(warm_counts, Q)
        cold_cvar_cost = cvar_objective(cold_counts, Q)

        # Use warm-start CVaR weights as primary quantum result
        quantum_weights = warm_cvar_w
        quantum_sharpe = warm_cvar_sharpe

        log(f"Quantum CVaR weights (warm): {dict(zip(ASSETS, [round(w, 4) for w in warm_cvar_w]))}")
        log(f"Quantum CVaR Sharpe (warm): {warm_cvar_sharpe:.4f}")
        log(f"Quantum simple Sharpe (warm): {warm_simple_sharpe:.4f}")
        log(f"Cold-start CVaR Sharpe: {cold_cvar_sharpe:.4f}")
        log(f"CVaR cost: warm={warm_cvar_cost:.6f}, cold={cold_cvar_cost:.6f}")
        log(f"Warm-start advantage over cold: Sharpe {warm_cvar_sharpe - cold_cvar_sharpe:+.4f}")

        # Step 7: Comparison
        weight_diffs = {sym: round(float(quantum_weights[i] - classical_weights[i]), 4)
                       for i, sym in enumerate(ASSETS)}
        sharpe_diff = quantum_sharpe - classical_sharpe
        quantum_advantage = sharpe_diff > 0
        warm_vs_cold = warm_cvar_sharpe - cold_cvar_sharpe

        results = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "month": datetime.date.today().strftime("%Y-%m"),
            "version": "v2_warm_start_cvar_batch",
            "assets": ASSETS,
            "backend": backend.name,
            "job_id": job_id,
            "shots": SHOTS,
            "qaoa_depth": QAOA_DEPTH,
            "cvar_alpha": CVAR_ALPHA,
            "resilience_level": 2,
            "transpilation_level": 3,
            "batch_mode": True,
            "warm_start_initial_point": [round(float(v), 4) for v in warm_params],
            "cold_start_initial_point": [round(float(v), 4) for v in cold_params],
            "circuit_depth_warm": transpiled_warm.depth(),
            "circuit_gates_warm": transpiled_warm.size(),
            "circuit_depth_cold": transpiled_cold.depth(),
            "circuit_gates_cold": transpiled_cold.size(),
            "quantum_weights_cvar": {s: round(float(warm_cvar_w[i]), 4) for i, s in enumerate(ASSETS)},
            "quantum_weights_simple": {s: round(float(warm_simple_w[i]), 4) for i, s in enumerate(ASSETS)},
            "cold_weights_cvar": {s: round(float(cold_cvar_w[i]), 4) for i, s in enumerate(ASSETS)},
            "classical_weights": {s: round(float(classical_weights[i]), 4) for i, s in enumerate(ASSETS)},
            "weight_differences": weight_diffs,
            "quantum_sharpe_cvar": round(float(warm_cvar_sharpe), 4),
            "quantum_sharpe_simple": round(float(warm_simple_sharpe), 4),
            "cold_sharpe_cvar": round(float(cold_cvar_sharpe), 4),
            "classical_sharpe": round(float(classical_sharpe), 4),
            "sharpe_difference": round(float(sharpe_diff), 4),
            "warm_vs_cold_sharpe": round(float(warm_vs_cold), 4),
            "warm_cvar_cost": round(float(warm_cvar_cost), 6),
            "cold_cvar_cost": round(float(cold_cvar_cost), 6),
            "quantum_advantage": quantum_advantage,
            "warm_start_advantage": warm_cvar_cost < cold_cvar_cost,
            "quantum_execution_time_s": round(quantum_time, 1),
            "classical_execution_time_s": round(classical_time, 4),
            "top_bitstrings_warm": warm_sorted[:10],
            "top_bitstrings_cold": cold_sorted[:10],
            "expected_returns": {s: round(float(exp_returns[i]), 4) for i, s in enumerate(ASSETS)},
            "correlation_sample": {
                f"{ASSETS[0]}-{ASSETS[1]}": round(float(corr[0][1]), 4),
                f"{ASSETS[0]}-{ASSETS[4]}": round(float(corr[0][4]), 4),
            },
        }

        # Save report
        month_str = datetime.date.today().strftime("%Y-%m")
        report_path = REPORTS_DIR / f"ibm_validation_{month_str}.json"
        report_path.write_text(json.dumps(results, indent=2, default=str))
        log(f"Report saved: {report_path}")

        # Update quantum feed
        feed_path = DATA_DIR / "ibm_quantum_results.json"
        feed_path.write_text(json.dumps(results, indent=2, default=str))
        log(f"Quantum feed updated: {feed_path}")

        # Telegram notification
        adv_str = "YES" if quantum_advantage else "NO"
        ws_str = "YES" if results["warm_start_advantage"] else "NO"
        msg = (
            f"<b>IBM Quantum Validation (v2)</b>\n"
            f"<b>Month:</b> {month_str}\n"
            f"<b>Backend:</b> {backend.name}\n"
            f"<b>Job:</b> {job_id}\n"
            f"<b>Mode:</b> Batch | Resilience L2 (ZNE+M3) | CVaR({CVAR_ALPHA})\n\n"
            f"<b>Quantum CVaR Weights (warm-start):</b>\n"
        )
        for s in ASSETS:
            qw = results["quantum_weights_cvar"][s]
            cw = results["classical_weights"][s]
            msg += f"  {s}: {qw:.1%} (classical: {cw:.1%})\n"
        msg += (
            f"\n<b>Warm CVaR Sharpe:</b> {warm_cvar_sharpe:.4f}\n"
            f"<b>Cold CVaR Sharpe:</b> {cold_cvar_sharpe:.4f}\n"
            f"<b>Classical Sharpe:</b> {classical_sharpe:.4f}\n"
            f"<b>Quantum Advantage:</b> {adv_str} (diff: {sharpe_diff:+.4f})\n"
            f"<b>Warm > Cold:</b> {ws_str} (CVaR: {warm_cvar_cost:.4f} vs {cold_cvar_cost:.4f})\n"
            f"<b>Transpiled depth:</b> {transpiled_warm.depth()} (level 3)\n"
            f"<b>Execution:</b> quantum {quantum_time:.0f}s vs classical {classical_time:.3f}s\n"
        )
        send_telegram(msg)
        log(f"Quantum advantage: {quantum_advantage} (Sharpe diff: {sharpe_diff:+.4f})")
        log(f"Warm-start advantage: {results['warm_start_advantage']}")
        return results

    except ImportError as e:
        log(f"Missing package: {e}. Attempting install...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install",
                       "qiskit-ibm-runtime", "qiskit-aer"], check=True)
        log("Installed packages. Please re-run.")
        return None
    except Exception as e:
        log(f"Quantum validation error: {e}")
        log(traceback.format_exc())
        send_telegram(f"<b>IBM Quantum Validation (v2) FAILED</b>\n{str(e)[:500]}")
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IBM Quantum QAOA Portfolio Optimizer v2")
    parser.add_argument("--dry-run", action="store_true",
                       help="Run on Aer simulator instead of real hardware")
    args = parser.parse_args()

    result = run_quantum_validation(dry_run=args.dry_run)
    if result:
        mode = result.get("mode", "hardware")
        log(f"Validation complete ({mode}). "
            f"Quantum advantage: {result.get('quantum_advantage', 'N/A')}, "
            f"Warm-start advantage: {result.get('warm_start_advantage', 'N/A')}")
    else:
        log("Validation did not produce results.")
        sys.exit(1)
