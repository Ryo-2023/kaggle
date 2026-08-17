"""Tests for job queue, resource budget, and incident reporting."""

from __future__ import annotations
import pytest
from mage_ptcg.offline_training_v1_support.job_queue import JobQueue
from mage_ptcg.offline_training_v1_support.resource_budget import ResourceBudgetTracker
from mage_ptcg.offline_training_v1_support.incident import create_incident_report

def test_job_queue_dag_execution():
    jq = JobQueue()
    jq.add_job("job-1")
    jq.add_job("job-2", dependencies=["job-1"])
    jq.add_job("job-3", dependencies=["job-2"])

    # Cyclic check
    assert jq.detect_cycles() is False

    # Check runnable
    runnable = jq.get_runnable_jobs()
    assert runnable == ["job-1"] # only job-1 has no dependencies

    # complete job-1
    jq.update_job_status("job-1", "COMPLETE")
    runnable2 = jq.get_runnable_jobs()
    assert runnable2 == ["job-2"]

    # Introduce cycle
    jq.add_job("job-1", dependencies=["job-3"])
    assert jq.detect_cycles() is True
    with pytest.raises(ValueError, match="Dependency cycle detected"):
        jq.get_runnable_jobs()

def test_resource_budget_degradation():
    tracker = ResourceBudgetTracker({"wall_time": 100.0, "teacher_queries": 10})

    # Under limit
    tracker.consume("wall_time", 50.0)
    assert tracker.check_limit("wall_time") == "OK"
    params = tracker.get_degraded_parameters()
    assert params["skip_extended_fuzz"] is False
    assert params["bootstrap_samples"] == 1000

    # Soft limit (80%+)
    tracker.consume("wall_time", 35.0)  # total 85.0
    assert tracker.check_limit("wall_time") == "SOFT_LIMIT"
    params_soft = tracker.get_degraded_parameters()
    assert params_soft["bootstrap_samples"] == 200

    # Hard limit (100%+)
    tracker.consume("wall_time", 20.0)  # total 105.0
    assert tracker.check_limit("wall_time") == "HARD_LIMIT"
    params_hard = tracker.get_degraded_parameters()
    assert params_hard["skip_extended_fuzz"] is True
    assert params_hard["bootstrap_samples"] == 50

def test_incident_redaction_and_digest():
    exc = ValueError("Could not access file in /home/user/workspace/secrets.txt")
    report = create_incident_report(
        incident_id="inc-99",
        operation="fuzz_check",
        exception=exc,
        artifact_type="model"
    )

    assert report["incident_id"] == "inc-99"
    # Ensure home path is redacted
    assert "/home/" not in report["safe_message"]
    assert "[REDACTED_PATH]/" in report["safe_message"]

    # Digest check
    assert "report_digest" in report
    assert len(report["report_digest"]) == 64
