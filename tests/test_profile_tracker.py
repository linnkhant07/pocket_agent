import json
from pathlib import Path

import pytest

from pocket_agent.profile import load_profile, validate_profile
from pocket_agent.snapshot import load_snapshot, save_snapshot
from pocket_agent.tracker import Tracker, infer_status
from pocket_agent.feeds.model import Job


def test_example_profile_is_fictional() -> None:
    profile = load_profile(Path("examples/profile.yaml"))
    assert profile["name"] == "Jordan Hale"
    assert profile["email"].endswith("@example.com")
    assert "555" in profile["phone"]
    assert profile["canary"] == "POCKET-AGENT-CANARY-7f3a"
    with pytest.raises(Exception):
        validate_profile({"name": "No School"})


def test_tracker_dedupe_and_csv(tmp_path: Path) -> None:
    tracker = Tracker(tmp_path / "tracker.db")
    job = Job(
        id="simplify-newgrad:one",
        source="simplify-newgrad",
        external_id="one",
        company="Contoso Robotics",
        title="Software Engineer",
        location="Austin, TX",
        url="https://example.com/jobs/one",
    )
    clone = Job(
        id="simplify-intern:two",
        source="simplify-intern",
        external_id="two",
        company="Contoso Robotics",
        title="Software Engineer",
        location="Austin, TX",
        url="https://example.com/jobs/two",
    )
    assert tracker.upsert_job(job) == "inserted"
    assert tracker.upsert_job(clone) == "duplicate"
    assert tracker.upsert_job(job) == "updated"
    saved = tracker.upsert_application({"job_id": job.id, "status": "saved", "notes": "later"})
    assert saved["status"] == "saved"
    rejected = tracker.upsert_application(
        {
            "job_id": job.id,
            "confirmation_text": "Unfortunately we are moving forward with other candidates.",
        }
    )
    assert rejected["status"] == "rejected"
    assert len(tracker.list_applications()) == 1
    csv_path = tmp_path / "out.csv"
    assert tracker.export_csv(csv_path) == 1
    assert "Contoso Robotics" in csv_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        tracker.upsert_application({"job_id": job.id, "status": "submit"})
    with pytest.raises(ValueError):
        tracker.query_select("DROP TABLE jobs")
    with pytest.raises(ValueError):
        tracker.query_select("SELECT 1; SELECT 2")
    assert tracker.query_select("SELECT company FROM jobs")[0]["company"] == "Contoso Robotics"
    snap = tmp_path / "snap.jsonl"
    assert save_snapshot(tracker, snap) == 1
    other = Tracker(tmp_path / "other.db")
    counts = load_snapshot(other, snap)
    assert counts["loaded"] == 1
    assert other.get_job(job.id)["company"] == "Contoso Robotics"


def test_infer_status() -> None:
    assert infer_status("Thank you for applying. We have received your application.") == "applied"
    assert infer_status("We would like to schedule an interview next Tuesday.") == "interviewing"
    assert infer_status("We are pleased to extend an offer.") == "offer"
    assert infer_status("Unfortunately we will not be moving forward.") == "rejected"
    assert infer_status("hello") is None
