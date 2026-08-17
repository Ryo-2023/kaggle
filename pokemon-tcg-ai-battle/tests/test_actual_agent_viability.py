"""Focused contracts for evaluation-only actual-cabt viability adapters."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mage_ptcg.evaluation.actual_agents import InstrumentedAgent, _availability, agent_inventory, make_instrumented_agent
from mage_ptcg.student.artifact import ArtifactValidationError, build_artifact, load_validated_artifact
from scripts.build_student_actual_artifact import _smoke_examples
from scripts.run_actual_agent_viability import _gate_status, _privacy_scan, ViabilityError, run_actual_agent_viability

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def neural_export_paths(tmp_path_factory) -> dict[str, Path]:
    """Build one tiny trained neural export, re-exported under both purposes."""
    pytest.importorskip("torch")
    from mage_ptcg.offline_training.collection import collection_dataset_path, run_collection
    from mage_ptcg.offline_training.dataset import build_dataset
    from mage_ptcg.offline_training import export as export_mod
    from mage_ptcg.offline_training import neural
    from mage_ptcg.student.artifact import feature_schema

    root = tmp_path_factory.mktemp("neural_collection")
    run_collection(
        source="fixture", run_id="cabt", games=6, base_seed=1000, output_root=root,
        canonical_base_sha="a" * 40, deck_path=REPOSITORY_ROOT / "deck.csv", repository_root=REPOSITORY_ROOT,
        validation_percent=20, split_seed=0, fixture_decisions_per_seat=4, fixture_option_count=3,
    )
    collected = collection_dataset_path(root, "cabt")
    dataset_dir = tmp_path_factory.mktemp("neural_dataset") / "canonical"
    build_dataset(
        source_jsonl=collected, output_dir=dataset_dir, shard_size=8, split_seed=12345,
        train_fraction=0.5, validation_fraction=0.25, test_fraction=0.25,
        teacher_id="rule-agent-v0", trainer_id="offline-training-v1", source_collection_hash="NONE",
    )
    ckdir = tmp_path_factory.mktemp("neural_checkpoints")
    neural.train(
        dataset_dir=dataset_dir, checkpoint_dir=ckdir, hidden_dims=[8], epochs=1,
        learning_rate=3e-4, weight_decay=1e-4, grad_clip=1.0, patience=5, seed=7,
        max_batch_decisions=64, model_purpose=neural.MODEL_PURPOSE_ACTUAL, device="cpu",
    )
    module, meta, spec = neural.load_module_from_checkpoint(ckdir / "best", device="cpu")
    paths: dict[str, Path] = {}
    for label, purpose in (("actual", neural.MODEL_PURPOSE_ACTUAL), ("smoke", neural.MODEL_PURPOSE_SMOKE)):
        document = export_mod.build_export(
            module=module, model_spec_dict=spec.to_dict(), normalization=meta["normalization"],
            feature_schema=feature_schema(), dataset_hash=meta["dataset_hash"], config_hash="cfg",
            teacher_id="rule-agent-v0", model_purpose=purpose,
        )
        path = tmp_path_factory.mktemp(f"neural_export_{label}") / "neural-student-v1.json"
        export_mod.write_export(document, path)
        paths[label] = path
    return paths


def test_inventory_supports_neural_student_challenger_fail_closed(neural_export_paths: dict[str, Path]) -> None:
    assert agent_inventory()["neural_student"].classification == "BLOCKED_BY_MISSING_ARTIFACT"
    with_model = agent_inventory(neural_model_path=neural_export_paths["actual"])
    assert with_model["neural_student"].classification == "RUNNABLE_WITH_MODEL"
    assert with_model["neural_student"].artifact_purpose == "NEURAL_ACTUAL_TRAINED"
    assert with_model["neural_student"].factory_name == "mage_ptcg.evaluation.actual_agents.make_neural_student_agent"


def test_neural_student_inventory_is_blocked_for_invalid_artifact(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not valid json", encoding="utf-8")
    inventory = agent_inventory(neural_model_path=broken)
    assert inventory["neural_student"].classification == "BLOCKED_BY_INVALID_ARTIFACT"


def test_neural_student_instrumentation_separates_inference_and_fallback() -> None:
    class Delegate:
        def __init__(self) -> None:
            self.student_policy = SimpleNamespace(last_decision_trace=None)

        def __call__(self, _observation: dict) -> list[int]:
            self.student_policy.last_decision_trace = {"student": {"status": "selected"}}
            return [0]

    wrapper = InstrumentedAgent(
        availability=_availability(
            "neural_student", classification="RUNNABLE_WITH_MODEL",
            factory_name="mage_ptcg.evaluation.actual_agents.make_neural_student_agent",
            artifact_hash="a" * 64, artifact_purpose="NEURAL_ACTUAL_TRAINED",
            effective_policy="Neural Student v1 with Rule Agent v0 fallback",
        ),
        delegate=Delegate(),
    )
    assert wrapper({"select": {"option": [{}], "minCount": 1, "maxCount": 1}}) == [0]
    metrics = wrapper.public_metrics()
    runtime = metrics["runtime_features"]  # type: ignore[assignment]
    assert runtime["inference_requested"] == runtime["inference_completed"] == runtime["student_selection_count"] == 1
    assert runtime["inference_failed"] == runtime["feature_failure_count"] == 0
    assert metrics["fallback_count"] == 0
    assert metrics["effective_policy_counts"] == {"Neural Student v1": 1}


def _legal_main_observation() -> dict[str, object]:
    """A minimal legal cabt MAIN-selection observation (mirrors package.py's clean-room fixture)."""
    player = {
        "active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
        "confused": False, "deckCount": 53, "discard": [], "hand": [{"id": 1}],
        "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [None] * 6,
    }
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0,
            "players": [player, dict(player)], "result": -1, "retreated": False,
            "stadium": [], "stadiumPlayed": False, "supporterPlayed": False,
            "turn": 1, "turnActionCount": 0, "yourIndex": 0,
        },
        "select": {"type": 0, "context": 0, "option": [{"type": 14}, {"type": 7, "index": 0}], "minCount": 1, "maxCount": 1},
        "step": 1,
    }


def test_neural_student_challenger_runs_through_viability_wiring(tmp_path: Path, neural_export_paths: dict[str, Path]) -> None:
    calls: list[int] = []

    def runner(**kwargs):
        calls.append(int(kwargs["seed"]))
        agent_a = kwargs["agent_a_factory"]([1] * 60, int(kwargs["seed"]))
        agent_b = kwargs["agent_b_factory"]([1] * 60, int(kwargs["seed"]) + 1)
        agent_a(_legal_main_observation())
        agent_b(_legal_main_observation())
        return {"status": "DONE", "winner": 0, "elapsed_seconds": 0.25}

    output = tmp_path / "neural-viability.json"
    result = run_actual_agent_viability(
        challenger_id="neural_student",
        games=1,
        base_seed=3,
        output_path=output,
        canonical_base_sha="a" * 40,
        neural_model_path=neural_export_paths["actual"],
        capability_report=_ready_report(),
        match_runner=runner,
    )
    assert calls == [3]
    assert result["gate_status"] in {"PASS", "CLEAN_PASS", "PASS_WITH_RUNTIME_FALLBACKS"}
    assert result["challenger_metrics"]["model_hash"] is not None


def test_neural_student_smoke_only_is_refused_for_gate_b_before_match(tmp_path: Path, neural_export_paths: dict[str, Path]) -> None:
    with pytest.raises(ViabilityError, match="neural_student_gate_b_requires_actual_trained"):
        run_actual_agent_viability(
            challenger_id="neural_student",
            games=20,
            base_seed=0,
            output_path=tmp_path / "neural-student-gate-b.json",
            canonical_base_sha="a" * 40,
            neural_model_path=neural_export_paths["smoke"],
            capability_report=_ready_report(),
        )


def _ready_report() -> dict[str, object]:
    return {
        "status": "READY",
        "reason_code": "READY",
        "kaggle_environments_version": "1.32.0",
        "requested_environment": "cabt",
        "actual_execution_allowed": True,
        "engine_seed_supported": False,
    }


def test_inventory_is_fail_closed_for_missing_student_and_c5() -> None:
    inventory = agent_inventory()
    assert inventory["rule"].classification == "RUNNABLE"
    assert inventory["deterministic"].classification == "RUNNABLE"
    assert inventory["bounded_search"].classification == "RUNNABLE_WITH_FALLBACK"
    assert inventory["student"].classification == "BLOCKED_BY_MISSING_ARTIFACT"
    assert inventory["c5"].classification == "NOT_A_RUNTIME_AGENT"


def test_student_model_manifest_is_required_and_hash_schema_checked(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    manifest = build_artifact(
        examples=_smoke_examples(), output_dir=artifact, canonical_base_sha="a" * 40,
        work_commit_sha="b" * 40, dataset_source_type="CANONICAL_C4_FIXTURE", artifact_purpose="SMOKE_ONLY", epochs=20,
    )
    model_path, manifest_path = artifact / "student-v0.json", artifact / "manifest.json"
    assert manifest["performance_eligible"] is False
    assert load_validated_artifact(model_path, manifest_path)[1]["model_hash"] == manifest["model_hash"]
    assert agent_inventory(student_model_path=model_path, student_manifest_path=manifest_path)["student"].classification == "RUNNABLE_WITH_MODEL"
    assert agent_inventory(student_model_path=model_path)["student"].classification == "BLOCKED_BY_INVALID_ARTIFACT"
    broken = json.loads(manifest_path.read_text(encoding="utf-8"))
    broken["model_hash"] = "0" * 64
    manifest_path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="hash mismatch"):
        load_validated_artifact(model_path, manifest_path)
    broken["model_hash"] = manifest["model_hash"]
    broken["feature_schema_hash"] = "0" * 64
    manifest_path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="schema mismatch"):
        load_validated_artifact(model_path, manifest_path)
    broken["feature_schema_hash"] = manifest["feature_schema_hash"]
    broken["artifact_purpose"] = "ACTUAL_TRAINED"
    broken["performance_eligible"] = False
    manifest_path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="ACTUAL_TRAINED artifact"):
        load_validated_artifact(model_path, manifest_path)
    broken["artifact_purpose"] = "SMOKE_ONLY"
    broken["performance_eligible"] = True
    manifest_path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match="SMOKE_ONLY artifact"):
        load_validated_artifact(model_path, manifest_path)


def test_student_instrumentation_separates_inference_and_fallback() -> None:
    class Delegate:
        def __init__(self) -> None:
            self.student_policy = SimpleNamespace(last_decision_trace=None)

        def __call__(self, _observation: dict) -> list[int]:
            self.student_policy.last_decision_trace = {"student": {"status": "selected"}}
            return [0]

    wrapper = InstrumentedAgent(
        availability=_availability("student", classification="RUNNABLE_WITH_MODEL", factory_name="main.make_student_agent", artifact_hash="a" * 64, artifact_purpose="SMOKE_ONLY", effective_policy="Student v0 with Rule Agent v0 fallback"),
        delegate=Delegate(),
    )
    assert wrapper({"select": {"option": [{}], "minCount": 1, "maxCount": 1}}) == [0]
    metrics = wrapper.public_metrics()
    runtime = metrics["runtime_features"]  # type: ignore[assignment]
    assert runtime["inference_requested"] == runtime["inference_completed"] == runtime["student_selection_count"] == 1
    assert runtime["inference_failed"] == runtime["feature_failure_count"] == 0
    assert metrics["fallback_count"] == 0
    assert metrics["effective_policy_counts"] == {"Student v0": 1}


def test_bounded_search_wrapper_records_fallback_only_without_observation_leakage() -> None:
    class Delegate:
        last_search_result = None

        def __call__(self, _observation: dict) -> list[int]:
            self.last_search_result = SimpleNamespace(
                expansions=0,
                budget_exhaustion_reason="complete",
                fallback_reason="engine_adapter_unavailable",
            )
            return [0]

    wrapper = InstrumentedAgent(
        availability=agent_inventory()["bounded_search"],
        delegate=Delegate(),
    )
    assert wrapper.__name__ == "evaluation_agent"
    assert wrapper.as_runtime_function().__name__ == "evaluation_agent"
    assert wrapper({"select": {"option": [{}], "minCount": 1, "maxCount": 1}}) == [0]
    metrics = wrapper.public_metrics()
    assert metrics["effective_policy"] == "Rule Agent v0 fallback only"
    assert metrics["fallback_count"] == 1
    assert metrics["legal_action_rate"] == 1.0
    assert metrics["decision_latency_samples"] == metrics["decisions"] == 1
    assert metrics["timeout_count"] == "UNKNOWN"
    assert metrics["runtime_features"]["search_block_reasons"] == {"engine_adapter_unavailable": 1}  # type: ignore[index]
    assert "observation" not in json.dumps(metrics)


def test_viability_runner_redacts_raw_runner_values_and_resumes(tmp_path: Path) -> None:
    calls: list[int] = []

    def runner(**kwargs):
        calls.append(int(kwargs["seed"]))
        kwargs["agent_a_factory"]([1] * 60, int(kwargs["seed"]))
        kwargs["agent_b_factory"]([1] * 60, int(kwargs["seed"]) + 1)
        return {
            "status": "DONE",
            "winner": 0,
            "elapsed_seconds": 0.25,
            "raw_observation": {"hand": [{"id": 700}]},
            "terminal_reason": "private exception text",
            "deck_a": "/home/private/deck.csv",
        }

    output = tmp_path / "viability.json"
    kwargs = {
        "challenger_id": "deterministic",
        "games": 1,
        "base_seed": 3,
        "output_path": output,
        "canonical_base_sha": "a" * 40,
        "capability_report": _ready_report(),
        "match_runner": runner,
    }
    first = run_actual_agent_viability(**kwargs)
    second = run_actual_agent_viability(**kwargs)

    assert first["gate_status"] == second["gate_status"] == "PASS"
    assert calls == [3]
    encoded = output.read_text(encoding="utf-8")
    assert "raw_observation" not in encoded
    assert "terminal_reason" not in encoded
    assert "/home/private" not in encoded
    # Match the private observation value in its serialized field context;
    # a bare numeric substring can occur legitimately in a commit SHA.
    assert '"id":700' not in encoded
    assert first["privacy_scan_executed"] is True
    assert first["privacy_violations"] == 0
    assert first["privacy_violation_categories"] == {}
    assert first["config"]["engine_seed_supported"] is False
    assert first["config"]["agent_seed_schedule_deterministic"] is True
    assert first["config"]["seat_schedule_deterministic"] is True
    assert first["config"]["engine_outcomes_deterministic"] is False
    assert first["resume_duplicate_execution_detected"] is False
    assert first["resume_duplicate_measurement"] == "STRUCTURAL_GUARANTEE"


@pytest.mark.parametrize(
    ("unsafe", "category"),
    [
        ({"raw_observation": {"hand": [{"id": 700}]}}, "raw_observation"),
        ({"card_id": 700}, "raw_card_identity"),
        ({"candidate_identity": "private-candidate"}, "candidate_identity"),
        ({"identity_hash": "private-hash"}, "identity_hash"),
        ({"actor_a_view": {"hand": [{"id": 700}]}}, "actor_cross_contamination"),
        ({"detail": "/home/private/secret"}, "absolute_private_path"),
        ({"contact": "person@example.com"}, "secret_or_private_value"),
        ({"download": "https://example.invalid/file?X-Amz-Signature=private"}, "secret_or_private_value"),
        ({"terminal_reason": "private exception text"}, "raw_exception_message"),
        ({"search_begin_input": "opaque"}, "opaque_observation_field"),
        ({"logs": ["private"]}, "opaque_observation_field"),
        ({"remainingOverageTime": 50}, "opaque_observation_field"),
    ],
)
def test_privacy_scanner_detects_controlled_categories_without_retaining_values(unsafe: dict[str, object], category: str) -> None:
    result = _privacy_scan(unsafe)
    assert result["privacy_scan_executed"] is True
    assert result["privacy_violations"] > 0
    assert category in result["privacy_violation_categories"]
    encoded = json.dumps(result)
    assert "private exception text" not in encoded
    assert "/home/private/secret" not in encoded
    assert "700" not in encoded


def test_unscanned_or_violating_privacy_cannot_pass_gate() -> None:
    assert _gate_status({"privacy_scan_executed": False}, "deterministic") == ("STOPPED", "privacy_scan_not_executed")
    assert _gate_status({"privacy_scan_executed": True, "privacy_violations": "UNKNOWN"}, "deterministic") == ("STOPPED", "privacy_scan_not_executed")
    assert _gate_status({"privacy_scan_executed": True, "privacy_violations": 1}, "deterministic") == ("STOPPED", "privacy_violation_detected")


def test_resume_rejects_different_engine_seed_capability_provenance(tmp_path: Path) -> None:
    output = tmp_path / "viability.json"

    def runner(**kwargs):
        kwargs["agent_a_factory"]([1] * 60, int(kwargs["seed"]))
        kwargs["agent_b_factory"]([1] * 60, int(kwargs["seed"]) + 1)
        return {"status": "DONE", "winner": 0, "elapsed_seconds": 0.01}

    kwargs = {
        "challenger_id": "deterministic",
        "games": 1,
        "base_seed": 1,
        "output_path": output,
        "canonical_base_sha": "a" * 40,
        "capability_report": _ready_report(),
        "match_runner": runner,
    }
    first = run_actual_agent_viability(**kwargs)
    assert first["config"]["engine_seed_supported"] is False
    changed = {**_ready_report(), "engine_seed_supported": True}
    with pytest.raises(ViabilityError, match="different_config"):
        run_actual_agent_viability(**{**kwargs, "capability_report": changed})


def test_viability_refuses_c5_and_bounded_search_smoke_before_match(tmp_path: Path) -> None:
    with pytest.raises(ViabilityError, match="NOT_A_RUNTIME_AGENT"):
        run_actual_agent_viability(
            challenger_id="c5",
            games=1,
            base_seed=0,
            output_path=tmp_path / "c5.json",
            canonical_base_sha="a" * 40,
            capability_report=_ready_report(),
        )
    with pytest.raises(ViabilityError, match="requires_passing"):
        run_actual_agent_viability(
            challenger_id="bounded_search",
            games=20,
            base_seed=0,
            output_path=tmp_path / "search.json",
            canonical_base_sha="a" * 40,
            capability_report=_ready_report(),
        )


def test_student_smoke_only_is_refused_for_gate_b_before_match(tmp_path: Path) -> None:
    artifact = tmp_path / "smoke"
    build_artifact(
        examples=_smoke_examples(), output_dir=artifact, canonical_base_sha="a" * 40,
        work_commit_sha="b" * 40, dataset_source_type="CANONICAL_C4_FIXTURE", artifact_purpose="SMOKE_ONLY", epochs=20,
    )
    with pytest.raises(ViabilityError, match="student_gate_b_requires_actual_trained"):
        run_actual_agent_viability(
            challenger_id="student",
            games=20,
            base_seed=0,
            output_path=tmp_path / "student-gate-b.json",
            canonical_base_sha="a" * 40,
            student_model_path=artifact / "student-v0.json",
            student_manifest_path=artifact / "manifest.json",
            capability_report=_ready_report(),
        )


def test_neural_student_package_inventory_and_run(tmp_path: Path, neural_export_paths: dict[str, Path]) -> None:
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "models").mkdir()
    import shutil
    shutil.copyfile(neural_export_paths["actual"], pkg_dir / "models/neural-student-v1.json")
    (pkg_dir / "deck.csv").write_text("1,2,3", encoding="utf-8")
    
    manifest = {
        "model_hash": "a" * 64,
        "model_purpose": "NEURAL_ACTUAL_TRAINED",
        "archive_sha256": "pkg_hash",
        "files": []
    }
    import json
    (pkg_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    
    main_code = (
        "import sys\n"
        "def make_neural_agent(*, deck=None, model_path=None):\n"
        "    from mage_ptcg.offline_training.neural_runtime import NeuralRuntimePolicy\n"
        "    policy = NeuralRuntimePolicy.load(model_path)\n"
        "    def agent(obs):\n"
        "        return policy.choose(obs)\n"
        "    return agent\n"
    )
    (pkg_dir / "main.py").write_text(main_code, encoding="utf-8")

    inventory = agent_inventory(package_path=pkg_dir)
    assert inventory["neural_student_package"].classification == "RUNNABLE_WITH_MODEL"
    assert inventory["neural_student_package"].artifact_purpose == "NEURAL_ACTUAL_TRAINED"

    calls = []
    def runner(**kwargs):
        calls.append(int(kwargs["seed"]))
        agent_a = kwargs["agent_a_factory"]([1] * 60, int(kwargs["seed"]))
        agent_b = kwargs["agent_b_factory"]([1] * 60, int(kwargs["seed"]) + 1)
        agent_a(_legal_main_observation())
        agent_b(_legal_main_observation())
        return {"status": "DONE", "winner": 0, "elapsed_seconds": 0.1}

    output = tmp_path / "package-viability.json"
    result = run_actual_agent_viability(
        challenger_id="neural_student_package",
        games=1,
        base_seed=10,
        output_path=output,
        canonical_base_sha="a" * 40,
        package_path=pkg_dir,
        capability_report=_ready_report(),
        match_runner=runner,
    )
    assert calls == [10]
    assert result["gate_status"] == "CLEAN_PASS"
