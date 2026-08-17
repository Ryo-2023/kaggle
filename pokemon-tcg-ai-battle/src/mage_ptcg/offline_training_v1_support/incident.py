"""Incident reporting utility.

Captures execution failures and outputs secure public reports without private leakages.
"""

from __future__ import annotations
import time
from typing import Any
from mage_ptcg.offline_training_v1_support.contracts import digest

def create_incident_report(
    incident_id: str,
    operation: str,
    exception: Exception,
    artifact_type: str = "dataset",
    last_safe_checkpoint: str = None,
    reproduction_command: str = ""
) -> dict[str, Any]:
    """Compile a secure, public-safe incident report."""
    raw_msg = str(exception)
    safe_msg = raw_msg
    for path_pat in ("/home/", "/mnt/", "/Users/", "C:\\"):
        if path_pat in safe_msg:
            safe_msg = safe_msg.replace(path_pat, "[REDACTED_PATH]/")

    safe_msg = safe_msg[:512]

    report = {
        "incident_id": incident_id,
        "operation": operation,
        "time_utc": time.time(),
        "artifact_type": artifact_type,
        "failure_category": "UNEXPECTED_EXCEPTION",
        "exception_type": type(exception).__name__,
        "safe_message": safe_msg,
        "last_safe_checkpoint": last_safe_checkpoint,
        "recovery_status": "PENDING_MANUAL_REVIEW",
        "reproduction_command": reproduction_command,
        "recommended_action": "Verify input path portability and check schemas."
    }

    report["report_digest"] = digest(report, domain="incident-report:v1")
    return report
