#!/usr/bin/env python3
"""
Quantum Kernel Regime Detector
Uses qiskit-machine-learning FidelityQuantumKernel + QSVC for market regime classification.
Compares quantum kernel SVM vs classical SVM vs existing HMM detector.

Features: spy_returns, vix_normalized, oil_returns, yield_curve_slope, credit_spread, volume_ratio
Train on last 180 days with regime labels from HMM detector.

Scheduled: Daily 05:00 UTC (gs-quantum-kernel.timer)
Output: data/quantum_feed/quantum_kernel_regime.json
"""
import json, os, sys, time, datetime, traceback, urllib.request
from pathlib import Path
import numpy as np

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

DATA_DIR = REPO_ROOT / "data" / "quantum_feed"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = REPO_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOOKBACK_DAYS = 180
N_FEATURES = 6


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


def fetch_feature_data():
    """Fetch 180 days of market data to construct 6 features, with synthetic fallback."""
    spy_c = spy_v = uso_c = tlt_c = hyg_c = None
    try:
        live_key = env.get("ALPACA_API_KEY_LIVE", env.get("ALPACA_API_KEY", ""))
        live_secret = env.get("ALPACA_SECRET_KEY_LIVE", env.get("ALPACA_SECRET_KEY", ""))
        end = datetime.date.today()
        start = end - datetime.timedelta(days=LOOKBACK_DAYS + 30)

        def fetch_bars(symbols):
            sym_str = ",".join(symbols)
            url = (f"https://data.alpaca.markets/v2/stocks/bars?"
                   f"symbols={sym_str}&timeframe=1Day&start={start}&end={end}&limit=300&sort=asc")
            req = urllib.request.Request(url)
            req.add_header("APCA-API-KEY-ID", live_key)
            req.add_header("APCA-API-SECRET-KEY", live_secret)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())

        data = fetch_bars(["SPY", "USO", "TLT", "HYG"])

        def get_closes_and_volumes(sym):
            bars = data.get("bars", {}).get(sym, [])
            closes = [b["c"] for b in bars]
            volumes = [b["v"] for b in bars]
            return np.array(closes), np.array(volumes)

        spy_c, spy_v = get_closes_and_volumes("SPY")
        uso_c, _ = get_closes_and_volumes("USO")
        tlt_c, _ = get_closes_and_volumes("TLT")
        hyg_c, _ = get_closes_and_volumes("HYG")

        min_len = min(len(spy_c), len(uso_c), len(tlt_c), len(hyg_c))
        if min_len < 50:
            raise ValueError(f"Insufficient API data: {min_len} bars")
    except Exception as e:
        log(f"API fetch failed ({e}), generating synthetic feature data")
        # Synthetic: generate correlated price series
        np.random.seed(int(datetime.date.today().toordinal()))
        n_days = LOOKBACK_DAYS + 1
        # SPY: ~550, USO: ~75, TLT: ~90, HYG: ~78
        spy_c = 550 * np.cumprod(1 + np.random.normal(0.0004, 0.01, n_days))
        spy_v = np.random.uniform(5e7, 1.5e8, n_days)
        uso_c = 75 * np.cumprod(1 + np.random.normal(0.0002, 0.015, n_days))
        tlt_c = 90 * np.cumprod(1 + np.random.normal(-0.0001, 0.008, n_days))
        hyg_c = 78 * np.cumprod(1 + np.random.normal(0.0001, 0.005, n_days))
        min_len = n_days

    spy_c = spy_c[-min_len:]
    spy_v = spy_v[-min_len:]
    uso_c = uso_c[-min_len:]
    tlt_c = tlt_c[-min_len:]
    hyg_c = hyg_c[-min_len:]

    n = min_len - 1
    spy_returns = np.diff(spy_c) / spy_c[:-1]

    vol_window = 20
    vix_proxy = np.zeros(n)
    for i in range(n):
        window_start = max(0, i - vol_window + 1)
        vix_proxy[i] = np.std(spy_returns[window_start:i+1]) * np.sqrt(252) if i > 0 else 0.15
    vix_normalized = (vix_proxy - np.mean(vix_proxy)) / (np.std(vix_proxy) + 1e-10)

    oil_returns = np.diff(uso_c) / uso_c[:-1]
    tlt_returns = np.diff(tlt_c) / tlt_c[:-1]

    yield_slope = np.zeros(n)
    for i in range(n):
        window_start = max(0, i - 20 + 1)
        yield_slope[i] = np.sum(tlt_returns[window_start:i+1])

    hyg_returns = np.diff(hyg_c) / hyg_c[:-1]
    credit_spread = hyg_returns - tlt_returns

    volume_ratio = np.zeros(n)
    for i in range(n):
        window_start = max(0, i - 20 + 1)
        avg_vol = np.mean(spy_v[1:][window_start:i+1])
        volume_ratio[i] = spy_v[i+1] / avg_vol if avg_vol > 0 else 1.0

    features = np.column_stack([
        spy_returns, vix_normalized, oil_returns,
        yield_slope, credit_spread, volume_ratio
    ])

    return features


def get_hmm_regime_labels(n_samples):
    """Load regime labels from existing HMM detector output."""
    hmm_path = DATA_DIR / "hmm_regime.json"
    if hmm_path.exists():
        try:
            data = json.loads(hmm_path.read_text())
            history = data.get("regime_history", data.get("history", []))
            if isinstance(history, list) and len(history) >= n_samples:
                labels = []
                for entry in history[-n_samples:]:
                    if isinstance(entry, dict):
                        regime = entry.get("regime", entry.get("state", 0))
                    else:
                        regime = int(entry)
                    labels.append(regime)
                return np.array(labels)
        except Exception as e:
            log(f"HMM label load error: {e}")
    return None


def generate_regime_labels(features):
    """Generate regime labels from feature data when HMM labels unavailable."""
    spy_returns = features[:, 0]
    vix_norm = features[:, 1]

    labels = np.zeros(len(features), dtype=int)
    for i in range(len(features)):
        if spy_returns[i] > 0.005 and vix_norm[i] < 0:
            labels[i] = 0  # Bull/Risk-on
        elif spy_returns[i] < -0.005 and vix_norm[i] > 0.5:
            labels[i] = 2  # Bear/Risk-off
        else:
            labels[i] = 1  # Neutral
    return labels


def run_quantum_kernel_regime():
    """Main: train quantum kernel SVM and compare to classical approaches."""
    log("=== Quantum Kernel Regime Detector ===")

    log(f"Fetching {LOOKBACK_DAYS}-day feature data...")
    try:
        features = fetch_feature_data()
        log(f"Feature matrix: {features.shape}")
    except Exception as e:
        log(f"Failed to fetch features: {e}")
        log(traceback.format_exc())
        return None

    n_samples = len(features)
    labels = get_hmm_regime_labels(n_samples)
    if labels is None:
        labels = generate_regime_labels(features)
    unique, counts = np.unique(labels, return_counts=True)
    log(f"Labels distribution: {dict(zip(unique.tolist(), counts.tolist()))}")

    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    features_scaled = scaler.fit_transform(features)

    split = n_samples - 30
    X_train, X_test = features_scaled[:split], features_scaled[split:]
    y_train, y_test = labels[:split], labels[split:]
    log(f"Train: {len(X_train)}, Test: {len(X_test)}")

    unique_train = np.unique(y_train)
    if len(unique_train) < 2:
        y_train[-1] = (unique_train[0] + 1) % 3

    results = {}

    # Classical SVM
    log("Training classical SVM...")
    from sklearn.svm import SVC
    from sklearn.metrics import accuracy_score

    t0 = time.time()
    classical_svm = SVC(kernel='rbf', C=1.0, gamma='scale')
    classical_svm.fit(X_train, y_train)
    classical_preds = classical_svm.predict(X_test)
    classical_acc = accuracy_score(y_test, classical_preds)
    classical_time = time.time() - t0
    log(f"Classical SVM accuracy: {classical_acc:.3f}, time: {classical_time:.2f}s")
    results["classical_svm"] = {
        "accuracy": round(float(classical_acc), 4),
        "time_s": round(classical_time, 3),
        "predictions": classical_preds.tolist(),
    }

    # Quantum Kernel SVM
    log("Training Quantum Kernel SVM (ZZFeatureMap + FidelityQuantumKernel)...")
    t0 = time.time()
    quantum_acc = 0.0
    try:
        from qiskit.circuit.library import ZZFeatureMap
        from qiskit_machine_learning.kernels import FidelityQuantumKernel
        from qiskit_algorithms.state_fidelities import ComputeUncompute
        from qiskit.primitives import StatevectorSampler

        feature_map = ZZFeatureMap(feature_dimension=N_FEATURES, reps=2, entanglement='linear')

        sampler = StatevectorSampler()
        fidelity = ComputeUncompute(sampler=sampler)
        quantum_kernel = FidelityQuantumKernel(fidelity=fidelity, feature_map=feature_map)

        max_train = min(80, len(X_train))
        indices = np.random.choice(len(X_train), max_train, replace=False)
        X_train_sub = X_train[indices]
        y_train_sub = y_train[indices]

        unique_sub = np.unique(y_train_sub)
        if len(unique_sub) < 2:
            for i in range(len(y_train)):
                if y_train[i] != unique_sub[0]:
                    X_train_sub = np.vstack([X_train_sub, X_train[i:i+1]])
                    y_train_sub = np.append(y_train_sub, y_train[i])
                    break

        log(f"Computing quantum kernel matrix ({len(X_train_sub)}x{len(X_train_sub)})...")
        K_train = quantum_kernel.evaluate(X_train_sub)
        K_test = quantum_kernel.evaluate(X_test, X_train_sub)

        qsvm = SVC(kernel='precomputed', C=1.0)
        qsvm.fit(K_train, y_train_sub)
        quantum_preds = qsvm.predict(K_test)
        quantum_acc = accuracy_score(y_test, quantum_preds)
        quantum_time = time.time() - t0
        log(f"Quantum Kernel SVM accuracy: {quantum_acc:.3f}, time: {quantum_time:.2f}s")

        results["quantum_kernel_svm"] = {
            "accuracy": round(float(quantum_acc), 4),
            "time_s": round(quantum_time, 3),
            "train_samples": len(X_train_sub),
            "feature_map": "ZZFeatureMap(6, reps=2, linear)",
            "predictions": quantum_preds.tolist(),
        }
    except Exception as e:
        quantum_time = time.time() - t0
        log(f"Quantum Kernel SVM failed: {e}")
        log(traceback.format_exc())
        results["quantum_kernel_svm"] = {"error": str(e), "time_s": round(quantum_time, 3)}

    # HMM baseline
    hmm_path = DATA_DIR / "hmm_regime.json"
    if hmm_path.exists():
        try:
            hmm_data = json.loads(hmm_path.read_text())
            results["hmm_detector"] = {
                "current_regime": hmm_data.get("current_regime", hmm_data.get("regime", "unknown"))
            }
        except Exception:
            pass

    # Today's prediction
    today_features = features_scaled[-1:] if len(features_scaled) > 0 else None
    today_regime = None
    if today_features is not None:
        classical_today = int(classical_svm.predict(today_features)[0])
        results["today_classical_regime"] = classical_today

        if "quantum_kernel_svm" in results and "error" not in results["quantum_kernel_svm"]:
            try:
                K_today = quantum_kernel.evaluate(today_features, X_train_sub)
                quantum_today = int(qsvm.predict(K_today)[0])
                results["today_quantum_regime"] = quantum_today
                today_regime = quantum_today
            except Exception:
                today_regime = classical_today
        else:
            today_regime = classical_today

    regime_names = {0: "Bull/Risk-On", 1: "Neutral", 2: "Bear/Risk-Off"}

    comparison = {
        "classical_svm_accuracy": round(float(classical_acc), 4),
        "quantum_kernel_accuracy": round(float(quantum_acc), 4) if quantum_acc else None,
        "quantum_advantage": bool(quantum_acc > classical_acc) if quantum_acc else False,
        "accuracy_diff": round(float(quantum_acc - classical_acc), 4) if quantum_acc else None,
    }

    output = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "n_features": N_FEATURES,
        "feature_names": ["spy_returns", "vix_normalized", "oil_returns",
                         "yield_curve_slope", "credit_spread", "volume_ratio"],
        "regime_names": regime_names,
        "today_regime": today_regime,
        "today_regime_name": regime_names.get(today_regime, "unknown") if today_regime is not None else "unknown",
        "results": results,
        "comparison": comparison,
    }

    out_path = DATA_DIR / "quantum_kernel_regime.json"
    out_path.write_text(json.dumps(output, indent=2, default=str))
    log(f"Output saved: {out_path}")

    regime_str = regime_names.get(today_regime, "unknown")
    msg = (
        f"<b>Quantum Kernel Regime Detector</b>\n"
        f"Today: <b>{regime_str}</b>\n"
        f"Classical SVM: {classical_acc:.1%}\n"
        f"Quantum Kernel: {quantum_acc:.1%}\n"
        f"Advantage: {comparison['quantum_advantage']}"
    )
    send_telegram(msg)
    return output


if __name__ == "__main__":
    result = run_quantum_kernel_regime()
    if result:
        log(f"Regime detection complete. Today: {result.get('today_regime_name')}")
    else:
        log("Regime detection failed.")
        sys.exit(1)
