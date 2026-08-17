from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from mage_ptcg.continuous_league.benchmark import (
    BenchmarkManifest,
    ExposureCohort,
    ExposureSnapshot,
    SubjectDeck,
    build_schedule,
)
from mage_ptcg.continuous_league.calibration import fit_calibration, forecast_public_score
from mage_ptcg.continuous_league.catalog import CatalogEntry, CatalogSnapshot
from mage_ptcg.continuous_league.contracts import LeagueContractError, content_id
from mage_ptcg.continuous_league.controller import ContinuousLeagueController
from mage_ptcg.continuous_league.evaluation import (
    EvaluationJob,
    compare_evaluations,
    run_evaluation,
)
from mage_ptcg.continuous_league.report import consume_sealed_holdout
from mage_ptcg.continuous_league.role_ledger import extend_role_ledger
from mage_ptcg.continuous_league.scheduler import DurableScheduler, ResourceRequest


def _hash(value: str) -> str:
    return content_id("test", value)


def _entry(
    asset: str,
    *,
    role: str,
    policy: str | None = None,
    deck: str | None = None,
    source: str | None = None,
    archetype: str = "A",
    policy_hash: str | None = None,
    deck_hash: str | None = None,
) -> CatalogEntry:
    return CatalogEntry(
        asset_id=asset,
        policy_id=policy or f"policy-{asset}",
        deck_id=deck or f"deck-{asset}",
        source_id=source or f"source-{asset}",
        policy_kind="rule_v0",
        runtime_path="builtin:rule_v0",
        deck_path="deck.csv",
        policy_hash=policy_hash or _hash(f"policy-{asset}"),
        deck_hash=deck_hash or _hash(f"deck-{asset}"),
        source_hash=_hash(f"source-{asset}"),
        runtime_config_hash=_hash("rule-v0"),
        role=role,
        archetype_id=archetype,
    )


def test_catalog_role_ledger_exposure_and_schedule_are_stable() -> None:
    training = _entry("training", role="TRAINING_ACTIVE")
    exact = _entry(
        "exact",
        role="BENCHMARK_VISIBLE",
        policy=training.policy_id,
        deck=training.deck_id,
        source=training.source_id,
        policy_hash=training.policy_hash,
        deck_hash=training.deck_hash,
    )
    same_deck = _entry(
        "same-deck",
        role="BENCHMARK_VISIBLE",
        deck=training.deck_id,
        archetype="B",
        deck_hash=training.deck_hash,
    )
    same_policy = _entry(
        "same-policy",
        role="BENCHMARK_VISIBLE",
        policy=training.policy_id,
        archetype="C",
        policy_hash=training.policy_hash,
    )
    same_archetype = _entry(
        "same-archetype", role="BENCHMARK_VISIBLE", archetype="A"
    )
    untouched = _entry(
        "untouched", role="BENCHMARK_VISIBLE", archetype="UNSEEN"
    )
    catalog = CatalogSnapshot.build(
        [training, same_deck, same_policy, same_archetype, untouched]
    )
    exposure = ExposureSnapshot.build(
        replay_dataset_version_id=_hash("replay"),
        population_epoch_id=_hash("population"),
        entries=[training],
    )
    assert exposure.classify(training) == ExposureCohort.EXACT_KNOWN
    assert exposure.classify(same_deck) == ExposureCohort.KNOWN_DECK_NOVEL_POLICY
    assert exposure.classify(same_policy) == ExposureCohort.NOVEL_DECK_KNOWN_POLICY
    assert (
        exposure.classify(same_archetype)
        == ExposureCohort.NOVEL_DECK_KNOWN_ARCHETYPE
    )
    assert exposure.classify(untouched) == ExposureCohort.FULLY_UNTOUCHED
    assert ExposureSnapshot.from_dict(exposure.to_dict()) == exposure

    benchmark = BenchmarkManifest.build(
        name="fixed",
        catalog=catalog,
        subject_decks=[
            SubjectDeck("subject", "deck.csv", _hash("subject-deck"))
        ],
        opponent_instance_ids=[same_deck.opponent_instance_id],
        repetitions=2,
        base_seed=17,
    )
    runtime_id = _hash("runtime")
    schedule = build_schedule(benchmark, runtime_id)
    assert len(schedule) == 4
    assert len({game.game_key for game in schedule}) == 4
    assert [game.game_key for game in schedule] == [
        game.game_key for game in build_schedule(benchmark, runtime_id)
    ]
    other_runtime_schedule = build_schedule(benchmark, _hash("other-runtime"))
    assert [game.env_seed for game in schedule] == [
        game.env_seed for game in other_runtime_schedule
    ]
    assert [game.game_key for game in schedule] != [
        game.game_key for game in other_runtime_schedule
    ]

    ledger = extend_role_ledger(
        [training, same_deck],
        role_counts={"TRAINING_ACTIVE": 1, "BENCHMARK_VISIBLE": 1},
        seed=3,
    )
    extended = extend_role_ledger(
        [training, same_deck, untouched],
        prior=ledger,
        role_counts={"TRAINING_RESERVE": 1},
        seed=999,
    )
    old_roles = {
        asset: assignment.role
        for assignment in ledger.assignments
        for asset in assignment.asset_ids
    }
    new_roles = {
        asset: assignment.role
        for assignment in extended.assignments
        for asset in assignment.asset_ids
    }
    assert all(new_roles[key] == value for key, value in old_roles.items())
    assert new_roles["untouched"] == "TRAINING_RESERVE"


def test_evaluation_resumes_and_compares_by_block(tmp_path: Path) -> None:
    training = _entry("training", role="TRAINING_ACTIVE")
    opponent = _entry("opponent", role="BENCHMARK_VISIBLE")
    catalog = CatalogSnapshot.build([training, opponent])
    exposure = ExposureSnapshot.build(
        replay_dataset_version_id=_hash("replay"),
        population_epoch_id=_hash("population"),
        entries=[training],
    )
    benchmark = BenchmarkManifest.build(
        name="resume",
        catalog=catalog,
        subject_decks=[SubjectDeck("subject", "deck.csv", _hash("deck"))],
        opponent_instance_ids=[opponent.opponent_instance_id],
        repetitions=2,
        base_seed=11,
    )
    job = EvaluationJob.build(benchmark, _hash("candidate"), exposure)

    def winner(game, _entry):
        return {"outcome": "win" if game.seat == "subject_first" else "loss"}

    partial = run_evaluation(
        job=job,
        benchmark=benchmark,
        catalog=catalog,
        exposure=exposure,
        output_dir=tmp_path,
        run_game=winner,
        max_games=2,
    )
    assert not partial["is_schedule_complete"]
    complete = run_evaluation(
        job=job,
        benchmark=benchmark,
        catalog=catalog,
        exposure=exposure,
        output_dir=tmp_path,
        run_game=winner,
    )
    assert complete["is_schedule_complete"]
    assert complete["aggregate"]["game_weighted"]["score_rate"] == 0.5
    records = [
        json.loads(line)
        for line in (tmp_path / "games.jsonl").read_text().splitlines()
    ]
    baseline = [
        {**record, "outcome": "loss" if record["outcome"] == "win" else "loss"}
        for record in records
    ]
    comparison = compare_evaluations(records, baseline, bootstrap_samples=100)
    assert comparison["delta_score_rate"] == 0.5
    assert comparison["block_bootstrap_95"] is not None


def test_evaluation_uses_one_tty_progress_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mage_ptcg.continuous_league.evaluation as evaluation_module

    training = _entry("training", role="TRAINING_ACTIVE")
    opponent = _entry("opponent", role="BENCHMARK_VISIBLE")
    catalog = CatalogSnapshot.build([training, opponent])
    exposure = ExposureSnapshot.build(
        replay_dataset_version_id=_hash("replay"),
        population_epoch_id=_hash("population"),
        entries=[training],
    )
    benchmark = BenchmarkManifest.build(
        name="tty-progress",
        catalog=catalog,
        subject_decks=[SubjectDeck("subject", "deck.csv", _hash("deck"))],
        opponent_instance_ids=[opponent.opponent_instance_id],
        repetitions=1,
        base_seed=11,
    )
    job = EvaluationJob.build(benchmark, _hash("candidate"), exposure)
    bars = []

    class FakeBar:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.updated = 0
            self.closed = False

        def update(self, value: int) -> None:
            self.updated += value

        def set_postfix(self, **_kwargs) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    def make_bar(**kwargs):
        bar = FakeBar(**kwargs)
        bars.append(bar)
        return bar

    monkeypatch.setattr(
        evaluation_module,
        "sys",
        SimpleNamespace(stderr=SimpleNamespace(isatty=lambda: True)),
        raising=False,
    )
    monkeypatch.setitem(sys.modules, "tqdm", SimpleNamespace(tqdm=make_bar))

    result = run_evaluation(
        job=job,
        benchmark=benchmark,
        catalog=catalog,
        exposure=exposure,
        output_dir=tmp_path,
        run_game=lambda _game, _entry: {"outcome": "win"},
    )

    assert result["completed_games"] == 2
    assert len(bars) == 1
    assert bars[0].kwargs["total"] == 2
    assert bars[0].updated == 2
    assert bars[0].closed


def test_scheduler_controller_calibration_and_sealed_marker(tmp_path: Path) -> None:
    scheduler = DurableScheduler(
        tmp_path / "queue.json", cpu_slots=1, max_pending_evaluations=4
    )
    first = scheduler.enqueue(
        "VISIBLE_EVALUATION", {"runtime": "one"}, resources=ResourceRequest(1)
    )
    assert scheduler.enqueue("VISIBLE_EVALUATION", {"runtime": "one"}) == first
    assert scheduler.next_runnable() == first

    event_dir = tmp_path / "events"
    event_dir.mkdir()
    event = {
        "training_checkpoint_id": _hash("checkpoint"),
        "runtime_policy_id": _hash("runtime"),
    }
    (event_dir / "event.json").write_text(json.dumps(event), encoding="utf-8")
    controller = ContinuousLeagueController(
        root=tmp_path / "controller",
        checkpoint_event_dir=event_dir,
        visible_benchmark_id=_hash("benchmark"),
        exposure_snapshot_id=_hash("exposure"),
        handlers={"VISIBLE_EVALUATION": lambda item: {"task_id": item.task_id}},
    )
    result = controller.run_once()
    assert result["status"] == "COMPLETE"
    assert controller.run_once()["status"] == "IDLE"

    observations = [
        {
            "runtime_policy_id": _hash(f"runtime-{index}"),
            "benchmark_id": _hash("benchmark"),
            "evaluation_result_id": _hash(f"evaluation-{index}"),
            "offline_score_rate": index / 40,
            "public_score": 1000 + index * 10,
            "submission_reference": f"submission-{index}",
            "observation_id": _hash(f"observation-{index}"),
        }
        for index in range(30)
    ]
    calibration = fit_calibration(
        observations, output_path=tmp_path / "calibration.json"
    )
    assert calibration["status"] == "AVAILABLE"
    assert forecast_public_score(calibration, 0.5)["predicted_public_score"] > 1000
    unavailable = fit_calibration(
        observations[:2],
        output_path=tmp_path / "unavailable.json",
    )
    assert unavailable["status"] == "OBSERVATION_ONLY"

    evaluation = {
        "benchmark_id": _hash("sealed-benchmark"),
        "runtime_policy_id": _hash("sealed-runtime"),
        "evaluation_result_id": _hash("sealed-result"),
    }
    marker = consume_sealed_holdout(
        marker_root=tmp_path / "sealed",
        holdout_id="holdout-a",
        runtime_policy_id=evaluation["runtime_policy_id"],
        benchmark_id=evaluation["benchmark_id"],
        evaluation_result=evaluation,
    )
    assert marker["consumed"]
    with pytest.raises(LeagueContractError, match="already consumed"):
        consume_sealed_holdout(
            marker_root=tmp_path / "sealed",
            holdout_id="holdout-a",
            runtime_policy_id=evaluation["runtime_policy_id"],
            benchmark_id=evaluation["benchmark_id"],
            evaluation_result=evaluation,
        )


def test_scheduler_explicitly_recovers_interrupted_running_task(tmp_path: Path) -> None:
    state_path = tmp_path / "queue.json"
    scheduler = DurableScheduler(state_path)
    task = scheduler.enqueue("VISIBLE_EVALUATION", {"runtime": "one"})
    scheduler.transition(task.task_id, "RUNNING")

    restarted = DurableScheduler(state_path)

    assert restarted.recover_interrupted() == 1
    recovered = restarted.items[task.task_id]
    assert recovered.state == "PENDING"
    assert recovered.attempts == 1
    assert restarted.next_runnable() == recovered


def test_checkpoint_benchmark_terminal_summary_formats_scores_and_queue(
    tmp_path: Path,
) -> None:
    import mage_ptcg.continuous_league.controller as controller_module

    formatter = getattr(
        controller_module, "format_checkpoint_benchmark_terminal_summary", None
    )
    assert formatter is not None
    history_root = tmp_path / "history"
    history_root.mkdir()
    (history_root / "evaluation_summary.json").write_text(
        json.dumps(
            {
                "history": [
                    {
                        "training_step": 10_000,
                        "aggregate_status": "COMPLETE",
                        "fault_count": 0,
                        "is_schedule_complete": True,
                        "game_weighted_score_rate": 0.478515625,
                        "game_weighted_wilson_95": [0.435567, 0.521784],
                        "worst_opponent_score_rate": 0.1953125,
                        "score_delta_from_previous_complete": -0.01171875,
                    },
                    {
                        "training_step": 20_000,
                        "aggregate_status": "FAULTED",
                        "fault_count": 3,
                        "is_schedule_complete": True,
                        "game_weighted_score_rate": 0.9,
                        "game_weighted_wilson_95": [0.8, 1.0],
                        "worst_opponent_score_rate": 0.8,
                        "score_delta_from_previous_complete": None,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    scheduler = DurableScheduler(tmp_path / "queue.json")
    active = scheduler.enqueue("VISIBLE_EVALUATION", {"runtime": "active"})
    scheduler.transition(active.task_id, "RUNNING")
    scheduler.enqueue("VISIBLE_EVALUATION", {"runtime": "waiting"})

    rendered = formatter(history_root=history_root, scheduler=scheduler)

    assert "step | score | 95% CI | worst | fault | delta" in rendered
    assert "10,000 | 47.85% | 43.56–52.18% | 19.53% | 0 | -1.17pp" in rendered
    assert "20,000 | -- | -- | -- | 3 | --" in rendered
    assert "queue: complete=0 running=1 pending=1 failed=0" in rendered


def test_controller_reports_before_and_after_a_task(tmp_path: Path) -> None:
    event_dir = tmp_path / "events"
    event_dir.mkdir()
    (event_dir / "event.json").write_text(
        json.dumps(
            {
                "training_checkpoint_id": _hash("checkpoint"),
                "runtime_policy_id": _hash("runtime"),
                "training_step": 10_000,
            }
        ),
        encoding="utf-8",
    )
    calls = []
    controller = ContinuousLeagueController(
        root=tmp_path / "controller",
        checkpoint_event_dir=event_dir,
        visible_benchmark_id=_hash("benchmark"),
        exposure_snapshot_id=_hash("exposure"),
        handlers={"VISIBLE_EVALUATION": lambda item: {"task_id": item.task_id}},
    )
    controller.status_reporter = lambda scheduler: calls.append(
        scheduler.items.copy()
    )

    result = controller.run_once()

    assert result["status"] == "COMPLETE"
    assert len(calls) == 2
    assert list(calls[0].values())[0].state == "RUNNING"
    assert list(calls[1].values())[0].state == "COMPLETE"


def test_checkpoint_benchmark_terminal_renderer_skips_non_tty(tmp_path: Path) -> None:
    import mage_ptcg.continuous_league.controller as controller_module

    class NonInteractiveStream:
        def __init__(self) -> None:
            self.output = ""

        def isatty(self) -> bool:
            return False

        def write(self, value: str) -> None:
            self.output += value

        def flush(self) -> None:
            return None

    stream = NonInteractiveStream()
    scheduler = DurableScheduler(tmp_path / "queue.json")
    controller_module.render_checkpoint_benchmark_terminal_summary(
        history_root=tmp_path / "history", scheduler=scheduler, stream=stream
    )

    assert stream.output == ""
def test_controller_enqueues_every_checkpoint_without_superseding(
    tmp_path: Path,
) -> None:
    event_dir = tmp_path / "events"
    event_dir.mkdir()
    for index in range(5):
        event = {
            "training_checkpoint_id": _hash(f"checkpoint-{index}"),
            "runtime_policy_id": _hash(f"runtime-{index}"),
            "training_step": (index + 1) * 10_000,
        }
        (event_dir / f"{5 - index}.json").write_text(
            json.dumps(event), encoding="utf-8"
        )

    controller = ContinuousLeagueController(
        root=tmp_path / "controller",
        checkpoint_event_dir=event_dir,
        visible_benchmark_id=_hash("benchmark"),
        exposure_snapshot_id=_hash("exposure"),
        handlers={},
    )

    assert controller.discover_checkpoints() == 5
    scheduled = list(controller.scheduler.items.values())
    assert len(scheduled) == 5
    assert {item.state for item in scheduled} == {"PENDING"}
    assert [item.payload["training_step"] for item in scheduled] == [
        10_000,
        20_000,
        30_000,
        40_000,
        50_000,
    ]
