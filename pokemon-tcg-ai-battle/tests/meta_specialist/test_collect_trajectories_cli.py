"""Tests for the human-runnable ``collect-trajectories`` CLI and its planner.

Fast tests never run a real CABT game: they inject a fake ``_worker_target``
into ``ActorPoolV1`` (the same seam ``tests/meta_specialist/test_actor_pool_v1
.py`` already uses for its own fast resume test) so the real spawn/timeout/
resume machinery still runs for real, but each "game" is a trivial, schema
-valid record written in milliseconds -- never a fabricated result read as if
it were a genuine playout.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from collections import Counter

import pytest

from mage_ptcg.meta_specialist import cli
from mage_ptcg.meta_specialist.actor_pool_v1 import (
    ActorGameCollectionResultV1 as _Result,
)
from mage_ptcg.meta_specialist.actor_pool_v1 import (
    ActorJobConfigV1 as _Job,
)
from mage_ptcg.meta_specialist.actor_pool_v1 import (
    ActorPoolV1,
    ActorPoolJobOutcomeV1,
    ActorPoolV1Error,
    CanonicalGameIdentityV1,
    _install_worker_isolation_v1,
    _reconstruct_prefix_steps_v1,
    build_actor_pool_game_record_v1,
    rule_agent_behavior_identity_v1,
    worker_cuda_diagnostics_v1,
    write_actor_pool_game_record_v1,
)
from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    extract_specialist_model_input_v1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2
from mage_ptcg.meta_specialist.collect_trajectories_v1 import (
    CollectTrajectoriesError,
    build_collection_plan_v1,
    _resolve_opponent_rotation_v1,
    load_qualified_lanes_v1,
    resolve_requested_lanes_v1,
    run_collect_trajectories_v1,
    _summarize_v1,
)
from mage_ptcg.meta_specialist.seed_qualification_report_v1 import (
    atomic_write_seed_qualification_report_v1,
    build_seed_qualification_report_v1,
)
from mage_ptcg.meta_specialist.trajectory_v1 import build_actor_trajectory_transition_v1

_FIXTURE_COMMIT = "e" * 40
_EXPECTED_CANDIDATE_COUNT = 15


# ---------------------------------------------------------------------------
# Seed-qualification-report fixture builder.
# ---------------------------------------------------------------------------


def _deck_identity_for(seed: str) -> str:
    return "deck-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def _write_seed_qualification_fixture_v1(
    tmp_path: Path, *, qualified: tuple[str, ...], not_run: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    """Publish a real, schema-valid seed qualification report + materialized decks.

    Only ``qualified`` archetypes get a materialized deck CSV on disk (its
    bytes are never read by the fake worker target; only its existence
    matters to ``load_qualified_lanes_v1``'s fail-closed check).
    """
    materialized_dir = tmp_path / "materialized"
    materialized_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[dict[str, object]] = []
    for archetype_id in qualified:
        deck_identity = _deck_identity_for(archetype_id)
        deck_path = materialized_dir / f"{archetype_id}-p1-{deck_identity}.csv"
        deck_path.write_text("fixture deck bytes -- never read by the fake worker\n", encoding="utf-8")
        candidates.append({
            "runtime_id": archetype_id, "priority": 1, "deck_identity": deck_identity,
            "asset_class": "materialized_deck_csv_deduplicated_by_canonical_multiset",
            "materialization_status": "materialized_git_blob",
            "outcome": "qualified", "reason": None,
            "cabt_probe_status": "DONE", "cabt_probe_evidence": "fixture-probe-evidence",
            "qualified_asset_id": f"seed-{archetype_id}-p1-{deck_identity}",
        })
    for archetype_id in not_run:
        candidates.append({
            "runtime_id": archetype_id, "priority": 1, "deck_identity": _deck_identity_for(archetype_id),
            "asset_class": "materialized_deck_csv_deduplicated_by_canonical_multiset",
            "materialization_status": "materialized_git_blob",
            "outcome": "not_run", "reason": "fixture: registered but not yet run",
            "cabt_probe_status": None, "cabt_probe_evidence": None, "qualified_asset_id": None,
        })
    pad_index = 0
    while len(candidates) < _EXPECTED_CANDIDATE_COUNT:
        archetype_id = f"fixture-pad-archetype-{pad_index}"
        candidates.append({
            "runtime_id": archetype_id, "priority": 1, "deck_identity": _deck_identity_for(archetype_id),
            "asset_class": "materialized_deck_csv_deduplicated_by_canonical_multiset",
            "materialization_status": "materialized_git_blob",
            "outcome": "not_run", "reason": "fixture padding to the fixed 15-lane shape",
            "cabt_probe_status": None, "cabt_probe_evidence": None, "qualified_asset_id": None,
        })
        pad_index += 1
    assert len(candidates) == _EXPECTED_CANDIDATE_COUNT

    fixture_hash = hashlib.sha256(b"fixture").hexdigest()
    report = build_seed_qualification_report_v1(
        registry_content_sha256=fixture_hash, card_database_sha256=fixture_hash,
        card_vocabulary_sha256=fixture_hash, archetype_registry_schema_version="fixture-registry-v1",
        cabt_probe_seed=20260803, cabt_probe_max_steps=2000,
        generated_time_utc="2026-08-03T00:00:00Z", candidates=candidates,
    )
    report_path = tmp_path / "seed_qualification_report_v1.json"
    atomic_write_seed_qualification_report_v1(report_path, report)
    return report_path, materialized_dir


# ---------------------------------------------------------------------------
# Fake, fast ActorPoolV1 worker target (writes a genuinely schema-valid
# record.json in milliseconds; mirrors
# tests/meta_specialist/test_actor_pool_v1.py's own _fake_success_worker_target_v1).
# ---------------------------------------------------------------------------


def _fake_success_worker_target_v1(job_payload, stdout_path, stderr_path, output_root) -> None:
    _install_worker_isolation_v1(Path(stdout_path), Path(stderr_path))
    job = _Job.from_payload(job_payload)
    games_dir = Path(output_root) / "games" / job.job_id
    games_dir.mkdir(parents=True, exist_ok=True)
    counter_path = games_dir / "call_count.txt"
    count = (int(counter_path.read_text()) if counter_path.exists() else 0) + 1
    counter_path.write_text(str(count), encoding="utf-8")

    observation = {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [
                {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
                 "confused": False, "deckCount": 60, "discard": [], "hand": [], "handCount": 0,
                 "paralyzed": False, "poisoned": False, "prize": [None] * 6},
                {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
                 "confused": False, "deckCount": 60, "discard": [], "hand": None, "handCount": 0,
                 "paralyzed": False, "poisoned": False, "prize": [None] * 6},
            ],
            "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 1, "turnActionCount": 0, "yourIndex": 0,
        },
        "select": {"context": 1, "contextCard": None, "deck": None, "effect": None,
                   "maxCount": 0, "minCount": 0, "option": [],
                   "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1},
        "step": 1,
    }
    state = build_actor_visible_decision_state_v2(observation)
    extracted = extract_specialist_model_input_v1(state, make_test_card_vocabulary_v1(range(1, 1000)))
    steps = _reconstruct_prefix_steps_v1(recorded=(), final_semantic_selection=(), order_semantics="unordered_set")
    transition = build_actor_trajectory_transition_v1(
        model_input=extracted.model_input, order_semantics="unordered_set", prefix_steps=steps,
        value=0.0, reward=1.0, discount=0.0, terminal=True,
        subject_behavior_version=job.behavior_identity, opponent_instance_id="cabt-rule-agent-seed-fixture",
        opponent_version="c" * 64, pool_epoch=job.pool_epoch, policy_lag=job.policy_lag,
    )
    result = _Result(
        status="completed", job_id=job.job_id, transitions=(transition,), fault=None,
        winner=0, outcome="win", steps=1, elapsed_seconds=0.001,
        engine_entry_point="fixture", engine_source_sha256="0" * 64,
        opponent_version="c" * 64, deck_identity="deck-" + "0" * 20,
    )
    payload = build_actor_pool_game_record_v1(
        job=job, result=result, worker_diagnostics=worker_cuda_diagnostics_v1(),
        persistent_worker=False, started_at_utc="2026-08-03T00:00:00Z", finished_at_utc="2026-08-03T00:00:01Z",
    )
    write_actor_pool_game_record_v1(games_dir, payload)


def _fake_pool_factory_v1() -> ActorPoolV1:
    return ActorPoolV1(num_workers=2, _worker_target=_fake_success_worker_target_v1)


# ---------------------------------------------------------------------------
# Pure planning functions.
# ---------------------------------------------------------------------------


def test_resolve_requested_lanes_all_returns_sorted_qualified_archetypes(tmp_path: Path) -> None:
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(
        tmp_path, qualified=("zeta_archetype", "alpha_archetype"),
    )
    qualified = load_qualified_lanes_v1(report_path=report_path, materialized_dir=materialized_dir)

    assert resolve_requested_lanes_v1("all", qualified) == ("alpha_archetype", "zeta_archetype")


def test_resolve_requested_lanes_accepts_explicit_comma_separated_list(tmp_path: Path) -> None:
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(
        tmp_path, qualified=("alakazam", "rocket_mewtwo_spidops"),
    )
    qualified = load_qualified_lanes_v1(report_path=report_path, materialized_dir=materialized_dir)

    assert resolve_requested_lanes_v1("rocket_mewtwo_spidops,alakazam", qualified) == (
        "rocket_mewtwo_spidops", "alakazam",
    )


def test_resolve_requested_lanes_refuses_a_registered_but_unqualified_archetype(tmp_path: Path) -> None:
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(
        tmp_path, qualified=("alakazam",), not_run=("crustle_mega_kangaskhan",),
    )
    qualified = load_qualified_lanes_v1(report_path=report_path, materialized_dir=materialized_dir)

    with pytest.raises(CollectTrajectoriesError, match="crustle_mega_kangaskhan"):
        resolve_requested_lanes_v1("crustle_mega_kangaskhan", qualified)


def test_resolve_requested_lanes_refuses_an_unknown_archetype(tmp_path: Path) -> None:
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(tmp_path, qualified=("alakazam",))
    qualified = load_qualified_lanes_v1(report_path=report_path, materialized_dir=materialized_dir)

    with pytest.raises(CollectTrajectoriesError, match="not a qualified deck"):
        resolve_requested_lanes_v1("not_a_real_archetype", qualified)


def test_load_qualified_lanes_fails_closed_when_materialized_deck_is_missing(tmp_path: Path) -> None:
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(tmp_path, qualified=("alakazam",))
    for deck_file in materialized_dir.glob("alakazam-*.csv"):
        deck_file.unlink()

    with pytest.raises(CollectTrajectoriesError, match="missing on disk"):
        load_qualified_lanes_v1(report_path=report_path, materialized_dir=materialized_dir)


def test_build_collection_plan_balances_seats_evenly(tmp_path: Path) -> None:
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(tmp_path, qualified=("alakazam",))
    lanes = list(load_qualified_lanes_v1(report_path=report_path, materialized_dir=materialized_dir).values())
    identity = rule_agent_behavior_identity_v1()

    jobs = build_collection_plan_v1(
        lanes=lanes, num_games=4, base_seed=1000, source_commit=_FIXTURE_COMMIT,
        behavior_kind="rule_agent", behavior_identity=identity, opponent_kind="cabt_rule_agent_v0",
        decoding_mode="greedy", sampling_seed=0, pool_epoch=0, policy_lag=0, non_terminal_discount=1.0,
        max_steps=100, timeout_seconds=30.0, neural_checkpoint_path="",
    )

    assert len(jobs) == 4
    seats = [job.seat for job in jobs]
    assert seats.count(0) == 2 and seats.count(1) == 2
    assert [job.env_seed for job in jobs] == [1000, 1001, 1002, 1003]
    assert len({job.job_id for job in jobs}) == 4  # every job_id distinct


def test_build_collection_plan_odd_count_is_off_by_at_most_one(tmp_path: Path) -> None:
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(tmp_path, qualified=("alakazam",))
    lanes = list(load_qualified_lanes_v1(report_path=report_path, materialized_dir=materialized_dir).values())
    identity = rule_agent_behavior_identity_v1()

    jobs = build_collection_plan_v1(
        lanes=lanes, num_games=5, base_seed=0, source_commit=_FIXTURE_COMMIT,
        behavior_kind="rule_agent", behavior_identity=identity, opponent_kind="cabt_rule_agent_v0",
        decoding_mode="greedy", sampling_seed=0, pool_epoch=0, policy_lag=0, non_terminal_discount=1.0,
        max_steps=100, timeout_seconds=30.0, neural_checkpoint_path="",
    )

    seats = [job.seat for job in jobs]
    assert abs(seats.count(0) - seats.count(1)) <= 1


def test_build_collection_plan_is_deterministic_across_calls(tmp_path: Path) -> None:
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(tmp_path, qualified=("alakazam",))
    lanes = list(load_qualified_lanes_v1(report_path=report_path, materialized_dir=materialized_dir).values())
    identity = rule_agent_behavior_identity_v1()
    kwargs = dict(
        lanes=lanes, num_games=3, base_seed=42, source_commit=_FIXTURE_COMMIT,
        behavior_kind="rule_agent", behavior_identity=identity, opponent_kind="cabt_rule_agent_v0",
        decoding_mode="greedy", sampling_seed=0, pool_epoch=0, policy_lag=0, non_terminal_discount=1.0,
        max_steps=100, timeout_seconds=30.0, neural_checkpoint_path="",
    )

    first = [job.job_id for job in build_collection_plan_v1(**kwargs)]
    second = [job.job_id for job in build_collection_plan_v1(**kwargs)]

    assert first == second


# ---------------------------------------------------------------------------
# End-to-end (fake worker): resume, seat balance achieved, summary shape.
# ---------------------------------------------------------------------------

_EXPECTED_SUMMARY_KEYS = frozenset({
    "schema_version", "run_name", "started_at_utc", "finished_at_utc", "wall_time_seconds",
    "behavior_kind", "behavior_identity", "opponent_kind", "decoding_mode", "sampling_seed",
    "source_commit", "lanes", "num_games_requested", "games_attempted", "output_root", "games_dir",
    "run_summary_path", "progress_summary_path", "per_lane", "faulted_jobs", "faulted_jobs_truncated",
    "final_attempts",
    "games_completed", "games_resumed_skipped", "games_faulted", "games_timeout", "transitions_collected",
    "existing_games_outside_this_plan",
})


def _run_fixture_collection_v1(tmp_path: Path, *, run_name: str = "fixture-run") -> dict[str, object]:
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(tmp_path, qualified=("alakazam",))
    return run_collect_trajectories_v1(
        lanes_arg="alakazam", num_games=4, base_seed=500, workers=2, run_name=run_name,
        behavior_kind="rule_agent", timeout_seconds=30.0, max_steps=100,
        source_commit=_FIXTURE_COMMIT,
        seed_qualification_report_path=report_path, materialized_deck_dir=materialized_dir,
        output_base_dir=tmp_path / "actor-pool-output",
        pool_factory=_fake_pool_factory_v1,
    )


def test_run_collect_trajectories_summary_shape_is_stable(tmp_path: Path) -> None:
    payload = _run_fixture_collection_v1(tmp_path)

    assert set(payload) == _EXPECTED_SUMMARY_KEYS
    assert payload["schema_version"] == "meta-specialist-collect-trajectories-run-summary-v1"


def test_run_collect_trajectories_collects_four_games_seat_balanced(tmp_path: Path) -> None:
    payload = _run_fixture_collection_v1(tmp_path)

    assert payload["games_attempted"] == 4
    assert payload["games_completed"] == 4
    assert payload["games_resumed_skipped"] == 0
    assert payload["games_faulted"] == 0
    assert payload["games_timeout"] == 0
    assert payload["transitions_collected"] == 4  # one transition per fixture game

    lane = payload["per_lane"]["alakazam"]
    assert lane["seats"]["0"]["collected"] == 2
    assert lane["seats"]["1"]["collected"] == 2

    run_summary_path = Path(payload["run_summary_path"])
    assert run_summary_path.is_file()
    assert json.loads(run_summary_path.read_text(encoding="utf-8")) == payload


def test_run_collect_trajectories_resume_skips_completed_games_and_reports_the_count(tmp_path: Path) -> None:
    first = _run_fixture_collection_v1(tmp_path, run_name="resume-fixture")
    assert first["games_completed"] == 4
    assert first["games_resumed_skipped"] == 0

    call_counters = list(Path(first["games_dir"]).glob("*/call_count.txt"))
    assert len(call_counters) == 4
    assert all(path.read_text().strip() == "1" for path in call_counters)

    second = _run_fixture_collection_v1(tmp_path, run_name="resume-fixture")

    assert second["games_attempted"] == 4
    assert second["games_completed"] == 0
    assert second["games_resumed_skipped"] == 4
    assert second["games_faulted"] == 0
    assert second["transitions_collected"] == 4
    # The fake worker target must never have run a second time for any job.
    assert all(path.read_text().strip() == "1" for path in call_counters)


def test_summary_uses_final_retry_attempt_identity_for_success_and_exhaustion(tmp_path: Path) -> None:
    """The plan's retry-0 job is not provenance for a retry-1 final outcome."""
    deck_path = tmp_path / "deck.csv"
    deck_path.write_text("fixture\n", encoding="utf-8")
    behavior_identity = rule_agent_behavior_identity_v1()
    jobs = [
        _Job(
            job_id="successful-retry", archetype_id="lane", deck_csv_path=str(deck_path),
            source_commit=_FIXTURE_COMMIT, env_seed=10, seat=0, behavior_kind="rule_agent",
            behavior_identity=behavior_identity, opponent_kind="cabt_rule_agent_v0",
        ),
        _Job(
            job_id="exhausted-retry", archetype_id="lane", deck_csv_path=str(deck_path),
            source_commit=_FIXTURE_COMMIT, env_seed=11, seat=1, behavior_kind="rule_agent",
            behavior_identity=behavior_identity, opponent_kind="cabt_rule_agent_v0",
        ),
    ]
    identities = [
        CanonicalGameIdentityV1(
            opponent_id="opponent", opponent_policy_version="a" * 64,
            opponent_deck_fingerprint="b" * 64, seat=job.seat,
            environment_seed=job.env_seed, agent_sampling_seed=job.sampling_seed,
            retry_index=1,
        )
        for job in jobs
    ]
    outcomes = [
        ActorPoolJobOutcomeV1(
            job_id=jobs[0].job_id, status="completed", record_path="/fixture/success.json",
            transitions_count=3, fault_reason=None, wall_time_seconds=0.1,
            game_identity=identities[0].to_dict(), retry_index=1,
        ),
        ActorPoolJobOutcomeV1(
            job_id=jobs[1].job_id, status="faulted", record_path=None,
            transitions_count=0, fault_reason="second attempt failed", wall_time_seconds=0.2,
            game_identity=identities[1].to_dict(), retry_index=1,
        ),
    ]

    summary = _summarize_v1(jobs=jobs, outcomes=outcomes)

    final_attempts = {row["job_id"]: row for row in summary["final_attempts"]}
    assert final_attempts["successful-retry"] == {
        "job_id": "successful-retry", "status": "completed", "retry_index": 1,
        "game_identity": identities[0].to_dict(),
    }
    assert final_attempts["exhausted-retry"] == {
        "job_id": "exhausted-retry", "status": "faulted", "retry_index": 1,
        "game_identity": identities[1].to_dict(),
    }
    assert summary["faulted_jobs"] == [{
        "job_id": "exhausted-retry", "archetype_id": "lane", "seat": 1,
        "env_seed": 11, "retry_index": 1, "game_identity": identities[1].to_dict(),
        "status": "faulted", "fault_reason": "second attempt failed",
    }]


def test_run_collect_trajectories_refuses_an_unqualified_lane(tmp_path: Path) -> None:
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(
        tmp_path, qualified=("alakazam",), not_run=("crustle_mega_kangaskhan",),
    )

    with pytest.raises(CollectTrajectoriesError, match="crustle_mega_kangaskhan"):
        run_collect_trajectories_v1(
            lanes_arg="crustle_mega_kangaskhan", num_games=2, base_seed=1, workers=1, run_name="refused-run",
            behavior_kind="rule_agent", source_commit=_FIXTURE_COMMIT,
            seed_qualification_report_path=report_path, materialized_deck_dir=materialized_dir,
            output_base_dir=tmp_path / "actor-pool-output", pool_factory=_fake_pool_factory_v1,
        )
    assert not (tmp_path / "actor-pool-output" / "refused-run").exists()


def test_run_collect_trajectories_refuses_an_unsafe_run_name(tmp_path: Path) -> None:
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(tmp_path, qualified=("alakazam",))

    with pytest.raises(CollectTrajectoriesError, match="run-name"):
        run_collect_trajectories_v1(
            lanes_arg="alakazam", num_games=2, base_seed=1, workers=1, run_name="../escape",
            behavior_kind="rule_agent", source_commit=_FIXTURE_COMMIT,
            seed_qualification_report_path=report_path, materialized_deck_dir=materialized_dir,
            output_base_dir=tmp_path / "actor-pool-output", pool_factory=_fake_pool_factory_v1,
        )


def test_run_collect_trajectories_neural_behavior_requires_an_existing_checkpoint(tmp_path: Path) -> None:
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(tmp_path, qualified=("alakazam",))

    with pytest.raises(ActorPoolV1Error, match="neural checkpoint is missing"):
        run_collect_trajectories_v1(
            lanes_arg="alakazam", num_games=1, base_seed=1, workers=1, run_name="neural-missing-ckpt",
            behavior_kind="neural_specialist", neural_checkpoint_path=str(tmp_path / "does-not-exist.pt"),
            source_commit=_FIXTURE_COMMIT,
            seed_qualification_report_path=report_path, materialized_deck_dir=materialized_dir,
            output_base_dir=tmp_path / "actor-pool-output", pool_factory=_fake_pool_factory_v1,
        )


# ---------------------------------------------------------------------------
# argparse-level CLI wiring (never executes a collection: parse errors and a
# monkeypatched runner only, so these stay hermetic and fast).
# ---------------------------------------------------------------------------


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    exit_code = cli.main(argv)
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def test_cli_missing_required_arguments_is_argument_error(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code, out, err = _run(["collect-trajectories", "--lanes", "all"], capsys)

    assert exit_code == 2
    assert out == ""
    assert json.loads(err)["error_type"] == "ARGUMENT_ERROR"


def test_cli_invalid_behavior_kind_choice_is_argument_error(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code, out, err = _run(
        [
            "collect-trajectories", "--num-games", "1", "--base-seed", "0", "--run-name", "x",
            "--behavior-kind", "not-a-real-kind",
        ],
        capsys,
    )

    assert exit_code == 2
    assert out == ""
    assert json.loads(err)["error_type"] == "ARGUMENT_ERROR"


def test_cli_forwards_parsed_arguments_to_the_collection_runner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    recorded: dict[str, object] = {}

    def _fake_runner(**kwargs: object) -> dict[str, object]:
        recorded.update(kwargs)
        return {"schema_version": "fixture", "games_completed": 0}

    monkeypatch.setattr(cli, "run_collect_trajectories_v1", _fake_runner)

    exit_code, out, err = _run(
        [
            "collect-trajectories",
            "--lanes", "alakazam,rocket_mewtwo_spidops",
            "--num-games", "10",
            "--base-seed", "123",
            "--workers", "4",
            "--run-name", "wiring-check",
            "--behavior-kind", "rule_agent",
            "--decoding-mode", "greedy",
            "--timeout-seconds", "45.5",
            "--max-steps", "999",
            # This test checks argument -> runner-call wiring, not stdout
            # formatting, so it opts into the raw machine-readable payload;
            # see test_cli_collect_trajectories_default_stdout_is_aggregated_*
            # below for the (now default) aggregated human-readable summary.
            "--json",
        ],
        capsys,
    )

    assert exit_code == 0
    assert err == ""
    assert json.loads(out) == {"schema_version": "fixture", "games_completed": 0}
    assert recorded["lanes_arg"] == "alakazam,rocket_mewtwo_spidops"
    assert recorded["num_games"] == 10
    assert recorded["base_seed"] == 123
    assert recorded["workers"] == 4
    assert recorded["run_name"] == "wiring-check"
    assert recorded["behavior_kind"] == "rule_agent"
    assert recorded["decoding_mode"] == "greedy"
    assert recorded["timeout_seconds"] == 45.5
    assert recorded["max_steps"] == 999


def test_cli_maps_collect_trajectories_error_to_contract_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def _raising_runner(**_kwargs: object) -> dict[str, object]:
        raise CollectTrajectoriesError("fixture refusal message")

    monkeypatch.setattr(cli, "run_collect_trajectories_v1", _raising_runner)

    exit_code, out, err = _run(
        ["collect-trajectories", "--num-games", "1", "--base-seed", "0", "--run-name", "x"], capsys,
    )

    assert exit_code == 2
    assert out == ""
    error = json.loads(err)
    assert error["error_type"] == "CONTRACT_ERROR"
    assert "fixture refusal message" in error["message"]


# ---------------------------------------------------------------------------
# Default aggregated stdout summary vs. --json (a real 2,000-game run once
# flooded the terminal with a canonical-JSON faulted_jobs array; see
# docs/status handoff for the p0-rule-agent-2000 collection defect).
# ---------------------------------------------------------------------------


def _fixture_full_collect_payload_v1() -> dict[str, object]:
    """A payload shaped exactly like a real run_collect_trajectories_v1 result.

    Mirrors the measured p0-rule-agent-2000 collection run (2,000 games, 3
    lanes, 142 faulted -- 18 alakazam, 124 grimmsnarl_froslass_munkidori, 0
    rocket_mewtwo_spidops) so the aggregated summary is exercised against a
    realistic, not merely minimal, shape.
    """
    return {
        "schema_version": "meta-specialist-collect-trajectories-run-summary-v1",
        "run_name": "cli-summary-check",
        "started_at_utc": "2026-08-03T17:53:49.905154+00:00",
        "finished_at_utc": "2026-08-03T18:04:51.162719+00:00",
        "wall_time_seconds": 661.258,
        "behavior_kind": "rule_agent",
        "behavior_identity": "a" * 64,
        "opponent_kind": "cabt_rule_agent_v0",
        "decoding_mode": "greedy",
        "sampling_seed": 0,
        "source_commit": "b" * 40,
        "lanes": ["alakazam", "grimmsnarl_froslass_munkidori", "rocket_mewtwo_spidops"],
        "num_games_requested": 2000,
        "games_attempted": 2000,
        "output_root": "/fixture/output_root",
        "games_dir": "/fixture/output_root/games",
        "run_summary_path": "/fixture/output_root/run_summary.json",
        "progress_summary_path": "/fixture/output_root/progress_summary.json",
        "per_lane": {
            "alakazam": {
                "attempted": 667, "completed": 649, "resumed_skipped": 0, "faulted": 18, "timeout": 0,
                "transitions": 10668,
                "seats": {"0": {"attempted": 334, "collected": 325}, "1": {"attempted": 333, "collected": 324}},
            },
            "grimmsnarl_froslass_munkidori": {
                "attempted": 667, "completed": 543, "resumed_skipped": 0, "faulted": 124, "timeout": 0,
                "transitions": 13702,
                "seats": {"0": {"attempted": 334, "collected": 277}, "1": {"attempted": 333, "collected": 266}},
            },
            "rocket_mewtwo_spidops": {
                "attempted": 666, "completed": 666, "resumed_skipped": 0, "faulted": 0, "timeout": 0,
                "transitions": 9478,
                "seats": {"0": {"attempted": 333, "collected": 333}, "1": {"attempted": 333, "collected": 333}},
            },
        },
        "faulted_jobs": [
            {
                "job_id": f"fixture-faulted-job-{index}", "archetype_id": "grimmsnarl_froslass_munkidori",
                "seat": index % 2, "env_seed": 2000100 + index, "status": "faulted",
                "fault_reason": "worker exited with code 1",
            }
            for index in range(50)
        ],
        "final_attempts": [],
        "faulted_jobs_truncated": True,
        "games_completed": 1858,
        "games_resumed_skipped": 0,
        "games_faulted": 142,
        "games_timeout": 0,
        "transitions_collected": 33848,
    }


def test_cli_collect_trajectories_default_stdout_is_aggregated_not_raw_faulted_jobs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Fails if the CLI ever again dumps the full run-summary JSON -- one entry
    per faulted job among dozens -- to stdout by default on a real long run.
    """
    payload = _fixture_full_collect_payload_v1()
    monkeypatch.setattr(cli, "run_collect_trajectories_v1", lambda **_kwargs: payload)

    exit_code, out, err = _run(
        ["collect-trajectories", "--num-games", "2000", "--base-seed", "2000000", "--run-name", "x"], capsys,
    )

    assert exit_code == 0
    assert err == ""
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)  # default stdout is a human-readable summary, not JSON
    assert "faulted_jobs" not in out
    assert "fixture-faulted-job-0" not in out  # no per-job lines
    assert "completed=1858" in out
    assert "faulted=142" in out
    assert "50x worker exited with code 1" in out  # counted once, not 50 lines
    assert "/fixture/output_root/run_summary.json" in out
    assert out.count("\n") < 20  # short: aggregated, not one line per job


def test_cli_collect_trajectories_json_flag_still_emits_the_full_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _fixture_full_collect_payload_v1()
    monkeypatch.setattr(cli, "run_collect_trajectories_v1", lambda **_kwargs: payload)

    exit_code, out, err = _run(
        ["collect-trajectories", "--num-games", "2000", "--base-seed", "2000000", "--run-name", "x", "--json"],
        capsys,
    )

    assert exit_code == 0
    assert err == ""
    assert json.loads(out) == payload
    assert "faulted_jobs" in out
    assert "fixture-faulted-job-0" in out


# --------------------------------------------------------------------------
# A re-collection after any commit reuses nothing, because ``source_commit``
# is part of every job id. The summary has to *say* that; reporting only
# ``resumed_skipped=0`` reads as broken resume and cost a real operator a
# 20-minute re-collection they thought had failed.
# --------------------------------------------------------------------------


def test_the_survey_counts_only_records_this_plan_does_not_claim(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.collect_trajectories_v1 import (
        _survey_unplanned_existing_games_v1,
    )

    games = tmp_path / "games"
    for job_id, transitions, version in (
        ("planned-a", 3, "v-current"),
        ("planned-b", 4, "v-current"),
        ("stale-a", 5, "v-older"),
        ("stale-b", 6, "v-older"),
        ("stale-c", 7, "v-oldest"),
    ):
        directory = games / job_id
        directory.mkdir(parents=True)
        (directory / "record.json").write_text(
            json.dumps({
                "transitions": [{"i": i} for i in range(transitions)],
                "subject_behavior_version": version,
            }),
            encoding="utf-8",
        )

    survey = _survey_unplanned_existing_games_v1(
        games, planned_job_ids=frozenset({"planned-a", "planned-b"})
    )

    assert survey["count"] == 3
    assert survey["transitions"] == 5 + 6 + 7
    assert survey["unreadable"] == 0
    assert survey["behavior_versions"] == ["v-older", "v-oldest"]


def test_the_survey_counts_a_corrupt_record_rather_than_ignoring_it(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.collect_trajectories_v1 import (
        _survey_unplanned_existing_games_v1,
    )

    games = tmp_path / "games"
    (games / "broken").mkdir(parents=True)
    (games / "broken" / "record.json").write_text("{not json", encoding="utf-8")
    (games / "no-transitions").mkdir(parents=True)
    (games / "no-transitions" / "record.json").write_text("{}", encoding="utf-8")

    survey = _survey_unplanned_existing_games_v1(games, planned_job_ids=frozenset())

    assert survey["count"] == 0
    assert survey["unreadable"] == 2


def test_the_survey_is_silent_when_the_output_is_fresh(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.collect_trajectories_v1 import (
        _survey_unplanned_existing_games_v1,
    )

    survey = _survey_unplanned_existing_games_v1(
        tmp_path / "never-created", planned_job_ids=frozenset({"a"})
    )
    assert survey == {"count": 0, "transitions": 0, "unreadable": 0, "behavior_versions": []}


def test_cli_summary_explains_why_a_recollection_reused_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _fixture_full_collect_payload_v1()
    payload["existing_games_outside_this_plan"] = {
        "count": 2289,
        "transitions": 40733,
        "unreadable": 0,
        "behavior_versions": ["b89ca316191957b26e5afa37c6cd121f61ba43435724aa6b982b3b06b07ff6e1"],
    }
    monkeypatch.setattr(cli, "run_collect_trajectories_v1", lambda **_kwargs: payload)

    exit_code, out, _err = _run(
        ["collect-trajectories", "--num-games", "2000", "--base-seed", "2000000", "--run-name", "x"], capsys,
    )

    assert exit_code == 0
    assert "existing games outside this plan: 2289" in out
    assert "40733 transitions" in out
    assert "different source_commit" in out
    # The operator must learn the data is not lost, only unclaimed.
    assert "still" in out and "contribute to training" in out
    assert "b89ca3161919" in out


def test_cli_summary_stays_quiet_when_nothing_unclaimed_exists(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _fixture_full_collect_payload_v1()
    payload["existing_games_outside_this_plan"] = {
        "count": 0, "transitions": 0, "unreadable": 0, "behavior_versions": [],
    }
    monkeypatch.setattr(cli, "run_collect_trajectories_v1", lambda **_kwargs: payload)

    exit_code, out, _err = _run(
        ["collect-trajectories", "--num-games", "2000", "--base-seed", "2000000", "--run-name", "x"], capsys,
    )

    assert exit_code == 0
    assert "existing games outside this plan" not in out


# ---------------------------------------------------------------------------
# Opponent rotation: train against the distribution you measure against.
# ---------------------------------------------------------------------------


def _plan_with_rotation(tmp_path: Path, *, num_games: int, opponent_kinds):
    report_path, materialized_dir = _write_seed_qualification_fixture_v1(
        tmp_path, qualified=("alakazam",))
    lanes = list(load_qualified_lanes_v1(
        report_path=report_path, materialized_dir=materialized_dir).values())
    return build_collection_plan_v1(
        lanes=lanes, num_games=num_games, base_seed=1000, source_commit=_FIXTURE_COMMIT,
        behavior_kind="rule_agent", behavior_identity=rule_agent_behavior_identity_v1(),
        opponent_kind="cabt_rule_agent_v0", opponent_kinds=opponent_kinds,
        decoding_mode="greedy", sampling_seed=0, pool_epoch=0, policy_lag=0,
        non_terminal_discount=1.0, max_steps=100, timeout_seconds=30.0,
        neural_checkpoint_path="",
    )


def test_a_rotation_cycles_the_opponent_across_games(tmp_path: Path) -> None:
    jobs = _plan_with_rotation(tmp_path, num_games=6, opponent_kinds=("a", "b", "c"))

    assert [job.opponent_kind for job in jobs] == ["a", "b", "c", "a", "b", "c"]


def test_every_opponent_in_a_rotation_is_met_from_both_seats(tmp_path: Path) -> None:
    """The seat must not alias with the rotation.

    With ``seat = index % 2`` and an even opponent count, index and index+count
    share a parity, so each opponent would only ever be met from one seat.  The
    run-wide seat counts still look balanced, which is what makes the bug quiet:
    per matchup, half the opponents are always played first and half always
    second, and first-move advantage is large in this game.
    """
    jobs = _plan_with_rotation(tmp_path, num_games=16, opponent_kinds=("a", "b", "c", "d"))

    seats_by_opponent: dict[str, set[int]] = {}
    for job in jobs:
        seats_by_opponent.setdefault(job.opponent_kind, set()).add(job.seat)

    assert seats_by_opponent == {"a": {0, 1}, "b": {0, 1}, "c": {0, 1}, "d": {0, 1}}


def test_a_single_opponent_keeps_the_previous_seat_pattern(tmp_path: Path) -> None:
    """Backwards compatibility: one opponent must reduce to the old index % 2."""
    rotated = _plan_with_rotation(tmp_path, num_games=6, opponent_kinds=("only",))
    default = _plan_with_rotation(tmp_path, num_games=6, opponent_kinds=None)

    assert [job.seat for job in rotated] == [0, 1, 0, 1, 0, 1]
    assert [job.seat for job in default] == [0, 1, 0, 1, 0, 1]


def test_a_rotation_still_gives_every_job_a_distinct_seed_and_id(tmp_path: Path) -> None:
    jobs = _plan_with_rotation(tmp_path, num_games=9, opponent_kinds=("a", "b", "c"))

    assert [job.env_seed for job in jobs] == list(range(1000, 1009))
    assert len({job.job_id for job in jobs}) == 9


def test_a_rotation_is_reproducible(tmp_path: Path) -> None:
    first = _plan_with_rotation(tmp_path, num_games=8, opponent_kinds=("a", "b", "c"))
    second = _plan_with_rotation(tmp_path, num_games=8, opponent_kinds=("a", "b", "c"))

    assert [job.job_id for job in first] == [job.job_id for job in second]


def test_a_repeated_opponent_is_refused(tmp_path: Path) -> None:
    """A duplicate silently doubles that opponent's share of the training data."""
    with pytest.raises(CollectTrajectoriesError, match="repeats"):
        _resolve_opponent_rotation_v1("cabt_rule_agent_v0", "a,b,a")


def test_the_rotation_argument_accepts_a_comma_separated_string() -> None:
    assert _resolve_opponent_rotation_v1("unused", "a, b ,c") == ("a", "b", "c")
    assert _resolve_opponent_rotation_v1("fallback", "") == ("fallback",)
    assert _resolve_opponent_rotation_v1("fallback", None) == ("fallback",)


def test_a_named_opponent_is_recorded_under_its_own_id() -> None:
    """Regression: every game was labelled as the built-in rule agent.

    ``opponent_instance_id`` was hardcoded to ``cabt-rule-agent-seed-N``, which
    was only correct while the self-mirror was the sole opponent.  With a
    rotation it recorded a game against a registered pool member as one against
    the rule agent, so any per-opponent reading of the collected trajectories
    would silently collapse all opponents into one.
    """
    from mage_ptcg.meta_specialist.actor_pool_v1 import _opponent_instance_id_v1
    from mage_ptcg.meta_specialist.opponent_pool_v1 import OpponentInstanceV1

    registered = OpponentInstanceV1(
        opponent_id="kiyotah_dragapult", deck_csv_path="/x/deck.csv",
        policy_path="/x/main.py", canonical_deck_hash="a" * 64, policy_hash="b" * 64,
        usage_boundary="local_eval_only", source="public", mean_decision_ms=1.0,
    )

    assert _opponent_instance_id_v1(registered, 991010) == "kiyotah_dragapult"


def test_the_self_mirror_keeps_a_per_seed_instance_id() -> None:
    """The built-in rule agent is re-seeded per game, so it is a new instance."""
    from mage_ptcg.meta_specialist.actor_pool_v1 import _opponent_instance_id_v1
    from mage_ptcg.meta_specialist.opponent_pool_v1 import (
        MIRROR_OPPONENT_ID_V1, OpponentInstanceV1,
    )

    mirror = OpponentInstanceV1(
        opponent_id=MIRROR_OPPONENT_ID_V1, deck_csv_path="/x/deck.csv", policy_path="",
        canonical_deck_hash="", policy_hash="", usage_boundary="internal_mirror",
        source="engine_builtin", mean_decision_ms=None,
    )

    assert _opponent_instance_id_v1(mirror, 991010) == "cabt-rule-agent-seed-991011"


def test_the_opponent_factory_returns_a_plain_function(tmp_path: Path) -> None:
    """Regression: a callable *object* opponent was invoked with too many args.

    ``kaggle_environments`` truncates the argument list to ``co_argcount`` only
    when the agent has ``__code__``.  A class instance does not, so it received
    (observation, configuration, ...) and raised TypeError inside the opponent,
    which the engine reported only as AGENT_ERROR.  The seven ``meta_*``
    opponents bind exactly such an instance, so every game against them failed
    at step 1 and they contributed nothing to 60-opponent rotations.
    """
    import types

    from mage_ptcg.meta_specialist import opponent_pool_v1 as module

    class CallableObjectAgent:
        """The shape `agents.generic_agent.make_agent` returns."""

        def __call__(self, observation):
            return [0]

    instance = types.SimpleNamespace(
        opponent_id="fake", deck_csv_path="/x/deck.csv", policy_path="/x/main.py",
        is_mirror=False,
    )
    original = module.load_opponent_agent_callable_v1
    module.load_opponent_agent_callable_v1 = lambda _inst: CallableObjectAgent()
    try:
        factory = module.build_opponent_agent_factory_v1(instance)
        agent = factory(None, 0)
    finally:
        module.load_opponent_agent_callable_v1 = original

    assert hasattr(agent, "__code__"), "engine truncates args only for __code__ callables"
    assert agent.__code__.co_argcount == 1
    assert agent({"select": None}) == [0]


def test_a_prefix_of_a_weighted_schedule_keeps_the_target_ratios() -> None:
    """Regression: a short run collected a nearly uniform sample.

    A plain round-robin puts one of each distinct opponent first, so a run
    shorter than one full cycle ignores the weights entirely.  Measured on the
    real schedule: 200 games against a 538-entry cycle gave the archetype that
    is 28% of the observed meta no share at all, and left one that is 6% of it
    at 14.5%.
    """
    from mage_ptcg.meta_specialist.collect_trajectories_v1 import smooth_weighted_order_v1

    weights = {"heavy": 30, "mid": 10, "light_a": 1, "light_b": 1}
    order = smooth_weighted_order_v1(weights)
    total = sum(weights.values())

    assert len(order) == total
    assert Counter(order) == Counter(weights)
    prefix = Counter(order[:20])
    assert abs(prefix["heavy"] / 20 - weights["heavy"] / total) < 0.10


def test_a_rotation_longer_than_the_run_still_balances_seats(tmp_path: Path) -> None:
    """Regression: every game was played from seat 0.

    Seats used to come from the cycle number, ``(index // len(rotation)) % 2``.
    With a weighted schedule the cycle is longer than a single run, so the term
    never reached 1: a 200-game run against a 538-entry rotation played all 200
    from the same seat while `seat_counts` still looked balanced run-wide.
    """
    rotation = tuple(f"opp_{i:03d}" for i in range(50))
    jobs = _plan_with_rotation(tmp_path, num_games=20, opponent_kinds=rotation)

    seats_by_opponent: dict[str, list[int]] = {}
    for job in jobs:
        seats_by_opponent.setdefault(job.opponent_kind, []).append(job.seat)

    # 20 games over 50 opponents: each is met once, and the first meeting is
    # seat 0 -- but a second meeting must flip, which the cycle rule never did.
    jobs = _plan_with_rotation(tmp_path, num_games=120, opponent_kinds=rotation)
    seats_by_opponent = {}
    for job in jobs:
        seats_by_opponent.setdefault(job.opponent_kind, set()).add(job.seat)
    both = [name for name, seats in seats_by_opponent.items() if seats == {0, 1}]

    assert len(both) == 50, f"only {len(both)}/50 opponents were met from both seats"
