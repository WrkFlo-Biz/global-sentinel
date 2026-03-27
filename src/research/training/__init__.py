"""Training datasets and offline research trainers for Global Sentinel."""

from .chokepoint_scenarios import (  # noqa: F401
    CHOKEPOINTS,
    CHOKEPOINT_CRISIS_EVENTS,
    COMBINED_SCENARIOS,
    EXECUTION_BOUNDARY,
    build_chokepoint_analog_library,
    compute_chokepoint_risk_score,
    get_chokepoint_telegram_summary,
    merge_chokepoint_analog_library,
)
from .crisis_training_dataset import (  # noqa: F401
    CRISIS_EVENTS,
    CRISIS_PLAYBOOKS,
    build_analog_library,
    dataset_summary,
    event_to_analog_entry,
    event_to_feature_vector,
)
