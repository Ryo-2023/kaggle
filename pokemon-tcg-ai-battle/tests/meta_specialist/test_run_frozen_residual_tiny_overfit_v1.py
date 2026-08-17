"""Fail-closed tests for the research-only residual tiny-overfit descriptor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_frozen_residual_tiny_overfit_v1 as runner
from tests.meta_specialist.test_frozen_residual_preflight_v1 import _manifest


def test_tiny_overfit_refuses_without_explicit_dry_run(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest().to_dict()), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        runner.main(["--manifest", str(manifest_path)])
    assert exc.value.code == 2


def test_tiny_overfit_dry_run_emits_no_execution_descriptor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "descriptor.json"
    manifest_path.write_text(json.dumps(_manifest().to_dict()), encoding="utf-8")
    assert runner.main([
        "--manifest", str(manifest_path), "--dry-run", "--output", str(output_path),
    ]) == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["execution"] == "DRY_RUN_NOT_STARTED"
    assert payload["evidence_class"] == "SELF_IMITATION_INTEGRATION_ONLY"
    assert payload["performance_evidence"] is False
    assert payload["training_permitted"] is False
    assert payload["cabt_permitted"] is False
    assert payload["longrun_allowed"] is False
    assert payload["optimizer_updates"] == 0
    assert tuple(item["seed"] for item in payload["seeds"]) == (0, 1)
    assert json.loads(capsys.readouterr().out)["execution"] == "DRY_RUN_NOT_STARTED"
