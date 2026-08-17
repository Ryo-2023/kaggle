"""Contract tests for the CABT-free frozen-residual strength descriptor."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.frozen_residual_loader_v1 import SIDECAR_ARTIFACT_SCHEMA_V1
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    Wave6ProvenanceV1,
    build_frozen_residual_preflight_manifest_v1,
    build_seed_known_manifest_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import (
    FrozenResidualSidecarV1,
    STOP_ACTION_KEY_V1,
)


def _load_runner():
    script = Path(__file__).resolve().parents[2] / "scripts" / "measure_frozen_residual_strength_v1.py"
    spec = importlib.util.spec_from_file_location("measure_frozen_residual_strength_v1", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, str, Path, str, Path, str]:
    deck = tmp_path / "subject.csv"
    deck.write_text("1\n", encoding="utf-8")
    deck_sha = _sha(deck.read_bytes())
    domains = []
    for seed in (0, 1):
        provenance = Wave6ProvenanceV1(
            seed=seed,
            checkpoint_path=f"/sealed/base-{seed}.pt",
            checkpoint_file_sha256=_sha(f"checkpoint-file-{seed}"),
            checkpoint_tensor_state_sha256=_sha(f"checkpoint-tensor-{seed}"),
            screen_path=f"/sealed/screen-{seed}.json",
            screen_file_sha256=_sha(f"screen-{seed}"),
            transitions_path=f"/sealed/transitions-{seed}.jsonl",
            transitions_file_sha256=_sha(f"transitions-{seed}"),
            subject_deck_sha256=deck_sha,
        )
        domains.append(build_seed_known_manifest_v1(
            provenance,
            context_ids=(_sha(f"context-{seed}"),),
            action_keys=(_sha(f"action-{seed}"), STOP_ACTION_KEY_V1),
            transition_count=1,
            prefix_count=1,
        ))
    preflight = build_frozen_residual_preflight_manifest_v1(
        tuple(domains), subject_deck_sha256=deck_sha,
    )
    preflight_path = tmp_path / "preflight.json"
    preflight_path.write_text(json.dumps(preflight.to_dict(), sort_keys=True), encoding="utf-8")

    sidecar_path = tmp_path / "sidecar.pt"
    domain = preflight.seeds[0]
    sidecar = FrozenResidualSidecarV1(
        known_context_ids=domain.context_ids,
        known_action_keys=domain.action_keys,
        base_checkpoint_file_sha256=domain.provenance.checkpoint_file_sha256,
        base_checkpoint_tensor_sha256=domain.provenance.checkpoint_tensor_state_sha256,
    )
    torch.save({
        "schema_version": SIDECAR_ARTIFACT_SCHEMA_V1,
        "base_checkpoint_file_sha256": domain.provenance.checkpoint_file_sha256,
        "base_checkpoint_tensor_state_sha256": domain.provenance.checkpoint_tensor_state_sha256,
        "target_kind": "signed_behavior_log_probability",
        "target_manifest_file_sha256": _sha("signed-targets"),
        "source_episode_sha256": _sha("source-episodes"),
        "state_dict": sidecar.state_dict(),
        "training_permitted": False,
        "promotion_authority": False,
        "longrun_allowed": False,
    }, sidecar_path)
    return (
        deck,
        deck_sha,
        preflight_path,
        _sha(preflight_path.read_bytes()),
        sidecar_path,
        _sha(sidecar_path.read_bytes()),
    )


def _arguments(
    deck: Path,
    deck_sha: str,
    preflight: Path,
    preflight_sha: str,
    sidecar: Path,
    sidecar_sha: str,
    output: Path,
) -> list[str]:
    return [
        "--sidecar", str(sidecar), "--sidecar-sha256", sidecar_sha,
        "--preflight", str(preflight), "--preflight-sha256", preflight_sha,
        "--seed", "0", "--base-deck-csv", str(deck),
        "--base-deck-sha256", deck_sha, "--base-archetype-id", "alakazam",
        "--games-per-cell", "2", "--output", str(output),
    ]


def test_dry_run_emits_hash_bound_identity_and_zero_observation_coverage(tmp_path: Path) -> None:
    """The descriptor must remain a non-authorizing plan until CABT is implemented."""
    runner = _load_runner()
    deck, deck_sha, preflight, preflight_sha, sidecar, sidecar_sha = _fixture(tmp_path)
    output = tmp_path / "descriptor.json"

    assert runner.main(_arguments(
        deck, deck_sha, preflight, preflight_sha, sidecar, sidecar_sha, output,
    )) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == runner.FROZEN_RESIDUAL_STRENGTH_SCHEMA_V1
    assert payload["execution"] == "DRY_RUN_NOT_EXECUTED"
    assert payload["cabt_invoked"] is False
    assert payload["research_only"] is True
    assert payload["performance_evidence"] is False
    assert payload["promotion_authority"] is False
    assert payload["training_permitted"] is False
    assert payload["longrun_allowed"] is False
    assert payload["engine_seed_supported"] is False
    assert payload["pairing"] == "independent_stratified_not_game_paired"
    assert payload["fixed_held_out_opponent_ids"] == list(runner.EVAL_HELD_OUT_V1)
    assert payload["planned_cells"] == 12
    assert payload["planned_games"] == 24
    assert payload["base_deck"]["file_sha256"] == deck_sha
    assert payload["preflight"]["file_sha256"] == preflight_sha
    assert payload["sidecar"]["file_sha256"] == sidecar_sha
    assert payload["loader"]["base_checkpoint_file_sha256"] == payload["factory"]["base_checkpoint_file_sha256"]
    assert len(payload["factory"]["identity_sha256"]) == 64
    assert payload["coverage"] == {
        "total_decisions": 0,
        "known_context": 0,
        "known_action": 0,
        "nonzero_residual": 0,
        "ood_pass_through": 0,
        "stop": 0,
    }


def test_execute_requires_the_two_game_cell_bound_and_has_no_cabt_path(tmp_path: Path) -> None:
    """An accidental execution request must fail before any engine integration exists."""
    runner = _load_runner()
    deck, deck_sha, preflight, preflight_sha, sidecar, sidecar_sha = _fixture(tmp_path)
    output = tmp_path / "descriptor.json"
    arguments = _arguments(deck, deck_sha, preflight, preflight_sha, sidecar, sidecar_sha, output)

    with pytest.raises(ValueError, match="games-per-cell"):
        runner.main([*arguments[:-4], "--games-per-cell", "3", "--output", str(output), "--execute"])
    with pytest.raises(RuntimeError, match="not implemented"):
        runner.main([*arguments, "--execute"])
