#!/usr/bin/env python3
"""RL Position Sizer — PPO-based optimal position sizing for trades."""
import json, os, datetime, numpy as np
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
MODEL_PATH = REPO_ROOT / "data/quantum_feed/rl_sizer_model.zip"
OUTPUT_PATH = REPO_ROOT / "data/quantum_feed/rl_sizing_recommendations.json"

try:
    import gymnasium as gym
    from gymnasium import spaces
    from stable_baselines3 import PPO
    HAS_RL = True
except ImportError:
    HAS_RL = False

class TradeSizingEnv(gym.Env):
    """Custom Gym environment for position sizing."""
    metadata = {"render_modes": []}

    def __init__(self, episodes=None):
        super().__init__()
        # State: regime_score, vix, momentum, signal_strength, exposure, cash_ratio, hour
        self.observation_space = spaces.Box(low=-5, high=5, shape=(7,), dtype=np.float32)
        # Action: position size multiplier 0.0 to 1.0
        self.action_space = spaces.Box(low=0, high=1, shape=(1,), dtype=np.float32)
        self.episodes = episodes or self._load_episodes()
        self.current_episode = 0
        self.current_step = 0
        self.portfolio_value = 1.0

    def _load_episodes(self):
        """Load paper trade history as training episodes."""
        episodes = []
        reports_dir = REPO_ROOT / "reports/paper_trades"
        if reports_dir.exists():
            for f in sorted(reports_dir.glob("day_trade_*.json")):
                try:
                    data = json.loads(f.read_text())
                    positions = data.get("positions", [])
                    if positions:
                        episodes.append(positions)
                except:
                    continue
        if not episodes:
            # Generate synthetic episodes for initial training
            for _ in range(50):
                ep = []
                for _ in range(3):
                    pnl = np.random.normal(0, 0.15)
                    ep.append({
                        "pnl_pct": pnl * 100,
                        "entry_price": 1.0 + np.random.uniform(-0.1, 0.1),
                        "regime_score": np.random.uniform(0.2, 0.8),
                        "vix": np.random.uniform(15, 40),
                    })
                episodes.append(ep)
        return episodes

    def _get_obs(self):
        if self.current_episode >= len(self.episodes):
            return np.zeros(7, dtype=np.float32)
        ep = self.episodes[self.current_episode]
        if self.current_step >= len(ep):
            return np.zeros(7, dtype=np.float32)
        trade = ep[self.current_step]
        regime = trade.get("regime_score", np.random.uniform(0.3, 0.7))
        vix = trade.get("vix", np.random.uniform(18, 30)) / 40.0
        momentum = trade.get("pct_move", np.random.uniform(-3, 3)) / 5.0
        signal = trade.get("confidence", np.random.uniform(0.3, 0.8))
        exposure = min(1.0, self.current_step * 0.3)
        cash = max(0, 1.0 - exposure)
        hour = np.random.uniform(0.3, 0.9)
        return np.array([regime, vix, momentum, signal, exposure, cash, hour], dtype=np.float32)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_episode = (self.current_episode + 1) % max(1, len(self.episodes))
        self.current_step = 0
        self.portfolio_value = 1.0
        return self._get_obs(), {}

    def step(self, action):
        size = float(action[0])
        ep = self.episodes[self.current_episode % len(self.episodes)]
        if self.current_step >= len(ep):
            return self._get_obs(), 0.0, True, False, {}

        trade = ep[self.current_step]
        pnl_pct = trade.get("pnl_pct", trade.get("realized_pnl", 0)) / 100.0
        trade_return = pnl_pct * size
        self.portfolio_value *= (1 + trade_return)

        # Reward: P&L minus drawdown penalty
        reward = trade_return * 10  # Scale up
        if trade_return < -0.05:
            reward -= abs(trade_return) * 5  # Extra penalty for big losses
        if size < 0.1 and pnl_pct > 0.05:
            reward -= 0.5  # Penalty for being too small on winners

        self.current_step += 1
        done = self.current_step >= len(ep)
        return self._get_obs(), float(reward), done, False, {}

def train(timesteps=50000):
    """Train the RL position sizer."""
    if not HAS_RL:
        print("stable-baselines3 not installed")
        return None
    env = TradeSizingEnv()
    model = PPO("MlpPolicy", env, verbose=0, learning_rate=3e-4, n_steps=256, batch_size=64)
    model.learn(total_timesteps=timesteps)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(MODEL_PATH))
    print(f"Model saved to {MODEL_PATH}")
    return model

def predict(candidates):
    """Get sizing recommendations for trade candidates."""
    if not HAS_RL or not MODEL_PATH.exists():
        return [{"symbol": c.get("symbol", "?"), "size_multiplier": 0.5, "confidence": 0.5} for c in candidates]
    model = PPO.load(str(MODEL_PATH))
    results = []
    for c in candidates:
        obs = np.array([
            c.get("regime_score", 0.5),
            c.get("vix", 25) / 40.0,
            c.get("momentum", 0) / 5.0,
            c.get("confidence", 0.5),
            c.get("exposure", 0.3),
            c.get("cash_ratio", 0.7),
            c.get("hour", 0.5),
        ], dtype=np.float32)
        action, _ = model.predict(obs, deterministic=True)
        results.append({
            "symbol": c.get("symbol", "?"),
            "size_multiplier": round(float(action[0]), 3),
            "confidence": round(float(action[0]) * c.get("confidence", 0.5), 3),
        })
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps({
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "recommendations": results,
    }, indent=2))
    return results

if __name__ == "__main__":
    print("Training RL position sizer...")
    model = train(timesteps=20000)
    if model:
        test_candidates = [
            {"symbol": "SPY", "regime_score": 0.45, "vix": 26, "momentum": -1.2, "confidence": 0.6},
            {"symbol": "TSLA", "regime_score": 0.45, "vix": 26, "momentum": 2.8, "confidence": 0.7},
        ]
        recs = predict(test_candidates)
        print("Recommendations:", json.dumps(recs, indent=2))
