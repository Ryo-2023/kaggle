"""TDD for sealed screen JSONL -> cross-fitted outcome target manifests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from tests.meta_specialist.test_cross_fitted_outcome_residual_v1 import _episode


def _load_builder():
    script = Path(__file__).resolve().parents[2] / "scripts" / "build_cross_fitted_outcome_residual_manifest_v1.py"
    spec = importlib.util.spec_from_file_location("build_cross_fitted_outcome_residual_manifest_v1", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_screen(path: Path) -> None:
    rows: list[dict[str, object]] = []
    for episode_index, reward in enumerate((1.0, -1.0, 1.0, -1.0)):
        episode = _episode(episode_index, reward)
        for transition_index, transition in enumerate(episode.transitions):
            rows.append({
                "schema": "meta-specialist-v4-dagger-transition-v1",
                "component_id": episode.episode_id,
                "env_seed": 100 + episode_index,
                "episode_group": episode.episode_id,
                "game_id": episode.episode_id,
                "opponent_id": f"opponent-{episode_index}",
                "partition": "train",
                "seat": episode_index % 2,
                "transition_index": transition_index,
                "transition": transition.to_dict(),
            })
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def test_builder_exports_only_closed_anonymous_outcome_targets(tmp_path: Path) -> None:
    builder = _load_builder()
    source = tmp_path / "screen.transitions.jsonl"
    output = tmp_path / "targets.json"
    _write_screen(source)

    summary = builder.build_manifest_from_screen_jsonl_v1(source, output=output, fold_count=2)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert summary["episodes"] == 4
    assert summary["transitions"] == 8
    assert summary["source_file_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert payload["objective_kind"] == "cross_fitted_mc_signed_behavior_residual"
    assert "opponent" not in json.dumps(payload)
    assert "seat" not in json.dumps(payload)
    assert all(target["target_kind"] == "signed_behavior_log_probability" for episode in payload["episodes"] for target in episode["targets"])


def test_builder_rejects_game_reentry_or_bad_transition_order(tmp_path: Path) -> None:
    builder = _load_builder()
    source = tmp_path / "screen.transitions.jsonl"
    _write_screen(source)
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    rows[2]["transition_index"] = 9
    source.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")

    try:
        builder.build_manifest_from_screen_jsonl_v1(source, output=tmp_path / "targets.json", fold_count=2)
    except ValueError as exc:
        assert "order" in str(exc) or "contiguous" in str(exc)
    else:  # pragma: no cover - explicit failure signal
        raise AssertionError("builder accepted noncontiguous screen transition order")
