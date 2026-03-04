# CI Smoke Fixtures (V4.3 Time-Window-Aware)

Quick-running replay fixtures for PR validation. Each fixture includes time-window expectations.

## Fixtures

| # | Fixture | Window | Mode | Key Checks |
|---|---------|--------|------|------------|
| 1 | normal_orb_window_smoke_01 | ORB window | NORMAL | Strategy eligibility, multipliers |
| 2 | crisis_manual_veto_orb_smoke_02 | ORB window | CRISIS/MANUAL_REVIEW | Veto blocks all strategies |
| 3 | lunch_lull_fallback_conflict_smoke_03 | Lunch lull | ELEVATED | Fallback+quorum fail, watchlist-only |
| 4 | power_hour_continuation_smoke_04 | Power hour | ELEVATED | ORB strategies eligible in power hour |
| 5 | close_exhaustion_watch_smoke_05 | Close exhaustion | NORMAL | Watchlist-only, size_mult=0, shadow blocked |

## Running

```bash
python src/replay_runner.py --repo-root . --fixtures tests/replays/ci_smoke
```
