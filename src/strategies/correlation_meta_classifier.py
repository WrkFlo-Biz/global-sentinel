#!/usr/bin/env python3
"""
Correlation-Aware Meta-Classifier for Strategy Capital Allocation
Based on @joshuaaalampour concept: weight strategies by UNCORRELATION, not just performance.

Key insight: A mediocre uncorrelated strategy is more valuable than a good correlated one.
This is Markowitz portfolio theory applied to STRATEGIES instead of stocks.

Feeds into quantum training as a feature for the continuous learner.
"""
import json, os, datetime, numpy as np
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF = REPO_ROOT / "data/quantum_feed"
OUTPUT = QF / "strategy_correlation_weights.json"

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def load_strategy_returns():
    """Load daily returns per strategy from synthetic trades + paper trades."""
    returns = {}
    
    # From synthetic simulator
    syn_path = REPO_ROOT / "data/synthetic_trades"
    if syn_path.exists():
        for f in sorted(syn_path.glob("trades_*.json"))[-30:]:
            try:
                data = json.loads(f.read_text())
                for t in data.get("trades", []):
                    strat = t.get("strategy", "unknown")
                    pnl = t.get("pnl_pct", 0)
                    returns.setdefault(strat, []).append(pnl)
            except:
                pass
    
    # From strategy outputs
    for f in QF.glob("strategy_*.json"):
        try:
            data = json.loads(f.read_text())
            strat = data.get("strategy", f.stem)
            signals = data.get("signals", [])
            for s in signals:
                score = s.get("score", s.get("ensemble_signal", s.get("alpha_score", 0)))
                returns.setdefault(strat, []).append(float(score) if score else 0)
        except:
            pass
    
    return returns

def compute_correlation_matrix(returns):
    """Compute correlation matrix between strategy return streams."""
    strategies = sorted(returns.keys())
    if len(strategies) < 2:
        return {}, strategies
    
    # Pad to same length
    max_len = max(len(v) for v in returns.values())
    matrix_data = []
    for s in strategies:
        r = returns[s]
        padded = r + [0] * (max_len - len(r))
        matrix_data.append(padded[:max_len])
    
    arr = np.array(matrix_data)
    if arr.shape[1] < 2:
        return {}, strategies
    
    corr = np.corrcoef(arr)
    corr = np.nan_to_num(corr, nan=0)
    
    corr_dict = {}
    for i, s1 in enumerate(strategies):
        for j, s2 in enumerate(strategies):
            if i < j:
                corr_dict[f"{s1}_vs_{s2}"] = round(float(corr[i, j]), 4)
    
    return corr_dict, strategies

def compute_meta_weights(returns, corr_dict, strategies):
    """
    Compute capital allocation weights using correlation-aware approach.
    
    Logic: weight = performance_score * diversification_bonus
    - Performance: rolling Sharpe-like metric
    - Diversification: average INVERSE correlation with all other strategies
    - High diversification bonus for uncorrelated strategies
    """
    weights = {}
    
    for strat in strategies:
        r = returns.get(strat, [])
        if not r or len(r) < 5:
            weights[strat] = 0.0
            continue
        
        # Performance score (simplified Sharpe)
        mean_r = np.mean(r)
        std_r = np.std(r) + 1e-10
        perf_score = mean_r / std_r
        
        # Diversification bonus: how uncorrelated is this strategy with others?
        avg_corr = 0
        count = 0
        for key, corr_val in corr_dict.items():
            if strat in key:
                avg_corr += abs(corr_val)
                count += 1
        avg_corr = avg_corr / max(1, count)
        
        # Diversification bonus: less correlated = higher bonus
        # 0 correlation = 2x bonus, 1.0 correlation = 0.5x bonus
        div_bonus = 2.0 - avg_corr * 1.5
        
        # Combined weight
        raw_weight = max(0, perf_score * div_bonus)
        weights[strat] = round(raw_weight, 4)
    
    # Normalize to sum to 1
    total = sum(weights.values())
    if total > 0:
        weights = {k: round(v / total, 4) for k, v in weights.items()}
    
    return weights

def run():
    returns = load_strategy_returns()
    
    if not returns:
        print("No strategy returns data yet")
        return
    
    corr_dict, strategies = compute_correlation_matrix(returns)
    weights = compute_meta_weights(returns, corr_dict, strategies)
    
    # Sort by weight
    sorted_weights = dict(sorted(weights.items(), key=lambda x: x[1], reverse=True))
    
    output = {
        "timestamp": iso_now(),
        "strategies_analyzed": len(strategies),
        "correlation_matrix": corr_dict,
        "allocation_weights": sorted_weights,
        "method": "correlation_aware_meta_classifier",
        "insight": "Strategies weighted by performance * diversification_bonus. Uncorrelated strategies get higher allocation.",
        "top_3": list(sorted_weights.items())[:3],
        "most_correlated_pair": max(corr_dict.items(), key=lambda x: abs(x[1])) if corr_dict else None,
        "least_correlated_pair": min(corr_dict.items(), key=lambda x: abs(x[1])) if corr_dict else None,
    }
    
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2, default=str))
    
    print(f"Analyzed {len(strategies)} strategies")
    print(f"Top allocations:")
    for s, w in list(sorted_weights.items())[:5]:
        print(f"  {s}: {w:.1%}")
    if corr_dict:
        most = max(corr_dict.items(), key=lambda x: abs(x[1]))
        least = min(corr_dict.items(), key=lambda x: abs(x[1]))
        print(f"Most correlated: {most[0]} ({most[1]:.3f})")
        print(f"Least correlated: {least[0]} ({least[1]:.3f})")

if __name__ == "__main__":
    run()
