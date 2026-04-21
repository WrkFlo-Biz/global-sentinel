#!/usr/bin/env python3
"""
Topological Graph Diffusion Arbitrage Algorithm
Inspired by @joshuaaalampour's approach: uses TDA + graph diffusion to find
statistical arbitrage opportunities across correlated assets.
"""
import json, os, datetime, warnings, traceback
import numpy as np
from pathlib import Path

warnings.filterwarnings("ignore")
REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
OUTPUT_PATH = REPO_ROOT / "data/quantum_feed/topo_arb_signals.json"

WATCHLIST = [
    "SPY","QQQ","AAPL","MSFT","NVDA","AMD","META","GOOGL","AMZN","TSLA",
    "XLE","XOM","CVX","OXY","USO","XLF","JPM","GS","BAC","DAL","UAL",
    "LMT","RTX","BA","GLD","TLT","PLTR","COIN","CCL","SOXL"
]

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def log(msg):
    print(f"[{iso_now()}] TOPO_ARB: {msg}", flush=True)

def fetch_returns(symbols, days=90):
    """Fetch daily returns via yfinance."""
    try:
        import yfinance as yf
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days + 10)
        data = yf.download(symbols, start=start, end=end, progress=False, auto_adjust=True)
        if hasattr(data.columns, 'levels'):
            prices = data['Close'] if 'Close' in data.columns.get_level_values(0) else data
        else:
            prices = data
        returns = prices.pct_change().dropna()
        return returns.iloc[-days:] if len(returns) > days else returns
    except Exception as e:
        log(f"yfinance error: {e}")
        return None

def build_correlation_graph(returns, threshold=0.5):
    """Build weighted graph from correlation matrix."""
    import networkx as nx
    corr = returns.corr()
    G = nx.Graph()
    symbols = list(corr.columns)
    for s in symbols:
        G.add_node(s)
    for i, s1 in enumerate(symbols):
        for j, s2 in enumerate(symbols):
            if i < j and abs(corr.iloc[i, j]) > threshold:
                G.add_edge(s1, s2, weight=abs(corr.iloc[i, j]))
    return G, corr

def compute_persistent_homology(corr_matrix):
    """Compute persistent homology on the correlation distance matrix."""
    try:
        from ripser import ripser
        # Convert correlation to distance: d = 1 - |corr|
        dist_matrix = 1.0 - np.abs(corr_matrix.values)
        np.fill_diagonal(dist_matrix, 0)
        result = ripser(dist_matrix, maxdim=1, distance_matrix=True)
        diagrams = result['dgms']

        # Extract topological features
        h0 = diagrams[0]  # Connected components
        h1 = diagrams[1] if len(diagrams) > 1 else np.array([])  # Loops

        # Betti numbers at various thresholds
        betti_0 = len(h0[h0[:, 1] == np.inf]) if len(h0) > 0 else 0
        betti_1 = len(h1) if len(h1) > 0 else 0

        # Persistence entropy (measure of topological complexity)
        lifetimes_0 = h0[h0[:, 1] != np.inf][:, 1] - h0[h0[:, 1] != np.inf][:, 0] if len(h0) > 0 else np.array([0])
        lifetimes_1 = h1[:, 1] - h1[:, 0] if len(h1) > 0 else np.array([0])

        total_life_0 = lifetimes_0.sum() if len(lifetimes_0) > 0 else 1
        probs_0 = lifetimes_0 / total_life_0 if total_life_0 > 0 else np.array([1])
        entropy_0 = -np.sum(probs_0 * np.log(probs_0 + 1e-10))

        total_life_1 = lifetimes_1.sum() if len(lifetimes_1) > 0 else 1
        probs_1 = lifetimes_1 / total_life_1 if total_life_1 > 0 else np.array([1])
        entropy_1 = -np.sum(probs_1 * np.log(probs_1 + 1e-10))

        return {
            "betti_0": int(betti_0),
            "betti_1": int(betti_1),
            "persistence_entropy_h0": round(float(entropy_0), 4),
            "persistence_entropy_h1": round(float(entropy_1), 4),
            "num_h0_features": len(h0),
            "num_h1_features": len(h1),
            "h0_lifetimes": lifetimes_0.tolist()[:10],
            "h1_lifetimes": lifetimes_1.tolist()[:10],
        }
    except ImportError:
        log("ripser not installed, using simplified topology")
        return {"betti_0": 0, "betti_1": 0, "persistence_entropy_h0": 0, "persistence_entropy_h1": 0}

def graph_diffusion(G, t=1.0):
    """Apply heat kernel diffusion on the graph."""
    import networkx as nx
    from scipy.linalg import expm

    if len(G.nodes()) == 0:
        return {}, {}

    nodes = sorted(G.nodes())
    n = len(nodes)
    node_idx = {s: i for i, s in enumerate(nodes)}

    # Build adjacency and Laplacian
    A = np.zeros((n, n))
    for u, v, d in G.edges(data=True):
        i, j = node_idx[u], node_idx[v]
        w = d.get('weight', 1.0)
        A[i, j] = w
        A[j, i] = w

    D = np.diag(A.sum(axis=1))
    L = D - A

    # Heat kernel: H(t) = exp(-tL)
    H = expm(-t * L)

    # Diffusion coordinates: each row is a node's position in diffusion space
    diffusion_coords = {}
    for s in nodes:
        i = node_idx[s]
        diffusion_coords[s] = H[i, :].tolist()

    # Cluster by diffusion proximity using simple k-means
    from sklearn.cluster import KMeans
    X = np.array([H[node_idx[s], :] for s in nodes])
    n_clusters = min(5, max(2, n // 5))
    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = km.fit_predict(X)

    clusters = {}
    for s, label in zip(nodes, labels):
        clusters[s] = int(label)

    # Compute centroid distances (anomaly = far from cluster center)
    centroids = km.cluster_centers_
    anomaly_scores = {}
    for s in nodes:
        i = node_idx[s]
        cluster_id = clusters[s]
        dist = np.linalg.norm(X[i] - centroids[cluster_id])
        anomaly_scores[s] = round(float(dist), 6)

    return clusters, anomaly_scores

def generate_signals(returns, clusters, anomaly_scores, corr):
    """Generate arbitrage signals from topological analysis."""
    signals = []
    symbols = list(returns.columns)

    # Recent performance (last 5 days vs last 20 days)
    recent_5d = returns.iloc[-5:].mean() if len(returns) >= 5 else returns.mean()
    recent_20d = returns.iloc[-20:].mean() if len(returns) >= 20 else returns.mean()

    # Cluster peer performance
    cluster_groups = {}
    for s, c in clusters.items():
        cluster_groups.setdefault(c, []).append(s)

    cluster_avg_return = {}
    for c, members in cluster_groups.items():
        valid = [s for s in members if s in recent_5d.index]
        if valid:
            cluster_avg_return[c] = recent_5d[valid].mean()

    for sym in symbols:
        if sym not in clusters or sym not in anomaly_scores:
            continue

        cluster_id = clusters[sym]
        anomaly = anomaly_scores[sym]
        sym_return = recent_5d.get(sym, 0)
        cluster_return = cluster_avg_return.get(cluster_id, 0)

        # Divergence from cluster peers
        divergence = sym_return - cluster_return

        # Mean reversion signal: if stock underperforms cluster = long, overperforms = short
        if abs(divergence) > 0.002:  # Minimum divergence threshold
            direction = "long" if divergence < 0 else "short"
            # Expected reversion: half the divergence
            expected_reversion = abs(divergence) * 0.5 * 100

            # Confidence based on anomaly score and cluster cohesion
            cluster_size = len(cluster_groups.get(cluster_id, []))
            confidence = min(1.0, anomaly * 10 + cluster_size / 10)

            signals.append({
                "symbol": sym,
                "direction": direction,
                "anomaly_score": round(anomaly, 4),
                "cluster_id": cluster_id,
                "divergence_from_cluster": round(float(divergence) * 100, 4),
                "expected_reversion_pct": round(expected_reversion, 2),
                "confidence": round(confidence, 3),
                "cluster_peers": cluster_groups.get(cluster_id, [])[:5],
                "recent_5d_return": round(float(sym_return) * 100, 4),
                "cluster_avg_return": round(float(cluster_return) * 100, 4),
            })

    # Sort by confidence * expected_reversion (best opportunities first)
    signals.sort(key=lambda x: x["confidence"] * x["expected_reversion_pct"], reverse=True)
    return signals[:10]  # Top 10

def run_backtest(returns, lookback=60, forward=5):
    """Simple walk-forward backtest of the strategy."""
    if len(returns) < lookback + forward:
        return {"error": "insufficient data", "total_return": 0}

    total_pnl = 0
    trades = 0
    wins = 0

    for start in range(0, len(returns) - lookback - forward, forward):
        train = returns.iloc[start:start + lookback]
        test = returns.iloc[start + lookback:start + lookback + forward]

        try:
            G, corr = build_correlation_graph(train, threshold=0.4)
            clusters, anomaly_scores = graph_diffusion(G, t=1.0)
            signals = generate_signals(train, clusters, anomaly_scores, corr)

            for sig in signals[:3]:  # Top 3 signals per window
                sym = sig["symbol"]
                if sym not in test.columns:
                    continue
                period_return = test[sym].sum()
                if sig["direction"] == "long":
                    pnl = period_return
                else:
                    pnl = -period_return
                total_pnl += pnl
                trades += 1
                if pnl > 0:
                    wins += 1
        except Exception:
            continue

    return {
        "total_return_pct": round(float(total_pnl) * 100, 2),
        "trades": trades,
        "win_rate": round(wins / max(1, trades), 3),
        "avg_return_per_trade": round(float(total_pnl) / max(1, trades) * 100, 4),
    }

def run_full_analysis():
    """Run the complete topological graph diffusion arbitrage analysis."""
    log("Starting topological graph diffusion arbitrage analysis...")

    # 1. Fetch data
    returns = fetch_returns(WATCHLIST, days=90)
    if returns is None or returns.empty:
        log("No return data available")
        return None

    valid_symbols = [s for s in WATCHLIST if s in returns.columns]
    returns = returns[valid_symbols]
    log(f"Loaded {len(returns)} days of returns for {len(valid_symbols)} symbols")

    # 2. Build correlation graph
    G, corr = build_correlation_graph(returns, threshold=0.4)
    log(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # 3. Compute persistent homology
    topo_features = compute_persistent_homology(corr)
    log(f"Topology: H0={topo_features['betti_0']}, H1={topo_features['betti_1']}, "
        f"entropy_H0={topo_features['persistence_entropy_h0']:.3f}, "
        f"entropy_H1={topo_features['persistence_entropy_h1']:.3f}")

    # 4. Graph diffusion
    clusters, anomaly_scores = graph_diffusion(G, t=1.0)
    n_clusters = len(set(clusters.values())) if clusters else 0
    log(f"Diffusion: {n_clusters} clusters identified")

    # 5. Generate signals
    signals = generate_signals(returns, clusters, anomaly_scores, corr)
    log(f"Generated {len(signals)} arbitrage signals")

    # 6. Backtest
    backtest = run_backtest(returns, lookback=60, forward=5)
    log(f"Backtest: {backtest.get('total_return_pct', 0):.2f}% total, "
        f"{backtest.get('trades', 0)} trades, "
        f"{backtest.get('win_rate', 0):.1%} win rate")

    # 7. Save output
    output = {
        "timestamp": iso_now(),
        "topology": topo_features,
        "num_clusters": n_clusters,
        "cluster_assignments": clusters,
        "top_signals": signals,
        "backtest": backtest,
        "symbols_analyzed": len(valid_symbols),
        "graph_edges": G.number_of_edges(),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    log(f"Output saved to {OUTPUT_PATH}")

    # Print top signals
    for i, sig in enumerate(signals[:5]):
        log(f"  #{i+1}: {sig['direction'].upper()} {sig['symbol']} | "
            f"anomaly={sig['anomaly_score']:.4f} | "
            f"divergence={sig['divergence_from_cluster']:.2f}% | "
            f"expected_reversion={sig['expected_reversion_pct']:.2f}% | "
            f"cluster={sig['cluster_id']}")

    return output

if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        log("Running quick test with synthetic data...")
        np.random.seed(42)
        n_days, n_assets = 90, 10
        base = np.random.randn(n_days, 3) * 0.01
        returns_data = {}
        syms = WATCHLIST[:n_assets]
        for i, s in enumerate(syms):
            factor_weights = np.random.randn(3)
            returns_data[s] = (base @ factor_weights) + np.random.randn(n_days) * 0.005
        import pandas as pd
        test_returns = pd.DataFrame(returns_data)

        G, corr = build_correlation_graph(test_returns, threshold=0.3)
        log(f"Test graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

        topo = compute_persistent_homology(corr)
        log(f"Test topology: {topo}")

        clusters, anomaly_scores = graph_diffusion(G, t=1.0)
        log(f"Test clusters: {clusters}")

        signals = generate_signals(test_returns, clusters, anomaly_scores, corr)
        log(f"Test signals: {len(signals)}")
        for s in signals[:3]:
            log(f"  {s['direction']} {s['symbol']} anomaly={s['anomaly_score']}")
        log("Test PASSED")
    else:
        run_full_analysis()
