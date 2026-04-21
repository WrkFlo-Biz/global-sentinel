#!/usr/bin/env python3
"""Point-in-time joins for features to events.

CRITICAL: No future data leakage. Feature values are as-of the event timestamp.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


class PointInTimeJoiner:
    """Joins features to events using point-in-time semantics."""

    def join(
        self,
        events: List[Dict[str, Any]],
        feature_rows: List[Dict[str, Any]],
        as_of_field: str = "timestamp_utc",
        feature_time_field: str = "timestamp_utc",
        join_key: str = "symbol",
    ) -> List[Dict[str, Any]]:
        """For each event, find most recent feature values BEFORE event timestamp.

        Returns enriched events with feature values and lineage metadata.
        No future data leakage: only features with timestamp < event timestamp.
        """
        # Sort features by timestamp
        sorted_features = sorted(
            feature_rows,
            key=lambda x: x.get(feature_time_field, ""),
        )

        enriched = []
        for event in events:
            event_time = event.get(as_of_field, "")
            event_key = event.get(join_key, "")

            # Find most recent feature row for this key before event_time
            matched_feature = None
            for feat in sorted_features:
                feat_time = feat.get(feature_time_field, "")
                feat_key = feat.get(join_key, "")

                if feat_key != event_key:
                    continue
                if feat_time >= event_time:
                    break  # Future data — stop
                matched_feature = feat

            enriched_event = dict(event)
            if matched_feature:
                # Merge feature values (prefixed to avoid collision)
                for k, v in matched_feature.items():
                    if k not in (join_key, feature_time_field):
                        enriched_event[f"feat_{k}"] = v
                enriched_event["_pit_join"] = {
                    "matched": True,
                    "feature_timestamp": matched_feature.get(feature_time_field),
                    "event_timestamp": event_time,
                    "join_key": event_key,
                }
            else:
                enriched_event["_pit_join"] = {
                    "matched": False,
                    "event_timestamp": event_time,
                    "join_key": event_key,
                }

            enriched.append(enriched_event)

        return enriched
