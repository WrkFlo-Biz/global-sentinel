#!/usr/bin/env python3
"""
IBM Quantum Monthly Validation
Runs a QAOA portfolio optimization circuit on real IBM quantum hardware,
compares against classical optimizer, and reports results.
Scheduled: 1st of each month at 10:00 UTC.
"""
import json, os, sys, time, datetime, traceback, urllib.request
from pathlib import Path
import numpy as np

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


def build_qaoa_circuit(corr, exp_returns, risk_aversion=0.5):
    """Build QAOA circuit for 5-asset portfolio optimization."""
    from qiskit.circuit import QuantumCircuit, Parameter

    sigma = corr.copy()
    # Build QUBO matrix
    Q = np.zeros((N_ASSETS, N_ASSETS))
    for i in range(N_ASSETS):
        for j in range(N_ASSETS):
            Q[i][j] = risk_aversion * sigma[i][j]
        Q[i][i] -= (1 - risk_aversion) * exp_returns[i]

    n_qubits = N_ASSETS
    p = 2  # QAOA depth

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
    return qc, Q


def classical_optimize(corr, exp_returns, risk_aversion=0.5):
    """Classical portfolio optimization using scipy."""
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


def compute_sharpe(weights, exp_returns, corr, risk_free=0.05):
    """Compute annualized Sharpe ratio for given weights."""
    port_return = np.dot(weights, exp_returns)
    port_vol = np.sqrt(weights @ corr @ weights)
    if port_vol < 1e-10:
        return 0.0
    return (port_return - risk_free) / port_vol


def run_quantum_validation():
    """Main: run QAOA on IBM quantum hardware and compare to classical."""
    log("=== IBM Quantum Monthly Validation ===")

    if not IBM_TOKEN:
        log("ERROR: IBM_QUANTUM_TOKEN not set. Skipping.")
        return None

    # Step 1: Market data
    log("Loading correlation matrix and expected returns...")
    corr = get_correlation_matrix()
    exp_returns = get_expected_returns()
    log(f"Assets: {ASSETS}")
    log(f"Expected returns: {exp_returns}")

    # Step 2: Classical optimization
    log("Running classical optimizer...")
    t0 = time.time()
    classical_weights, classical_obj = classical_optimize(corr, exp_returns)
    classical_time = time.time() - t0
    classical_sharpe = compute_sharpe(classical_weights, exp_returns, corr)
    log(f"Classical weights: {dict(zip(ASSETS, [round(w, 4) for w in classical_weights]))}")
    log(f"Classical Sharpe: {classical_sharpe:.4f}, time: {classical_time:.2f}s")

    # Step 3: Build quantum circuit
    log("Building QAOA circuit...")
    qc, Q = build_qaoa_circuit(corr, exp_returns)
    log(f"Circuit: {qc.num_qubits} qubits, depth {qc.depth()}, gates {qc.size()}")

    # Step 4: Connect to IBM Quantum and run on real hardware
    try:
        from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2 as Sampler
        from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

        log("Connecting to IBM Quantum...")
        service = QiskitRuntimeService(channel="ibm_quantum", token=IBM_TOKEN)

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

        # Bind parameters with random initial values
        param_values = {}
        for param in qc.parameters:
            if "gamma" in param.name:
                param_values[param] = np.random.uniform(0, 2 * np.pi)
            else:
                param_values[param] = np.random.uniform(0, np.pi)
        bound_qc = qc.assign_parameters(param_values)

        # Transpile for target backend
        log(f"Transpiling for {backend.name}...")
        pm = generate_preset_pass_manager(backend=backend, optimization_level=2)
        transpiled = pm.run(bound_qc)
        log(f"Transpiled: depth={transpiled.depth()}, gates={transpiled.size()}")

        # Submit job with retries
        quantum_start = time.time()
        log(f"Submitting Sampler job with {SHOTS} shots...")
        job_id = None

        for attempt in range(MAX_RETRIES):
            try:
                sampler = Sampler(mode=backend)
                job = sampler.run([transpiled], shots=SHOTS)
                job_id = job.job_id()
                log(f"Job submitted: {job_id} (attempt {attempt + 1})")
                break
            except Exception as e:
                log(f"Submit error (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES - 1:
                    log(f"Retrying in {RETRY_DELAY}s...")
                    time.sleep(RETRY_DELAY)
                else:
                    raise

        # Wait for result (may take minutes to hours in queue)
        log("Waiting for quantum result (may take minutes to hours)...")
        result = job.result()
        quantum_time = time.time() - quantum_start
        log(f"Quantum job completed in {quantum_time:.1f}s")

        # Parse results
        pub_result = result[0]
        counts = pub_result.data.meas.get_counts()
        sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        log(f"Top 5 bitstrings: {sorted_counts[:5]}")

        # Convert bitstring distribution to portfolio weights
        quantum_weights = np.zeros(N_ASSETS)
        for bitstring, count in counts.items():
            bits = bitstring.replace(" ", "")[-N_ASSETS:]
            bits = bits.zfill(N_ASSETS)
            for i in range(N_ASSETS):
                if bits[i] == "1":
                    quantum_weights[i] += count

        if quantum_weights.sum() > 0:
            quantum_weights = quantum_weights / quantum_weights.sum()
        else:
            quantum_weights = np.ones(N_ASSETS) / N_ASSETS

        quantum_sharpe = compute_sharpe(quantum_weights, exp_returns, corr)
        log(f"Quantum weights: {dict(zip(ASSETS, [round(w, 4) for w in quantum_weights]))}")
        log(f"Quantum Sharpe: {quantum_sharpe:.4f}")

        # Step 5: Comparison
        weight_diffs = {sym: round(float(quantum_weights[i] - classical_weights[i]), 4)
                       for i, sym in enumerate(ASSETS)}
        sharpe_diff = quantum_sharpe - classical_sharpe
        quantum_advantage = sharpe_diff > 0

        results = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "month": datetime.date.today().strftime("%Y-%m"),
            "assets": ASSETS,
            "backend": backend.name,
            "job_id": job_id,
            "shots": SHOTS,
            "circuit_depth": transpiled.depth(),
            "circuit_gates": transpiled.size(),
            "quantum_weights": {s: round(float(quantum_weights[i]), 4) for i, s in enumerate(ASSETS)},
            "classical_weights": {s: round(float(classical_weights[i]), 4) for i, s in enumerate(ASSETS)},
            "weight_differences": weight_diffs,
            "quantum_sharpe": round(float(quantum_sharpe), 4),
            "classical_sharpe": round(float(classical_sharpe), 4),
            "sharpe_difference": round(float(sharpe_diff), 4),
            "quantum_advantage": quantum_advantage,
            "quantum_execution_time_s": round(quantum_time, 1),
            "classical_execution_time_s": round(classical_time, 4),
            "top_bitstrings": sorted_counts[:10],
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
        msg = (
            f"<b>IBM Quantum Monthly Validation</b>\n"
            f"<b>Month:</b> {month_str}\n"
            f"<b>Backend:</b> {backend.name}\n"
            f"<b>Job:</b> {job_id}\n\n"
            f"<b>Quantum Weights:</b>\n"
        )
        for s, w in results["quantum_weights"].items():
            cw = results["classical_weights"][s]
            msg += f"  {s}: {w:.1%} (classical: {cw:.1%})\n"
        msg += (
            f"\n<b>Quantum Sharpe:</b> {quantum_sharpe:.4f}\n"
            f"<b>Classical Sharpe:</b> {classical_sharpe:.4f}\n"
            f"<b>Quantum Advantage:</b> {adv_str} (diff: {sharpe_diff:+.4f})\n"
            f"<b>Execution:</b> quantum {quantum_time:.0f}s vs classical {classical_time:.3f}s\n"
        )
        send_telegram(msg)
        log(f"Quantum advantage: {quantum_advantage} (Sharpe diff: {sharpe_diff:+.4f})")
        return results

    except ImportError as e:
        log(f"Missing package: {e}. Attempting install...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "qiskit-ibm-runtime"], check=True)
        log("Installed qiskit-ibm-runtime. Please re-run.")
        return None
    except Exception as e:
        log(f"Quantum validation error: {e}")
        log(traceback.format_exc())
        send_telegram(f"<b>IBM Quantum Validation FAILED</b>\n{str(e)[:500]}")
        return None


if __name__ == "__main__":
    result = run_quantum_validation()
    if result:
        log(f"Validation complete. Advantage: {result.get('quantum_advantage')}")
    else:
        log("Validation did not produce results.")
        sys.exit(1)
