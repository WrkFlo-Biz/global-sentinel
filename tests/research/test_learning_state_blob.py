"""Tests for learning_state_persistence.py — save/restore/rollback/delta/hash verification."""
import json
import pytest
from pathlib import Path
from src.research.learning_state_persistence import LearningStatePersistence


@pytest.fixture
def lsp(tmp_path):
    return LearningStatePersistence(local_dir=tmp_path / "state_versions")


@pytest.fixture
def sample_state():
    return {"weights": [0.1, 0.2, 0.3], "version": "v1", "epoch": 42}


def test_save_creates_file(lsp, sample_state):
    version_id = lsp.save_state(sample_state)
    assert version_id
    versions = lsp.list_versions()
    assert version_id in versions


def test_save_envelope_has_schema_and_hash(lsp, sample_state):
    version_id = lsp.save_state(sample_state)
    local_file = lsp._local_dir / f"{version_id}.json"
    envelope = json.loads(local_file.read_text())
    assert envelope["schema_version"] == "learning_state_persistence.v1"
    assert envelope["content_hash"]
    assert envelope["manifest_hash"]
    assert envelope["learning_state"] == sample_state


def test_load_latest_returns_saved_state(lsp, sample_state):
    lsp.save_state(sample_state)
    loaded = lsp.load_latest_state()
    assert loaded == sample_state


def test_load_by_version(lsp, sample_state):
    version_id = lsp.save_state(sample_state)
    loaded = lsp.load_state_by_version(version_id)
    assert loaded == sample_state


def test_multiple_versions_ordered(lsp):
    v1 = lsp.save_state({"epoch": 1})
    v2 = lsp.save_state({"epoch": 2})
    versions = lsp.list_versions()
    assert len(versions) == 2
    assert v1 in versions
    assert v2 in versions


def test_rollback_to_previous(lsp):
    v1 = lsp.save_state({"epoch": 1, "weights": [0.5]})
    v2 = lsp.save_state({"epoch": 2, "weights": [0.9]})

    # Rollback to v1
    restored = lsp.rollback_to_version(v1)
    assert restored == {"epoch": 1, "weights": [0.5]}

    # Rollback re-saves v1 state — may produce same or new version_id
    # depending on whether timestamp+hash collide. Either way, v1 state
    # should be loadable.
    versions = lsp.list_versions()
    assert len(versions) >= 2  # at least v1 and v2
    reloaded = lsp.load_state_by_version(v1)
    assert reloaded == {"epoch": 1, "weights": [0.5]}


def test_rollback_metadata_recorded(lsp):
    v1 = lsp.save_state({"epoch": 1})
    lsp.save_state({"epoch": 2})
    lsp.rollback_to_version(v1)

    # Find the rollback version envelope and check metadata
    versions = lsp.list_versions()
    found_rollback = False
    for v in versions:
        fpath = lsp._local_dir / f"{v}.json"
        envelope = json.loads(fpath.read_text())
        if envelope.get("metadata", {}).get("rollback_from") == v1:
            found_rollback = True
            break
    assert found_rollback, "No version with rollback metadata found"
    envelope = json.loads(fpath.read_text())
    assert envelope["metadata"]["rollback_from"] == v1


def test_content_hash_changes_with_different_state(lsp):
    v1 = lsp.save_state({"epoch": 1})
    v2 = lsp.save_state({"epoch": 2})
    
    f1 = json.loads((lsp._local_dir / f"{v1}.json").read_text())
    f2 = json.loads((lsp._local_dir / f"{v2}.json").read_text())
    assert f1["content_hash"] != f2["content_hash"]


def test_content_hash_stable_for_same_state(lsp):
    state = {"weights": [1, 2, 3], "epoch": 10}
    v1 = lsp.save_state(state)
    v2 = lsp.save_state(state)
    
    f1 = json.loads((lsp._local_dir / f"{v1}.json").read_text())
    f2 = json.loads((lsp._local_dir / f"{v2}.json").read_text())
    assert f1["content_hash"] == f2["content_hash"]


def test_load_nonexistent_version_returns_none(lsp):
    result = lsp.load_state_by_version("nonexistent_version_id")
    assert result is None


def test_load_latest_empty_returns_none(lsp):
    result = lsp.load_latest_state()
    assert result is None


def test_list_versions_limit(lsp):
    for i in range(5):
        lsp.save_state({"epoch": i})
    versions = lsp.list_versions(limit=3)
    assert len(versions) == 3


def test_blob_client_not_available_by_default(lsp):
    assert lsp.available is False


def test_save_with_metadata(lsp, sample_state):
    version_id = lsp.save_state(sample_state, metadata={"experiment": "test_run"})
    envelope = json.loads((lsp._local_dir / f"{version_id}.json").read_text())
    assert envelope["metadata"]["experiment"] == "test_run"
