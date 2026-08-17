#!/usr/bin/env python3
"""Migrate the known V4 resume-digest ordering bug without changing weights.

This is a one-time, fail-closed artifact migration.  It accepts only the
legacy layout where both seed resume files carry the same old objective digest,
both user configs carry the report's materialized sequence digest, and no
other resume identity is changed.  Model and Adam tensors are copied exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any

import torch

from mage_ptcg.meta_specialist.recurrent_bc_v4 import trainer_implementation_sha256_v4


SCHEMA = "meta-specialist-recurrent-bc-v4-epoch-resume-v1"
REPORT_SCHEMA = "meta-specialist-recurrent-bc-v4-research-report"


def _hex64(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _atomic_torch_save(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    os.close(descriptor)
    try:
        torch.save(payload, temporary)
        with open(temporary, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _atomic_text_save(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        Path(temporary).unlink(missing_ok=True)
        raise


def migrate_legacy_resume_lineage_v4(root: str | Path, *, report_path: str | Path | None = None) -> dict[str, object]:
    root_path = Path(root).resolve()
    report_file = Path(report_path).resolve() if report_path is not None else root_path / "archaludon-training.json"
    report = json.loads(report_file.read_text(encoding="utf-8"))
    if report.get("schema") != REPORT_SCHEMA:
        raise ValueError("unexpected training report schema")
    new_sha = _hex64(report.get("selected_sequence_sha256"), "report selected_sequence_sha256")
    old_report_trainer_sha = _hex64(
        report.get("trainer_implementation_sha256"), "report trainer_implementation_sha256",
    )
    live_trainer_sha = trainer_implementation_sha256_v4()
    results = report.get("seed_results")
    if not isinstance(results, dict) or set(results) != {"0", "1"}:
        raise ValueError("migration requires exactly seed 0 and seed 1")
    payloads: dict[str, dict[str, Any]] = {}
    old_shas: set[str] = set()
    for seed in ("0", "1"):
        path = Path(str(results[seed].get("last_checkpoint_path", ""))).resolve()
        if root_path not in path.parents:
            raise ValueError(f"seed {seed} checkpoint escapes migration root")
        payload = torch.load(path, map_location="cpu", weights_only=False)
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise ValueError(f"seed {seed} resume schema is not migratable")
        run_config = payload.get("run_config")
        user = run_config.get("user") if isinstance(run_config, dict) else None
        old_sha = _hex64(run_config.get("selected_objective_sha256") if isinstance(run_config, dict) else None,
                         f"seed {seed} legacy objective")
        if not isinstance(user, dict) or user.get("selected_sequence_sha256") != new_sha:
            raise ValueError(f"seed {seed} user sequence identity differs from report")
        if (
            run_config.get("trainer_implementation_sha256") != old_report_trainer_sha
            or user.get("trainer_implementation_sha256") != old_report_trainer_sha
        ):
            raise ValueError(f"seed {seed} trainer lineage differs from report")
        old_shas.add(old_sha)
        payloads[seed] = payload
    if len(old_shas) != 1:
        raise ValueError("seed resume files do not share one legacy objective digest")
    old_sha = next(iter(old_shas))
    if old_sha == new_sha:
        raise ValueError("resume lineage is already on the canonical digest")
    for seed in ("0", "1"):
        run_config = dict(payloads[seed]["run_config"])
        run_config["selected_objective_sha256"] = new_sha
        run_config["trainer_implementation_sha256"] = live_trainer_sha
        user = dict(run_config["user"])
        user["trainer_implementation_sha256"] = live_trainer_sha
        run_config["user"] = user
        payloads[seed]["run_config"] = run_config
    for seed in ("0", "1"):
        path = Path(str(results[seed]["last_checkpoint_path"])).resolve()
        _atomic_torch_save(path, payloads[seed])

    migrated_report = dict(report)
    migrated_report["trainer_implementation_sha256"] = live_trainer_sha
    training_identity = {
        "training_config": migrated_report.get("training_config"),
        "coverage_target": migrated_report.get("coverage_target"),
        "selected_sequence_sha256": new_sha,
        "trainer_implementation_sha256": live_trainer_sha,
        "external_run_config_sha256": migrated_report.get("external_run_config_sha256"),
        "selection_manifest_file_sha256": migrated_report.get("selection_manifest_file_sha256"),
    }
    migrated_report["training_config_sha256"] = hashlib.sha256(json.dumps(
        training_identity, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    _atomic_text_save(
        report_file,
        json.dumps(migrated_report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    migration = {
        "schema": "meta-specialist-v4-resume-lineage-migration-v1",
        "migrated_unix": time.time(), "report_path": str(report_file),
        "seed_count": 2, "old_selected_objective_sha256": old_sha,
        "new_selected_objective_sha256": new_sha,
        "old_trainer_implementation_sha256": old_report_trainer_sha,
        "new_trainer_implementation_sha256": live_trainer_sha,
        "reason": "train-validation concatenation order differed from materializer sequence order",
        "checkpoint_file_sha256": {
            seed: hashlib.sha256(Path(str(results[seed]["last_checkpoint_path"])).read_bytes()).hexdigest()
            for seed in ("0", "1")
        },
    }
    (root_path / "resume-lineage-migration-v1.json").write_text(
        json.dumps(migration, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8",
    )
    return migration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(migrate_legacy_resume_lineage_v4(args.root, report_path=args.report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
