"""Local job queue manager using a manifest-based dependency DAG.

Supports cycles detection, status transitions, and dry-run modes without running a background daemon.
"""

from __future__ import annotations
from typing import Any

class JobQueue:
    """DAG-based local job queue."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}

    def add_job(
        self,
        job_id: str,
        dependencies: list[str] = None,
        retries: int = 3
    ) -> None:
        """Add a job to the queue with metadata."""
        self.jobs[job_id] = {
            "job_id": job_id,
            "dependencies": dependencies or [],
            "status": "PENDING",
            "retries_remaining": retries,
            "max_retries": retries
        }

    def detect_cycles(self) -> bool:
        """Detect cycles in the dependency graph using DFS."""
        visited: dict[str, int] = {}

        def dfs(node: str) -> bool:
            visited[node] = 1
            for dep in self.jobs.get(node, {}).get("dependencies", []):
                if dep not in self.jobs:
                    continue
                if visited.get(dep, 0) == 1:
                    return True
                if visited.get(dep, 0) == 0:
                    if dfs(dep):
                        return True
            visited[node] = 2
            return False

        for job_id in self.jobs:
            if visited.get(job_id, 0) == 0:
                if dfs(job_id):
                    return True
        return False

    def update_job_status(self, job_id: str, status: str) -> None:
        """Atomically transition job status."""
        valid_statuses = {
            "PENDING", "READY", "RUNNING", "COMPLETE",
            "FAILED_RETRYABLE", "FAILED_FINAL", "BLOCKED", "CANCELLED"
        }
        if status not in valid_statuses:
            raise ValueError(f"Invalid job status: {status}")

        if job_id not in self.jobs:
            raise KeyError(f"Job {job_id} not found")

        self.jobs[job_id]["status"] = status

    def get_runnable_jobs(self) -> list[str]:
        """Find PENDING/READY jobs that have all dependencies COMPLETE."""
        runnable = []
        if self.detect_cycles():
            raise ValueError("Dependency cycle detected, execution blocked")

        for job_id, j in self.jobs.items():
            if j["status"] not in ("PENDING", "READY", "FAILED_RETRYABLE"):
                continue

            deps_ok = True
            for dep in j["dependencies"]:
                dep_job = self.jobs.get(dep)
                if not dep_job or dep_job["status"] != "COMPLETE":
                    deps_ok = False
                    break

            if deps_ok:
                runnable.append(job_id)

        return runnable
