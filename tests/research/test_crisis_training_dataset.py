"""Tests for the crisis training dataset and runner helpers."""

from __future__ import annotations

from src.research.training.crisis_training_dataset import (
    CRISIS_EVENTS,
    CRISIS_PLAYBOOKS,
    REQUIRED_EVENT_FIELDS,
    build_analog_library,
    dataset_summary,
    event_to_feature_vector,
)


def test_all_events_have_required_fields():
    for event in CRISIS_EVENTS:
        missing = REQUIRED_EVENT_FIELDS.difference(event.keys())
        assert not missing, f"{event.get('id')} missing {sorted(missing)}"


def test_all_categories_have_playbooks():
    categories = {event["category"] for event in CRISIS_EVENTS}
    assert categories.issubset(CRISIS_PLAYBOOKS.keys())


def test_feature_vectors_are_fixed_length_and_numeric():
    vectors = [event_to_feature_vector(event) for event in CRISIS_EVENTS]
    assert vectors
    for vector in vectors:
        assert len(vector) == 16
        assert all(isinstance(value, float) for value in vector)


def test_no_duplicate_event_ids_and_dataset_exceeds_forty_events():
    ids = [event["id"] for event in CRISIS_EVENTS]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 40


def test_summary_and_analog_library_are_consistent():
    summary = dataset_summary()
    analogs = build_analog_library()

    assert summary["event_count"] == len(CRISIS_EVENTS)
    assert len(analogs) == len(CRISIS_EVENTS)
    assert summary["playbook_count"] == len(CRISIS_PLAYBOOKS)
