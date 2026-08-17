from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from mage_ptcg.continuous_league.catalog import CatalogEntry, CatalogSnapshot
from mage_ptcg.continuous_league.cli import _cmd_build_population
from mage_ptcg.continuous_league.contracts import LeagueContractError, content_id


ROOT = Path(__file__).resolve().parents[1]


def _entry(name: str, kind: str) -> CatalogEntry:
    return CatalogEntry(
        asset_id=name,
        policy_id=f"policy-{name}",
        deck_id=f"deck-{name}",
        source_id=f"source-{name}",
        policy_kind=kind,
        runtime_path=f"builtin:{kind}",
        deck_path="deck.csv",
        policy_hash=content_id("test", f"policy-{name}"),
        deck_hash=content_id("test", f"deck-{name}"),
        source_hash=content_id("test", f"source-{name}"),
        runtime_config_hash=content_id("test", kind),
        role="TRAINING_ACTIVE",
    )


def test_build_population_can_select_policy_kind(tmp_path: Path) -> None:
    catalog = CatalogSnapshot.build(
        [_entry("rule-v0", "rule_v0"), _entry("rule-v1", "rule_v1")]
    )
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(catalog.to_dict()),
        encoding="utf-8",
    )
    output = tmp_path / "population"
    result = _cmd_build_population(
        argparse.Namespace(
            catalog=catalog_path,
            role=["TRAINING_ACTIVE"],
            policy_kind=["rule_v1"],
            opponent_instance=None,
            output=output,
        )
    )
    mixture = json.loads((output / "mixture.json").read_text(encoding="utf-8"))
    assert result["members"] == 1
    assert mixture["members"][0]["kind"] == "rule_v1"


def test_bootstrap_distill_cli_reads_only_sealed_semantic_examples(tmp_path: Path) -> None:
    from mage_ptcg.bootstrap_champion.teacher import (
        BootstrapTeacherExample,
        collect_teacher_dataset,
    )
    from mage_ptcg.continuous_league.cli import _cmd_bootstrap_distill

    dataset = tmp_path / "teacher"
    collect_teacher_dataset(
        examples=[
            BootstrapTeacherExample(
                game_id="game-a",
                decision_index=0,
                public_state={"turn": 1},
                own_private_state={"hand_size": 2},
                visible_history=(),
                legal_action_keys=("a", "b"),
                selected_action_key="a",
                outcome="win",
                behavior_weight=1.0,
                teacher_candidate_id="a" * 64,
                encoded_state=(0.0, 1.0),
                encoded_actions=((1.0, 0.0), (0.0, 1.0)),
                selected_action=0,
            )
        ],
        excluded_game_ids=set(),
        skipped_multi_select_decisions=0,
        deck_hash="b" * 64,
        teacher_candidate_id="a" * 64,
        seed=7,
        output=dataset,
    )
    config = tmp_path / "distill.json"
    config.write_text(
        json.dumps(
            {
                "model": {
                    "state_size": 2,
                    "action_size": 2,
                    "hidden_size": 4,
                    "atoms": 3,
                    "opponent_classes": 2,
                    "deck_family_classes": 2,
                    "action_type_classes": 2,
                },
                "distillation": {"max_epochs": 1, "batch_size": 1},
            }
        ),
        encoding="utf-8",
    )

    result = _cmd_bootstrap_distill(
        argparse.Namespace(
            teacher_dataset=dataset,
            config=config,
            device="cpu",
            output=tmp_path / "distilled",
        )
    )

    assert Path(result["weights_path"]).is_file()
    assert result["teacher_dataset_id"] == json.loads(
        (dataset / "manifest.json").read_text(encoding="utf-8")
    )["teacher_dataset_id"]


def test_bootstrap_distill_cli_rejects_teacher_model_dimension_mismatch(tmp_path: Path) -> None:
    from mage_ptcg.bootstrap_champion.teacher import (
        BootstrapTeacherExample,
        collect_teacher_dataset,
    )
    from mage_ptcg.continuous_league.cli import _cmd_bootstrap_distill

    dataset = tmp_path / "teacher"
    collect_teacher_dataset(
        examples=[
            BootstrapTeacherExample(
                game_id="game-a", decision_index=0, public_state={}, own_private_state={},
                visible_history=(), legal_action_keys=("a",), selected_action_key="a",
                outcome="win", behavior_weight=1.0, teacher_candidate_id="a" * 64,
                encoded_state=(0.0, 1.0), encoded_actions=((1.0, 0.0),), selected_action=0,
            )
        ],
        excluded_game_ids=set(), skipped_multi_select_decisions=0,
        deck_hash="b" * 64, teacher_candidate_id="a" * 64, seed=7, output=dataset,
    )
    config = tmp_path / "mismatch.json"
    config.write_text(json.dumps({"model": {"state_size": 3, "action_size": 2}}), encoding="utf-8")

    with pytest.raises(LeagueContractError, match="state encoding width"):
        _cmd_bootstrap_distill(
            argparse.Namespace(teacher_dataset=dataset, config=config, device="cpu", output=tmp_path / "distilled")
        )


def test_bootstrap_publish_runtime_cli_uses_bootstrap_bundle_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mage_ptcg.continuous_league.cli as continuous_cli

    deck = tmp_path / "deck.csv"
    deck.write_text("\n".join(["1"] * 60) + "\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"model": {"hidden_size": 4}}), encoding="utf-8")
    calls: dict[str, object] = {}

    def publish(**kwargs: object) -> dict[str, str]:
        calls.update(kwargs)
        return {"runtime_policy_id": "a" * 64}

    monkeypatch.setattr(
        continuous_cli,
        "publish_bootstrap_runtime",
        publish,
        raising=False,
    )
    result = continuous_cli._cmd_bootstrap_publish_runtime(
        argparse.Namespace(
            bootstrap_checkpoint=tmp_path / "bootstrap",
            deck=deck,
            config=config,
            output=tmp_path / "runtime",
        )
    )

    assert result["runtime_policy_id"] == "a" * 64
    assert calls["bootstrap_checkpoint"] == tmp_path / "bootstrap"
    assert calls["deck"] == [1] * 60
    assert calls["model_config"].hidden_size == 4


def test_collect_cli_forwards_execution_block_to_keep_chunks_disjoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mage_ptcg.continuous_league.cli as continuous_cli

    deck = tmp_path / "deck.csv"
    deck.write_text("\n".join(["1"] * 60) + "\n", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(continuous_cli, "_catalog", lambda _path: object())
    monkeypatch.setattr(
        continuous_cli,
        "load_runtime_policy",
        lambda _path: type("Runtime", (), {"runtime_policy_id": "a" * 64})(),
    )
    monkeypatch.setattr(continuous_cli, "_mixture", lambda _path: object())
    monkeypatch.setattr(continuous_cli, "CabtMatchExecutor", lambda **_kwargs: object())
    monkeypatch.setattr(
        continuous_cli,
        "CollectionRequest",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        continuous_cli,
        "collect_experience",
        lambda **kwargs: captured.update(kwargs) or {"status": "COMPLETE"},
    )

    result = continuous_cli._cmd_collect(
        argparse.Namespace(
            catalog=tmp_path / "catalog.json",
            runtime=tmp_path / "runtime",
            mixture=tmp_path / "mixture.json",
            deck=deck,
            output=tmp_path / "collection",
            population_epoch_id="b" * 64,
            subject_deck_id="bootstrap-champion",
            episodes=16,
            opponent_episodes=None,
            seed=71_000,
            max_steps=10_000,
            execution_block="bootstrap-general-v1",
        )
    )

    assert result == {"status": "COMPLETE"}
    assert captured["request"]["execution_block"] == "bootstrap-general-v1"



def test_task_worker_records_checkpoint_evaluation_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from argparse import Namespace
    from types import SimpleNamespace

    import mage_ptcg.continuous_league.cli as continuous_cli

    checkpoint_id = content_id("task-worker-test", "checkpoint")
    runtime_id = content_id("task-worker-test", "runtime")
    benchmark_id = content_id("task-worker-test", "benchmark")
    exposure_id = content_id("task-worker-test", "exposure")
    result_id = content_id("task-worker-test", "result")
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(
        json.dumps(
            {
                "task_id": "task-1",
                "task_type": "VISIBLE_EVALUATION",
                "payload": {
                    "training_checkpoint_id": checkpoint_id,
                    "training_step": 10_000,
                    "runtime_policy_id": runtime_id,
                    "benchmark_id": benchmark_id,
                    "exposure_snapshot_id": exposure_id,
                },
            }
        ),
        encoding="utf-8",
    )
    history_root = tmp_path / "history"
    monkeypatch.setattr(
        continuous_cli,
        "_load_mapping",
        lambda _path: {
            "catalog": "unused",
            "benchmark": "unused",
            "exposure": "unused",
            "runtime_policy_root": str(tmp_path / "runtime"),
            "evaluation_output_root": str(tmp_path / "evaluations"),
            "checkpoint_evaluation_history_root": str(history_root),
        },
    )
    monkeypatch.setattr(
        continuous_cli,
        "load_json",
        lambda path: (
            json.loads(request_path.read_text(encoding="utf-8"))
            if Path(path) == request_path
            else {}
        ),
    )
    monkeypatch.setattr(continuous_cli, "_catalog", lambda _path: object())
    monkeypatch.setattr(
        continuous_cli,
        "BenchmarkManifest",
        SimpleNamespace(
            from_dict=lambda _payload, _catalog: SimpleNamespace(
                subject_decks=(), benchmark_id=benchmark_id
            )
        ),
    )
    monkeypatch.setattr(
        continuous_cli,
        "_exposure",
        lambda _path: SimpleNamespace(exposure_snapshot_id=exposure_id),
    )
    monkeypatch.setattr(
        continuous_cli,
        "load_runtime_policy",
        lambda _path: SimpleNamespace(runtime_policy_id=runtime_id),
    )
    monkeypatch.setattr(continuous_cli, "CabtMatchExecutor", lambda **_kwargs: object())
    monkeypatch.setattr(
        continuous_cli,
        "EvaluationJob",
        SimpleNamespace(build=lambda *_args: SimpleNamespace(evaluation_job_id="job")),
    )
    evaluation = {
        "evaluation_result_id": result_id,
        "runtime_policy_id": runtime_id,
        "benchmark_id": benchmark_id,
        "exposure_snapshot_id": exposure_id,
        "scheduled_games": 512,
        "completed_games": 512,
        "is_schedule_complete": True,
        "aggregate": {
            "status": "COMPLETE",
            "fault_count": 0,
            "game_weighted": {
                "games": 512,
                "wins": 256,
                "losses": 256,
                "draws": 0,
                "score_rate": 0.5,
                "wilson_95": [0.45, 0.55],
            },
            "opponent_equal_score_rate": 0.5,
            "worst_opponent_score_rate": 0.25,
        },
    }
    monkeypatch.setattr(
        continuous_cli, "run_evaluation", lambda **_kwargs: evaluation
    )

    result = continuous_cli._cmd_task_worker(
        Namespace(config=tmp_path / "config.yaml", task_request=request_path, result=result_path)
    )

    assert result["checkpoint_evaluation"]["training_step"] == 10_000
    assert json.loads(result_path.read_text(encoding="utf-8")) == result
    summary = json.loads((history_root / "evaluation_summary.json").read_text(encoding="utf-8"))
    assert summary["latest_complete"]["evaluation_result_id"] == result_id


def test_controller_forwards_worker_output_when_terminal_is_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    import mage_ptcg.continuous_league.cli as continuous_cli

    captured = []

    class CapturingHandler:
        def __init__(
            self, _command, _request_root, *, forward_output=False, quiet_result=False
        ):
            captured.append((forward_output, quiet_result))

    class OneShotController:
        def __init__(self, **_kwargs):
            return None

        def run_once(self):
            return {"status": "IDLE"}

    monkeypatch.setattr(continuous_cli, "SubprocessTaskHandler", CapturingHandler)
    monkeypatch.setattr(continuous_cli, "ContinuousLeagueController", OneShotController)
    monkeypatch.setattr(
        continuous_cli,
        "sys",
        SimpleNamespace(stderr=SimpleNamespace(isatty=lambda: True)),
        raising=False,
    )
    handler_config = tmp_path / "handlers.yaml"
    handler_config.write_text("VISIBLE_EVALUATION: worker-command\n", encoding="utf-8")

    result = continuous_cli._cmd_controller(
        argparse.Namespace(
            handler_config=handler_config,
            evaluation_command=None,
            root=tmp_path / "controller",
            events=tmp_path / "events",
            inbox=None,
            benchmark_id=content_id("test", "benchmark"),
            exposure_snapshot_id=content_id("test", "exposure"),
            cpu_slots=1,
            gpu_slots=0,
            max_pending_evaluations=0,
            recover_interrupted=False,
            checkpoint_history=tmp_path / "history",
            once=True,
            poll_seconds=1.0,
        )
    )

    assert result["status"] == "IDLE"
    assert captured == [(True, True)]



def test_checkpoint_benchmark_examples_build_512_and_1024_game_schedules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from argparse import Namespace
    from types import SimpleNamespace

    from mage_ptcg.continuous_league.benchmark import build_schedule

    import mage_ptcg.continuous_league.cli as continuous_cli

    class ExampleCatalog:
        catalog_snapshot_id = content_id("benchmark-example-test", "catalog")

        @staticmethod
        def get_instance(_opponent_id: str) -> SimpleNamespace:
            return SimpleNamespace(role="BENCHMARK_VISIBLE")

    monkeypatch.setattr(continuous_cli, "_catalog", lambda _path: ExampleCatalog())
    for expected_games, filename in (
        (512, "benchmark_512.example.yaml"),
        (1024, "benchmark_1024.example.yaml"),
    ):
        result = continuous_cli._cmd_build_benchmark(
            Namespace(
                catalog=tmp_path / "catalog.json",
                spec=ROOT / "configs" / "continuous_league" / filename,
                output=tmp_path / f"{expected_games}.json",
            )
        )
        manifest = continuous_cli.BenchmarkManifest.from_dict(
            result, ExampleCatalog()
        )
        assert len(build_schedule(manifest, content_id("benchmark-example-test", filename))) == expected_games


def test_benchmark_diversity_requirements_fail_before_manifest_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from argparse import Namespace
    from types import SimpleNamespace

    import mage_ptcg.continuous_league.cli as continuous_cli

    entry = SimpleNamespace(
        opponent_instance_id="opponent",
        role="BENCHMARK_VISIBLE",
        policy_hash=content_id("benchmark-diversity", "one-policy"),
        policy_kind="rule_v0",
    )

    class Catalog:
        catalog_snapshot_id = content_id("benchmark-diversity", "catalog")

        @staticmethod
        def get_instance(_opponent_id: str) -> SimpleNamespace:
            return entry

    monkeypatch.setattr(continuous_cli, "_catalog", lambda _path: Catalog())
    spec = {
        "name": "guarded",
        "opponent_instance_ids": ["opponent"],
        "subject_decks": [{
            "deck_id": "subject", "deck_path": "deck.csv",
            "deck_hash": content_id("benchmark-diversity", "deck"),
        }],
        "minimum_distinct_policy_hashes": 2,
    }
    spec_path = tmp_path / "guarded.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(LeagueContractError, match="distinct policy hashes"):
        continuous_cli._cmd_build_benchmark(
            Namespace(catalog=tmp_path / "catalog.json", spec=spec_path, output=tmp_path / "out.json")
        )
    assert not (tmp_path / "out.json").exists()
