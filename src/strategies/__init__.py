"""Instagram-derived trading strategies integrated with Global Sentinel."""

from .commodity_regime_rotation_strategy import (
    COMMODITY_SECTOR_WATCHLIST,
    CommodityRegimeRotationStrategy,
    evaluate_commodity_regime_rotation,
)
from .kronos_forecast_overlay_strategy import (
    KRONOS_WATCHLIST,
    KronosForecastOverlayStrategy,
    evaluate_kronos_forecast_overlay,
)
from .mgc_ai_optimized_strategy import (
    MGC_WATCHLIST,
    MGCAIOptimizedStrategy,
    evaluate_mgc_ai_optimized,
)
from .hormuz_osint_geopolitical_strategy import (
    ALL_WATCHED,
    HormuzOsintGeopoliticalStrategy,
    evaluate_hormuz_osint_geopolitical,
)
from .options_flow_model_strategy import (
    OPTIONS_WATCHLIST,
    OptionsFlowModelStrategy,
    evaluate_options_flow_model,
)
from .parrondo_paradox_strategy import (
    MEAN_REVERSION_SYMBOLS,
    MOMENTUM_SYMBOLS,
    ParrondoParadoxStrategy,
    evaluate_parrondo_paradox,
)
from .ict_candle_range_theory_strategy import (
    CRT_WATCHLIST,
    ICTCandleRangeTheoryStrategy,
    evaluate_ict_candle_range_theory,
)
from .quant_probability_pricing_strategy import (
    VOL_SURFACE_WATCHLIST,
    QuantProbabilityPricingStrategy,
    evaluate_quant_probability_pricing,
)

__all__ = [
    "COMMODITY_SECTOR_WATCHLIST",
    "CommodityRegimeRotationStrategy",
    "evaluate_commodity_regime_rotation",
    "KRONOS_WATCHLIST",
    "KronosForecastOverlayStrategy",
    "evaluate_kronos_forecast_overlay",
    "MGC_WATCHLIST",
    "MGCAIOptimizedStrategy",
    "evaluate_mgc_ai_optimized",
    "ALL_WATCHED",
    "HormuzOsintGeopoliticalStrategy",
    "evaluate_hormuz_osint_geopolitical",
    "OPTIONS_WATCHLIST",
    "OptionsFlowModelStrategy",
    "evaluate_options_flow_model",
    "MEAN_REVERSION_SYMBOLS",
    "MOMENTUM_SYMBOLS",
    "ParrondoParadoxStrategy",
    "evaluate_parrondo_paradox",
    "CRT_WATCHLIST",
    "ICTCandleRangeTheoryStrategy",
    "evaluate_ict_candle_range_theory",
    "VOL_SURFACE_WATCHLIST",
    "QuantProbabilityPricingStrategy",
    "evaluate_quant_probability_pricing",
]
