# Oil-Shock Regime Design

## Overview
The oil-shock regime module detects and responds to oil price dislocations caused by geopolitical events (Strait of Hormuz closure, OPEC supply shocks, sanctions escalation). It overlays on top of the existing crisis regime system, promoting oil-correlated strategies and adjusting risk limits dynamically.

## Thresholds

| Regime | WTI Price | Trigger | Duration Requirement |
|--------|-----------|---------|---------------------|
| NORMAL | < $95 | Default | Immediate |
| ELEVATED | $95-$100 | Price OR 5% 24h change OR chokepoint active | Any single trigger |
| SHOCK | $100-$105 | Price OR Hormuz closed | Any single trigger |
| DISLOCATION | > $105 | Price sustained 2+ cycles | Must persist across 2 consecutive cycles |

### Trigger Details

#### Price-Based Triggers
- WTI crude oil front-month contract price (CL1)
- Source: Alpaca market data (primary), FMP API (fallback)
- Checked every cycle (~60s)
- Hysteresis: regime does not downgrade until price is 2% below threshold for 3+ cycles
  - Example: SHOCK triggers at $100, does not revert to ELEVATED until price < $98 for 3 cycles
  - Prevents oscillation at boundary prices

#### Velocity Trigger (ELEVATED)
- 5% price change in trailing 24 hours
- Calculated as: `abs(current_price - price_24h_ago) / price_24h_ago`
- Can trigger ELEVATED even if absolute price is below $95
- Captures rapid moves that precede sustained shocks

#### Chokepoint Trigger (ELEVATED/SHOCK)
- Chokepoint composite score from DeescalationDetector
- Composite > 0.30 triggers ELEVATED
- Hormuz-specific score > 0.60 triggers SHOCK
- Current composite: 0.07 (low — see Known Issues)

#### Sustained Check (DISLOCATION)
- Price must remain above $105 for 2 consecutive cycles
- Prevents flash-spike false positives
- Once triggered, DISLOCATION persists until price < $103 for 5 cycles

## Strategy Promotion Matrix

When the oil regime changes, strategy weights are adjusted to capitalize on the regime's characteristics.

### NORMAL Regime (default weights)
| Strategy | Weight | Notes |
|----------|--------|-------|
| oil_energy_long | 1.0x | Standard weight |
| oil_energy_short | 1.0x | Standard weight |
| defense_sector | 1.0x | Standard weight |
| momentum_breakout | 1.0x | Standard weight |
| All others | 1.0x | No adjustment |

### ELEVATED Regime
| Strategy | Weight | Notes |
|----------|--------|-------|
| oil_energy_long | 2.0x | Double weight — energy longs benefit |
| oil_energy_short | 0.5x | Halve — shorting energy risky in rising oil |
| defense_sector | 1.5x | Increase — defense correlates with geopolitical tension |
| shipping_logistics | 1.5x | Increase — shipping rates rise with chokepoint risk |
| airline_transport | 0.5x | Halve — airlines hurt by oil costs |
| consumer_discretionary | 0.7x | Reduce — consumer spending compressed |
| momentum_breakout | 1.2x | Slight increase — volatility creates breakouts |
| mean_reversion | 0.8x | Reduce — trending markets hurt mean-reversion |

### SHOCK Regime
| Strategy | Weight | Notes |
|----------|--------|-------|
| oil_energy_long | 3.0x | Triple weight — max energy exposure |
| oil_energy_short | 0.0x | Disabled — do not short energy in shock |
| defense_sector | 2.0x | Double — defense names surge |
| shipping_logistics | 2.0x | Double — shipping bottlenecks |
| airline_transport | 0.0x | Disabled — airlines collapse in oil shock |
| consumer_discretionary | 0.3x | Minimal — consumer crushed |
| utilities | 1.5x | Increase — defensive sector, energy pass-through |
| gold_safe_haven | 2.0x | Double — flight to safety |
| momentum_breakout | 1.5x | Increase — strong trends form |
| mean_reversion | 0.5x | Minimal — trends dominate |

### DISLOCATION Regime
| Strategy | Weight | Notes |
|----------|--------|-------|
| oil_energy_long | 2.0x | Reduce from SHOCK — profit-taking risk |
| oil_energy_short | 0.5x | Re-enable cautiously — mean-reversion possible |
| defense_sector | 2.5x | Highest weight — sustained conflict |
| shipping_logistics | 2.5x | Highest weight — sustained disruption |
| airline_transport | 0.0x | Still disabled |
| consumer_discretionary | 0.2x | Minimal |
| utilities | 2.0x | Strong defensive position |
| gold_safe_haven | 2.5x | Max safe haven |
| treasury_hedge | 2.0x | New — flight to treasuries |
| volatility_long | 1.5x | New — VIX likely elevated |
| momentum_breakout | 1.0x | Normalize — trends may exhaust |
| mean_reversion | 1.0x | Normalize — dislocation creates reversion opportunities |

## Risk Overlay

Risk limits are dynamically adjusted per regime to prevent excessive exposure to oil-correlated assets while allowing the system to capitalize on the regime.

### Exposure Caps by Regime

| Parameter | NORMAL | ELEVATED | SHOCK | DISLOCATION |
|-----------|--------|----------|-------|-------------|
| Max oil-correlated exposure | 100% | 80% | 70% | 60% |
| Max single oil position | 8% | 6% | 5% | 4% |
| Max energy sector | 50% | 40% | 35% | 30% |
| Max position size multiplier | 1.0x | 1.2x | 1.5x | 1.0x |
| Daily loss halt | $3,000 | $3,000 | $5,000 | $5,000 |
| Correlation group cap | 40% | 35% | 30% | 25% |

### Oil-Correlated Asset Classification

Assets are tagged with an oil correlation coefficient (updated weekly):

| Category | Correlation Range | Examples |
|----------|------------------|----------|
| Direct oil | 0.8 - 1.0 | USO, XLE, OXY, CVX, XOM |
| High correlation | 0.5 - 0.8 | HAL, SLB, airlines (inverse), shipping |
| Moderate correlation | 0.3 - 0.5 | Industrials, materials, some utilities |
| Low correlation | 0.0 - 0.3 | Tech, healthcare, financials |
| Inverse correlation | -0.3 - 0.0 | Renewables, EV manufacturers |

The oil-correlated exposure cap applies to assets with correlation >= 0.5.

### Kill Switch Override
- All regimes: kill switch (`control/kill_switch.json`) overrides everything
- If kill switch active: cancel all non-close orders regardless of regime
- Regime state is preserved across kill switch activation for resumption

## Integration Points

### crisis_monitor.py
```python
# In main cycle loop, after data fetch:
try:
    oil_regime = oil_shock_regime.evaluate(
        wti_price=market_data.get_price("CL1"),
        wti_24h_change=market_data.get_change_24h("CL1"),
        chokepoint_composite=deescalation_detector.chokepoint_score,
        hormuz_score=deescalation_detector.hormuz_score
    )
    scorecard["v6_oil_regime"] = oil_regime.to_dict()
except Exception as e:
    logger.warning(f"Oil regime evaluation failed: {e}")
    oil_regime = OilRegime.NORMAL  # safe default
```

### strategy_engine.py
```python
# Apply promotion matrix to strategy weights:
weights = oil_regime.apply_promotions(base_weights)
```

### exposure_book.py
```python
# Apply regime-specific exposure caps:
caps = oil_regime.get_risk_caps()
exposure_check = exposure_book.check_against_caps(caps)
```

### Telegram Digest
- Oil regime included in every digest message
- Format: `Oil Regime: ELEVATED (WTI $97.50, +3.2% 24h)`
- Regime changes trigger immediate alert (not batched)

### Dashboard (Streamlit)
- Oil regime displayed in warroom sidebar
- Color-coded: NORMAL=green, ELEVATED=yellow, SHOCK=orange, DISLOCATION=red
- Historical regime chart showing transitions over time

## Configuration

Located in `config/oil_shock_regime.yaml`:
```yaml
oil_shock_regime:
  enabled: true
  thresholds:
    elevated: 95.0
    shock: 100.0
    dislocation: 105.0
  velocity_trigger: 0.05  # 5% 24h change
  chokepoint_elevated: 0.30
  chokepoint_shock: 0.60
  hysteresis_pct: 0.02  # 2% below threshold to revert
  hysteresis_cycles: 3   # cycles below threshold to revert
  dislocation_sustain_cycles: 2  # cycles above $105 to trigger
  dislocation_revert_cycles: 5   # cycles below $103 to revert
  data_sources:
    primary: alpaca
    fallback: fmp
  fallback_regime: NORMAL  # if oil price unavailable
```

## Graceful Degradation

| Failure | Behavior |
|---------|----------|
| Oil price unavailable (both sources) | Maintain current regime for 5 cycles, then revert to NORMAL |
| Chokepoint score unavailable | Ignore chokepoint triggers, use price-only |
| Strategy promotion fails | Log warning, use base weights (1.0x for all) |
| Exposure cap check fails | Log warning, use NORMAL caps (most permissive) |
| Configuration file missing | Use hardcoded defaults matching NORMAL regime |

## Testing

### Unit Tests
- `tests/test_oil_shock_regime.py`
  - Test each threshold boundary (94.99, 95.00, 95.01)
  - Test hysteresis (price drops below threshold, verify delay)
  - Test velocity trigger independent of price
  - Test chokepoint trigger independent of price
  - Test DISLOCATION sustained-cycle requirement
  - Test promotion matrix multiplication
  - Test risk cap application

### Integration Tests
- Verify regime appears in scorecard (`v6_oil_regime` key)
- Verify regime appears in Telegram digest
- Verify strategy weights change when regime changes
- Verify exposure caps enforce correctly
- Verify kill switch overrides regime

### Manual Validation
```bash
# Force regime for testing:
python3 -c "
from src.regime.oil_shock_regime import OilShockRegime
regime = OilShockRegime()
result = regime.evaluate(wti_price=102.0, wti_24h_change=0.06, chokepoint_composite=0.35, hormuz_score=0.2)
print(f'Regime: {result.regime}')
print(f'Triggers: {result.triggers}')
print(f'Promotions: {result.promotions}')
print(f'Risk caps: {result.risk_caps}')
"
```
