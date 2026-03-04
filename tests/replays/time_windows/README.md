# Time Windows Fixture Pack (V4.3)

Dedicated fixtures validating TimeWindowPolicyEngine integration across all key market windows.

## Fixtures

| # | Fixture | Window | Key Checks |
|---|---------|--------|------------|
| 1 | opening_whipsaw_after_release_01 | ORB window | Whipsaw+slippage guardrails, shadow blocked, reduced multipliers |
| 2 | lunch_fakeout_watchlist_only_01 | Lunch lull | Watchlist-only, no strategies eligible |
| 3 | late_morning_mean_reversion_eligibility_01 | Late morning MR | short_mean_reversion eligible, ORB blocked |
| 4 | power_hour_continuation_01 | Power hour | ELEVATED mode, ORB strategies eligible |
| 5 | close_exhaustion_watch_01 | Close exhaustion | Watchlist-only, size=0, shadow blocked |

## Running

```bash
python src/replay_runner.py --repo-root . --fixtures tests/replays/time_windows
```
