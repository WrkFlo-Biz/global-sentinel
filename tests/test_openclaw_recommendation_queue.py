import json
from pathlib import Path

from src.reports.openclaw_recommendation_queue import OpenClawRecommendationQueueWriter


def test_append_role_advisory_writes_expected_queue_entry(tmp_path: Path):
    repo_root = tmp_path
    artifact_path = repo_root / "reports" / "openclaw_research" / "cio_brief.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("{}", encoding="utf-8")

    artifact = {
        "role_id": "cio",
        "title": "Chief Investment Officer",
        "safety": {"paper_only": True},
        "inputs": {"scorecard": "/tmp/scorecard.json"},
        "status": "yellow",
        "observed_facts": ["Mode: ELEVATED"],
        "inferences": ["Canary still in stabilization."],
        "actions": ["Keep promotion authority with policy gates only."],
        "metrics": {"confidence": 0.61},
    }

    writer = OpenClawRecommendationQueueWriter(repo_root)
    entry = writer.append_role_advisory(role_artifact=artifact, artifact_path=artifact_path)

    queue_path = repo_root / "logs" / "self_improvement" / "recommendation_queue.jsonl"
    rows = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert entry["category"] == "role_advisory"
    assert entry["constraints"]["manual_approval_required"] is True
    assert rows[0]["role_id"] == "cio"
    assert rows[0]["references"]["artifact_json"] == str(artifact_path)
    assert rows[0]["payload"]["actions"][0] == "Keep promotion authority with policy gates only."
