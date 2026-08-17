from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import time

import pytest

import mage_ptcg.meta_specialist.teacher_quality_worker_v3 as worker_v3
import mage_ptcg.meta_specialist.teacher_quality_evidence_v3 as evidence_v3
from mage_ptcg.meta_specialist.teacher_quality_evidence_v3 import (
    AttemptObservationV3,
    LaneEvidenceInputV3,
    build_campaign_plan_v3,
    build_live_attempt_runner_v3,
    collect_teacher_quality_evidence_v3,
    read_attempt_ledger_v3,
    read_ready_teacher_quality_manifest_v3,
    read_source_snapshot_entry_v3,
    seal_teacher_quality_source_snapshot_v3,
    verify_source_snapshot_v3,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _pool(tmp_path: Path) -> tuple[Path, str, Path, str, tuple[LaneEvidenceInputV3, ...]]:
    pool_root = tmp_path / "pool"
    rows = []
    for opponent_id in ("teacher-a", "teacher-b", *(f"opponent-{index}" for index in range(8))):
        root = pool_root / opponent_id
        root.mkdir(parents=True)
        deck = ("1\n" * 60).encode()
        policy = b"def agent(observation):\n    return [0]\n"
        (root / "deck.csv").write_bytes(deck)
        (root / "main.py").write_bytes(policy)
        rows.append({
            "id": opponent_id,
            "canonical_deck_hash": _sha(deck),
            "policy_hash": _sha(policy),
            "usage_boundary": "local_eval_only",
            "source": "fixture",
            "mean_decision_ms": 1.0,
            "smoke_ok": True,
        })
    manifest = pool_root / "pool_manifest.json"
    manifest.write_bytes(_canonical(rows))
    schedule = tmp_path / "schedule.json"
    schedule.write_bytes(_canonical({row["id"]: 1 for row in rows}))
    deck_sha = _sha((pool_root / "teacher-a" / "deck.csv").read_bytes())
    lanes = (
        LaneEvidenceInputV3("alakazam", "teacher-a", "r-a", str(pool_root / "teacher-a" / "deck.csv"), deck_sha),
        LaneEvidenceInputV3("archaludon", "teacher-b", "r-b", str(pool_root / "teacher-b" / "deck.csv"), deck_sha),
    )
    return pool_root, _sha(manifest.read_bytes()), schedule, _sha(schedule.read_bytes()), lanes


def _plan(tmp_path: Path, profile: str = "calibration"):
    pool_root, pool_sha, schedule, schedule_sha, lanes = _pool(tmp_path)
    engine = tmp_path / "engine.py"
    engine.write_bytes(b"engine-v1")
    source_commit = "a" * 40
    return build_campaign_plan_v3(
        profile=profile,
        lanes=lanes,
        schedule_path=schedule,
        expected_schedule_sha256=schedule_sha,
        pool_root=pool_root,
        expected_pool_manifest_sha256=pool_sha,
        engine_entry_point=engine,
        expected_engine_sha256=_sha(engine.read_bytes()),
        source_commit=source_commit,
        expected_source_commit_sha256=_sha(source_commit.encode()),
    )


def _smoke_failed_plan(tmp_path: Path):
    pool_root, _pool_sha, schedule, schedule_sha, lanes = _pool(tmp_path)
    manifest = pool_root / "pool_manifest.json"
    rows = json.loads(manifest.read_text())
    rows[0]["smoke_ok"] = False
    manifest.write_bytes(_canonical(rows))
    engine = tmp_path / "engine.py"
    engine.write_bytes(b"engine-v1")
    source_commit = "a" * 40
    return build_campaign_plan_v3(
        profile="calibration",
        lanes=lanes,
        schedule_path=schedule,
        expected_schedule_sha256=schedule_sha,
        pool_root=pool_root,
        expected_pool_manifest_sha256=_sha(manifest.read_bytes()),
        engine_entry_point=engine,
        expected_engine_sha256=_sha(engine.read_bytes()),
        source_commit=source_commit,
        expected_source_commit_sha256=_sha(source_commit.encode()),
    )


def _sealed_snapshot(tmp_path: Path):
    return seal_teacher_quality_source_snapshot_v3(
        plan=_plan(tmp_path / "inputs"),
        staging_root=tmp_path / "stage",
    )


def _fixture_policy_path(snapshot, policy_sha256: str) -> str:
    manifest = json.loads(read_source_snapshot_entry_v3(snapshot, "source-manifest.json"))
    matches = [
        row["path"] for row in manifest["entries"]
        if row["sha256"] == policy_sha256 and str(row["path"]).endswith("/policy.py")
    ]
    assert len(matches) == 1
    return matches[0]


def _run_fixture_worker(tmp_path: Path, *, agent_sampling_seed: int = 17) -> dict[str, object]:
    policy = (
        "import random\n"
        "try:\n"
        "    import numpy\n"
        "except ImportError:\n"
        "    numpy = None\n"
        "try:\n"
        "    import torch\n"
        "except ImportError:\n"
        "    torch = None\n"
        "seen = 0\n"
        "def agent(_observation):\n"
        "    global seen\n"
        "    seen += 1\n"
        "    if seen != 1:\n"
        "        return {'outcome': 'loss'}\n"
        "    if random.randrange(1_000_000) != 547339:\n"
        "        return {'outcome': 'loss'}\n"
        "    if numpy is not None and int(numpy.random.randint(1_000_000)) != 993903:\n"
        "        return {'outcome': 'loss'}\n"
        "    if torch is not None and int(torch.randint(1_000_000, (1,)).item()) != 576559:\n"
        "        return {'outcome': 'loss'}\n"
        "    return {'outcome': 'win'}\n"
    ).encode()
    pool_root, pool_sha, schedule, schedule_sha, lanes = _pool(tmp_path / "inputs")
    teacher_policy = pool_root / "teacher-a" / "main.py"
    teacher_policy.write_bytes(policy)
    manifest = pool_root / "pool_manifest.json"
    rows = json.loads(manifest.read_text())
    rows[0]["policy_hash"] = _sha(policy)
    manifest.write_bytes(_canonical(rows))
    engine = tmp_path / "inputs" / "engine.py"
    engine.write_bytes(b"engine-v1")
    plan = build_campaign_plan_v3(
        profile="calibration",
        lanes=lanes,
        schedule_path=schedule,
        expected_schedule_sha256=schedule_sha,
        pool_root=pool_root,
        expected_pool_manifest_sha256=_sha(manifest.read_bytes()),
        engine_entry_point=engine,
        expected_engine_sha256=_sha(engine.read_bytes()),
        source_commit="a" * 40,
        expected_source_commit_sha256=_sha(("a" * 40).encode()),
    )
    snapshot = seal_teacher_quality_source_snapshot_v3(
        plan=plan, staging_root=tmp_path / "stage",
    )
    try:
        request = worker_v3.build_teacher_quality_worker_request_v3(
            snapshot=snapshot,
            campaign_id=plan.campaign_id,
            logical_game_id="f" * 64,
            retry_index=0,
            subject_seat=0,
            agent_sampling_seed=agent_sampling_seed,
            policy_path=_fixture_policy_path(snapshot, _sha(policy)),
        )
        request_path = tmp_path / "request.json"
        request_path.write_bytes(_canonical(request))
        return worker_v3.run_teacher_quality_attempt_worker_v3(request_path)
    finally:
        snapshot.close()


def _bridge_plan(tmp_path: Path):
    pool_root, pool_sha, schedule, schedule_sha, lanes = _pool(tmp_path / "inputs")
    engine = tmp_path / "engine.py"
    engine.write_text(
        "def run_teacher_quality_game_v3(*, subject_agent, opponent_agent, subject_deck_path, opponent_deck_path, subject_seat, environment_seed, max_steps):\n"
        "    assert subject_agent({'select': {'option': [0], 'minCount': 1, 'maxCount': 1}}) == [0]\n"
        "    assert opponent_agent({'select': {'option': [0], 'minCount': 1, 'maxCount': 1}}) == [0]\n"
        "    assert subject_deck_path.read_text() == ('1\\n' * 60)\n"
        "    assert opponent_deck_path.read_text() == ('1\\n' * 60)\n"
        "    assert max_steps == 77\n"
        "    return {'status': 'DONE', 'winner': subject_seat}\n",
        encoding="utf-8",
    )
    source_commit = "a" * 40
    return build_campaign_plan_v3(
        profile="calibration",
        lanes=lanes,
        schedule_path=schedule,
        expected_schedule_sha256=schedule_sha,
        pool_root=pool_root,
        expected_pool_manifest_sha256=pool_sha,
        engine_entry_point=engine,
        expected_engine_sha256=_sha(engine.read_bytes()),
        source_commit=source_commit,
        expected_source_commit_sha256=_sha(source_commit.encode()),
    )


def _default_engine_plan(tmp_path: Path):
    pool_root, pool_sha, schedule, schedule_sha, lanes = _pool(tmp_path / "inputs")
    engine = Path(__file__).resolve().parents[2] / "scripts" / "test_sim.py"
    source_commit = "a" * 40
    return build_campaign_plan_v3(
        profile="calibration",
        lanes=lanes,
        schedule_path=schedule,
        expected_schedule_sha256=schedule_sha,
        pool_root=pool_root,
        expected_pool_manifest_sha256=pool_sha,
        engine_entry_point=engine,
        expected_engine_sha256=_sha(engine.read_bytes()),
        source_commit=source_commit,
        expected_source_commit_sha256=_sha(source_commit.encode()),
    )


def _actual_plan_only_input() -> object:
    """Build the exact current CLI-default source/pool input without running CABT."""
    root = Path(__file__).resolve().parents[2]
    pool_root = root / "opponents"
    schedule = root / "configs" / "meta_specialist" / "opponent_schedule_v1.json"
    alakazam_deck = pool_root / "nihei_alakazam" / "deck.csv"
    archaludon_deck = pool_root / "public_archaludon_cinderace_r7" / "deck.csv"
    source_commit = "teacher-quality-v3-plan-only-closure"
    return build_campaign_plan_v3(
        profile="full",
        lanes=(
            LaneEvidenceInputV3("alakazam", "nihei_alakazam", "a502b37132b5558f", str(alakazam_deck), _sha(alakazam_deck.read_bytes())),
            LaneEvidenceInputV3("archaludon", "public_archaludon_cinderace_r7", "c08588467c3faa2c", str(archaludon_deck), _sha(archaludon_deck.read_bytes())),
        ),
        schedule_path=schedule,
        expected_schedule_sha256=_sha(schedule.read_bytes()),
        pool_root=pool_root,
        expected_pool_manifest_sha256=_sha((pool_root / "pool_manifest.json").read_bytes()),
        engine_entry_point=root / "scripts" / "test_sim.py",
        expected_engine_sha256=_sha((root / "scripts" / "test_sim.py").read_bytes()),
        source_commit=source_commit,
        expected_source_commit_sha256=_sha(source_commit.encode()),
    )


def _cwd_sensitive_plan(tmp_path: Path):
    pool_root, pool_sha, schedule, schedule_sha, lanes = _pool(tmp_path / "inputs")
    deck = (pool_root / "teacher-a" / "deck.csv").read_bytes()
    deck_sha = _sha(deck)
    policy = (
        "import hashlib\n"
        "import os\n"
        "from pathlib import Path\n"
        "root = Path(__file__).parents[3]\n"
        "assert str(root).startswith('/proc/self/fd/')\n"
        "assert (os.stat('.').st_dev, os.stat('.').st_ino) == (os.stat(root).st_dev, os.stat(root).st_ino)\n"
        "assert not Path('deck.csv').exists()\n"
        "assert not Path('data/raw/extracted/EN_Card_Data.csv').exists()\n"
        f"assert hashlib.sha256(Path(__file__).with_name('deck.csv').read_bytes()).hexdigest() == {deck_sha!r}\n"
        "def agent(_observation):\n"
        "    return {'outcome': 'win'}\n"
    ).encode()
    teacher_policy = pool_root / "teacher-a" / "main.py"
    teacher_policy.write_bytes(policy)
    manifest = pool_root / "pool_manifest.json"
    rows = json.loads(manifest.read_text())
    rows[0]["policy_hash"] = _sha(policy)
    manifest.write_bytes(_canonical(rows))
    engine = tmp_path / "inputs" / "engine.py"
    engine.write_bytes(b"engine-v1")
    source_commit = "a" * 40
    return build_campaign_plan_v3(
        profile="calibration",
        lanes=lanes,
        schedule_path=schedule,
        expected_schedule_sha256=schedule_sha,
        pool_root=pool_root,
        expected_pool_manifest_sha256=_sha(manifest.read_bytes()),
        engine_entry_point=engine,
        expected_engine_sha256=_sha(engine.read_bytes()),
        source_commit=source_commit,
        expected_source_commit_sha256=_sha(source_commit.encode()),
    )


def _write_cwd_poison(host_root: Path) -> None:
    (host_root / "deck.csv").write_text("HOST-DECK-POISON\n", encoding="utf-8")
    card_data = host_root / "data" / "raw" / "extracted" / "EN_Card_Data.csv"
    card_data.parent.mkdir(parents=True)
    card_data.write_text("HOST-CARD-DATA-POISON\n", encoding="utf-8")
    poison_cg = host_root / "cg"
    poison_cg.mkdir()
    (poison_cg / "__init__.py").write_text("raise RuntimeError('host cg poison imported')\n", encoding="utf-8")


def _write_minimal_cg_source(repo_root: Path) -> None:
    """Give synthetic source-root fixtures the same required root package shape."""
    cg = repo_root / "cg"
    cg.mkdir()
    (cg / "__init__.py").write_text("marker = 'sealed-cg'\n", encoding="utf-8")


def _race_plan(tmp_path: Path, *, policy_source: str, engine_source: str):
    pool_root, _pool_sha, schedule, schedule_sha, lanes = _pool(tmp_path / "inputs")
    policy = pool_root / "teacher-a" / "main.py"
    policy.write_text(policy_source, encoding="utf-8")
    manifest = pool_root / "pool_manifest.json"
    rows = json.loads(manifest.read_text())
    rows[0]["policy_hash"] = _sha(policy.read_bytes())
    manifest.write_bytes(_canonical(rows))
    engine = tmp_path / "inputs" / "engine.py"
    engine.write_text(engine_source, encoding="utf-8")
    source_commit = "a" * 40
    return build_campaign_plan_v3(
        profile="calibration", lanes=lanes, schedule_path=schedule,
        expected_schedule_sha256=schedule_sha, pool_root=pool_root,
        expected_pool_manifest_sha256=_sha(manifest.read_bytes()), engine_entry_point=engine,
        expected_engine_sha256=_sha(engine.read_bytes()), source_commit=source_commit,
        expected_source_commit_sha256=_sha(source_commit.encode()),
    )


def _wait_for_race_marker(marker: Path) -> None:
    deadline = time.monotonic() + 10.0
    while not marker.exists():
        if time.monotonic() >= deadline:
            pytest.fail(f"race child did not reach synchronization marker: {marker}")
        time.sleep(0.01)


def _run_concurrently(call):
    result: list[object] = []

    def target() -> None:
        try:
            result.append(call())
        except BaseException as exc:  # test harness must retain the child-facing failure
            result.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, result


def _join_race_thread(thread: threading.Thread, result: list[object]) -> BaseException:
    thread.join(timeout=10.0)
    assert not thread.is_alive()
    assert len(result) == 1
    assert isinstance(result[0], BaseException)
    return result[0]


def test_policy_import_preflight_reverifies_snapshot_after_concurrent_mutation(tmp_path: Path) -> None:
    """Catches a policy probe returning after its imported snapshot bytes change."""
    ready = tmp_path / "policy-ready"
    release = tmp_path / "policy-release"
    policy_source = (
        "from pathlib import Path\n"
        "import time\n"
        f"ready = Path({str(ready)!r})\n"
        f"release = Path({str(release)!r})\n"
        "ready.write_text('ready')\n"
        "while not release.exists():\n"
        "    time.sleep(0.01)\n"
        "def agent(_observation):\n"
        "    return {'outcome': 'win'}\n"
    )
    plan = _race_plan(tmp_path, policy_source=policy_source, engine_source="import main\n")
    snapshot = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "snapshots")
    game = next(item for item in plan.logical_games if item.arm == "teacher")
    policy_path = evidence_v3._subject_policy_path_v3(game, snapshot)
    try:
        thread, result = _run_concurrently(lambda: worker_v3.validate_snapshot_policy_imports_v3(
            snapshot=snapshot, policy_paths=(policy_path,),
        ))
        _wait_for_race_marker(ready)
        (snapshot.root / policy_path).write_text("def agent(_observation): return {'outcome': 'loss'}\n", encoding="utf-8")
        release.write_text("release", encoding="utf-8")
        error = _join_race_thread(thread, result)
    finally:
        snapshot.close()

    assert isinstance(error, ValueError)
    assert "snapshot policy import preflight failed" in str(error)


def test_engine_import_preflight_reverifies_snapshot_after_concurrent_mutation(tmp_path: Path) -> None:
    """Catches an engine probe returning after its imported snapshot bytes change."""
    ready = tmp_path / "engine-ready"
    release = tmp_path / "engine-release"
    engine_source = (
        "import main\n"
        "from pathlib import Path\n"
        "import time\n"
        f"ready = Path({str(ready)!r})\n"
        f"release = Path({str(release)!r})\n"
        "ready.write_text('ready')\n"
        "while not release.exists():\n"
        "    time.sleep(0.01)\n"
    )
    plan = _race_plan(tmp_path, policy_source="def agent(_observation): return {'outcome': 'win'}\n", engine_source=engine_source)
    snapshot = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "snapshots")
    try:
        engine_path = evidence_v3._snapshot_engine_path_v3(plan.logical_games[0], snapshot)
        thread, result = _run_concurrently(lambda: worker_v3.validate_snapshot_engine_import_v3(
            snapshot=snapshot, engine_path=engine_path,
        ))
        _wait_for_race_marker(ready)
        (snapshot.root / engine_path).write_text("import main\n", encoding="utf-8")
        release.write_text("release", encoding="utf-8")
        error = _join_race_thread(thread, result)
    finally:
        snapshot.close()

    assert isinstance(error, ValueError)
    assert "snapshot engine import setup failed" in str(error)


def test_attempt_worker_reverifies_snapshot_before_starting_bridge_after_mutation(
    tmp_path: Path,
) -> None:
    """Catches a game bridge starting after an already-imported policy's bytes change."""
    ready = tmp_path / "attempt-ready"
    release = tmp_path / "attempt-release"
    bridge_started = tmp_path / "bridge-started"
    policy_source = "def agent(_observation): return [0]\n"
    engine_source = (
        "from pathlib import Path\n"
        "import time\n"
        f"ready = Path({str(ready)!r})\n"
        f"release = Path({str(release)!r})\n"
        f"bridge_started = Path({str(bridge_started)!r})\n"
        "ready.write_text('ready')\n"
        "while not release.exists():\n"
        "    time.sleep(0.01)\n"
        "def run_teacher_quality_game_v3(**_kwargs):\n"
        "    bridge_started.write_text('started')\n"
        "    return {'status': 'DONE', 'winner': 0}\n"
    )
    plan = _race_plan(tmp_path, policy_source=policy_source, engine_source=engine_source)
    snapshot = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "snapshots")
    game = next(item for item in plan.logical_games if item.arm == "teacher" and item.seat == 0)
    try:
        policy_path = evidence_v3._subject_policy_path_v3(game, snapshot)
        request = worker_v3.build_teacher_quality_worker_request_v3(
            snapshot=snapshot, campaign_id=plan.campaign_id,
            logical_game_id=game.logical_game_id, retry_index=0,
            subject_seat=game.seat, agent_sampling_seed=game.agent_sampling_seed,
            policy_path=policy_path,
            game=evidence_v3._game_bridge_request_v3(game, snapshot, max_steps=1),
        )
        request_path = tmp_path / "request.json"
        request_path.write_bytes(_canonical(request))
        thread, result = _run_concurrently(lambda: worker_v3.run_teacher_quality_attempt_worker_v3(request_path))
        _wait_for_race_marker(ready)
        engine_path = evidence_v3._snapshot_engine_path_v3(game, snapshot)
        (snapshot.root / engine_path).write_text("def run_teacher_quality_game_v3(**_kwargs): return {'status': 'DONE', 'winner': 0}\n", encoding="utf-8")
        release.write_text("release", encoding="utf-8")
        thread.join(timeout=10.0)
        assert not thread.is_alive()
        assert len(result) == 1
        response = result[0]
    finally:
        snapshot.close()

    assert isinstance(response, dict)
    assert response["fault"]["kind"] == "worker_exception"
    assert not bridge_started.exists()


def test_actual_frozen_panel_cg_closure_imports_all_nine_policies(tmp_path: Path) -> None:
    """Catches the actual generic-panel import falling back to an absent live ``cg`` tree."""
    plan = _actual_plan_only_input()
    snapshot = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "snapshots")
    try:
        policy_paths = evidence_v3._snapshot_policy_import_paths_v3(plan, snapshot)
        manifest = json.loads(read_source_snapshot_entry_v3(snapshot, "source-manifest.json"))
        paths = {row["path"] for row in manifest["entries"]}

        assert len(policy_paths) == 9
        assert {"cg/__init__.py", "cg/api.py", "cg/sim.py", "cg/libcg.so"} <= paths
        assert all("__pycache__" not in path and not path.endswith((".pyc", ".pyo")) for path in paths)
        assert all(str(Path(__file__).resolve().parents[2]) not in path for path in paths)
        assert worker_v3.validate_snapshot_policy_imports_v3(
            snapshot=snapshot, policy_paths=policy_paths,
        ) == list(policy_paths)
    finally:
        snapshot.close()


def test_sealed_cwd_poison_cannot_reach_import_probe_or_attempt_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches isolated children inheriting host cwd instead of the sealed root descriptor."""
    plan = _cwd_sensitive_plan(tmp_path)
    snapshot = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "snapshots")
    poison_root = tmp_path / "host-cwd-poison"
    poison_root.mkdir()
    _write_cwd_poison(poison_root)
    game = next(item for item in plan.logical_games if item.arm == "teacher" and item.seat == 0)
    try:
        policy_path = evidence_v3._subject_policy_path_v3(game, snapshot)
        monkeypatch.chdir(poison_root)
        assert worker_v3.validate_snapshot_policy_imports_v3(
            snapshot=snapshot, policy_paths=(policy_path,),
        ) == [policy_path]
        request = worker_v3.build_teacher_quality_worker_request_v3(
            snapshot=snapshot, campaign_id=plan.campaign_id,
            logical_game_id=game.logical_game_id, retry_index=0,
            subject_seat=game.seat, agent_sampling_seed=game.agent_sampling_seed,
            policy_path=policy_path,
        )
        request_path = tmp_path / "request.json"
        request_path.write_bytes(_canonical(request))
        response = worker_v3.run_teacher_quality_attempt_worker_v3(request_path)
    finally:
        snapshot.close()

    assert response["outcome"] == "win"


def test_worker_seeds_before_external_policy_import(tmp_path: Path) -> None:
    response = _run_fixture_worker(tmp_path, agent_sampling_seed=17)

    assert response["rng"] == {"python": 17, "numpy": 17, "torch": 17}


def test_second_worker_does_not_observe_first_policy_global(tmp_path: Path) -> None:
    assert _run_fixture_worker(tmp_path / "first", agent_sampling_seed=17)["outcome"] == "win"
    assert _run_fixture_worker(tmp_path / "second", agent_sampling_seed=17)["outcome"] == "win"


def test_worker_never_claims_unattested_engine_seed(tmp_path: Path) -> None:
    assert _run_fixture_worker(tmp_path)["engine_randomness"] == "unattested"


@pytest.mark.parametrize("subject_seat", (0, 1))
def test_sealed_worker_bridge_converts_engine_winner_to_subject_outcome(
    tmp_path: Path, subject_seat: int,
) -> None:
    """Catches a bridge that ignores the requested seat or reports engine winner directly."""
    plan = _bridge_plan(tmp_path / "inputs")
    game = next(item for item in plan.logical_games if item.arm == "teacher" and item.seat == subject_seat)
    runner = build_live_attempt_runner_v3(
        plan=plan, transient_root=tmp_path / "snapshots", max_steps=77,
    )
    try:
        observation = runner(game, 0)
    finally:
        runner.close()

    assert observation.outcome == "win"
    assert observation.fault is None


def test_default_engine_setup_imports_main_from_replaced_snapshot_capability(tmp_path: Path) -> None:
    """Catches default-engine setup that resolves main.py from the host worktree."""
    plan = _default_engine_plan(tmp_path)
    snapshot = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "snapshots")
    original_root = snapshot.root
    relocated_root = tmp_path / "relocated-snapshot"
    poison_root = tmp_path / "poison"
    poison_root.mkdir()
    (poison_root / "main.py").write_text("raise RuntimeError('host-path fallback')\n", encoding="utf-8")
    try:
        original_root.rename(relocated_root)
        original_root.symlink_to(poison_root, target_is_directory=True)
        engine_path = evidence_v3._snapshot_engine_path_v3(plan.logical_games[0], snapshot)
        result = worker_v3.validate_snapshot_engine_import_v3(
            snapshot=snapshot, engine_path=engine_path,
        )
    finally:
        snapshot.close()

    assert result["engine_path"] == engine_path
    assert result["main_path"].startswith(f"/proc/self/fd/{snapshot.root_fd}/")
    assert result["main_path"].endswith("/main.py")


@pytest.mark.parametrize("subject_seat", (0, 1))
def test_rule_v0_baseline_bridge_uses_root_snapshot_main_with_adjacent_agents(
    tmp_path: Path, subject_seat: int,
) -> None:
    """Catches the copied inputs/rule-v0 main.py losing its sealed agents closure."""
    plan = _bridge_plan(tmp_path / "inputs")
    game = next(
        item for item in plan.logical_games
        if item.arm == "rule-v0-baseline" and item.seat == subject_seat
    )
    runner = build_live_attempt_runner_v3(
        plan=plan, transient_root=tmp_path / "snapshots", max_steps=77,
    )
    try:
        observation = runner(game, 0)
    finally:
        runner.close()

    assert observation.outcome == "win"
    assert observation.fault is None


def test_snapshot_merges_vendor_generic_agent_with_rule_agents_for_both_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic-panel policy and Rule v0 must share one sealed ``agents`` package."""
    plan = _bridge_plan(tmp_path / "inputs")
    fake_repo = tmp_path / "fake-repo"
    repo_root = Path(__file__).resolve().parents[2]
    shutil.copytree(repo_root / "src" / "mage_ptcg", fake_repo / "src" / "mage_ptcg")
    _write_minimal_cg_source(fake_repo)
    (fake_repo / "scripts").mkdir()
    (fake_repo / "scripts" / "test_sim.py").write_text("", encoding="utf-8")
    (fake_repo / "agents").mkdir()
    (fake_repo / "agents" / "__init__.py").write_text("from .rule_agent import marker\n", encoding="utf-8")
    (fake_repo / "agents" / "rule_agent.py").write_text("marker = 'rule-v0'\n", encoding="utf-8")
    (fake_repo / "main.py").write_text("from agents.rule_agent import marker\nagent = lambda _obs: [0]\n", encoding="utf-8")
    (fake_repo / "vendor_opponent_pilots" / "agents").mkdir(parents=True)
    (fake_repo / "vendor_opponent_pilots" / "agents" / "__init__.py").write_text("", encoding="utf-8")
    (fake_repo / "vendor_opponent_pilots" / "agents" / "generic_agent.py").write_text(
        "def make_agent(_deck):\n    return lambda _obs: [0]\n", encoding="utf-8",
    )
    generic_policy = "from agents.generic_agent import make_agent\nagent = make_agent([])\n"
    manifest = Path(plan.pool_root) / "pool_manifest.json"
    rows = json.loads(manifest.read_text())
    for row in rows:
        if row["id"] not in {"teacher-a", "teacher-b"}:
            policy = Path(plan.pool_root) / row["id"] / "main.py"
            policy.write_text(generic_policy, encoding="utf-8")
            row["policy_hash"] = _sha(policy.read_bytes())
    manifest.write_bytes(_canonical(rows))
    # Re-freeze the plan so the changed policy identity is part of its authority.
    plan = build_campaign_plan_v3(
        profile="calibration", lanes=plan.lanes, schedule_path=plan.schedule_path,
        expected_schedule_sha256=plan.schedule_sha256, pool_root=plan.pool_root,
        expected_pool_manifest_sha256=_sha(manifest.read_bytes()), engine_entry_point=plan.engine_entry_point,
        expected_engine_sha256=plan.engine_sha256, source_commit=plan.source_commit,
        expected_source_commit_sha256=plan.source_commit_sha256,
    )
    monkeypatch.setattr(worker_v3, "_repo_root", lambda: fake_repo)
    snapshot = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "snapshots")
    try:
        generic = next(item for item in plan.logical_games if item.arm == "teacher")
        policy_paths = (
            "main.py",
            evidence_v3._subject_policy_path_v3(generic, snapshot),
            evidence_v3._game_bridge_request_v3(generic, snapshot, max_steps=1)["opponent_policy_path"],
        )
        imported = worker_v3.validate_snapshot_policy_imports_v3(snapshot=snapshot, policy_paths=policy_paths)
    finally:
        snapshot.close()

    assert imported == list(policy_paths)


@pytest.mark.parametrize("arm", ("teacher", "rule-v0-baseline"))
@pytest.mark.parametrize("subject_seat", (0, 1))
def test_generic_panel_bridge_runs_both_arms_and_seats_inside_sealed_agents_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arm: str, subject_seat: int,
) -> None:
    """Import-only probes are insufficient: the merged package must play both arms."""
    plan = _bridge_plan(tmp_path / "inputs")
    fake_repo = tmp_path / "fake-repo"
    repo_root = Path(__file__).resolve().parents[2]
    shutil.copytree(repo_root / "src" / "mage_ptcg", fake_repo / "src" / "mage_ptcg")
    _write_minimal_cg_source(fake_repo)
    (fake_repo / "scripts").mkdir()
    (fake_repo / "scripts" / "test_sim.py").write_text("", encoding="utf-8")
    shutil.copytree(repo_root / "agents", fake_repo / "agents")
    shutil.copy2(repo_root / "main.py", fake_repo / "main.py")
    (fake_repo / "vendor_opponent_pilots" / "agents").mkdir(parents=True)
    (fake_repo / "vendor_opponent_pilots" / "agents" / "generic_agent.py").write_text(
        "def make_agent(_deck):\n    return lambda _obs: [0]\n", encoding="utf-8",
    )
    generic_policy = "from agents.generic_agent import make_agent\nagent = make_agent([])\n"
    manifest = Path(plan.pool_root) / "pool_manifest.json"
    rows = json.loads(manifest.read_text())
    for row in rows:
        if row["id"] not in {"teacher-a", "teacher-b"}:
            policy = Path(plan.pool_root) / row["id"] / "main.py"
            policy.write_text(generic_policy, encoding="utf-8")
            row["policy_hash"] = _sha(policy.read_bytes())
    manifest.write_bytes(_canonical(rows))
    plan = build_campaign_plan_v3(
        profile="calibration", lanes=plan.lanes, schedule_path=plan.schedule_path,
        expected_schedule_sha256=plan.schedule_sha256, pool_root=plan.pool_root,
        expected_pool_manifest_sha256=_sha(manifest.read_bytes()), engine_entry_point=plan.engine_entry_point,
        expected_engine_sha256=plan.engine_sha256, source_commit=plan.source_commit,
        expected_source_commit_sha256=plan.source_commit_sha256,
    )
    monkeypatch.setattr(worker_v3, "_repo_root", lambda: fake_repo)
    snapshot = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "snapshots")
    game = next(
        item for item in plan.logical_games
        if item.arm == arm and item.seat == subject_seat
    )
    runner = build_live_attempt_runner_v3(
        plan=plan, source_snapshot=snapshot, max_steps=77,
    )
    try:
        observation = runner(game, 0)
    finally:
        runner.close()
        snapshot.close()

    assert observation.outcome == "win"


def test_snapshot_ignores_python_cache_entries_and_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Volatile bytecode must not alter source-closure digest or sealing success."""
    plan = _plan(tmp_path / "inputs")
    fake_repo = tmp_path / "fake-repo"
    (fake_repo / "src" / "mage_ptcg" / "__pycache__").mkdir(parents=True)
    (fake_repo / "src" / "mage_ptcg" / "__init__.py").write_text("", encoding="utf-8")
    (fake_repo / "src" / "mage_ptcg" / "module.py").write_text("x = 1\n", encoding="utf-8")
    (fake_repo / "src" / "mage_ptcg" / "module.pyc").write_bytes(b"old")
    (fake_repo / "src" / "mage_ptcg" / "__pycache__" / "module.cpython-312.pyc").write_bytes(b"old-cache")
    _write_minimal_cg_source(fake_repo)
    (fake_repo / "scripts").mkdir()
    (fake_repo / "scripts" / "test_sim.py").write_text("", encoding="utf-8")
    (fake_repo / "main.py").write_text("agent = lambda _obs: [0]\n", encoding="utf-8")
    (fake_repo / "agents").mkdir()
    (fake_repo / "agents" / "__init__.py").write_text("", encoding="utf-8")
    (fake_repo / "agents" / "rule_agent.py").write_text("", encoding="utf-8")
    (fake_repo / "vendor_opponent_pilots" / "agents").mkdir(parents=True)
    (fake_repo / "vendor_opponent_pilots" / "agents" / "generic_agent.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(worker_v3, "_repo_root", lambda: fake_repo)
    first = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "snapshots")
    try:
        (fake_repo / "src" / "mage_ptcg" / "module.pyc").write_bytes(b"new")
        (fake_repo / "src" / "mage_ptcg" / "__pycache__" / "module.cpython-312.pyc").write_bytes(b"new-cache")
        second = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "snapshots")
        try:
            manifest = json.loads(read_source_snapshot_entry_v3(second, "source-manifest.json"))
            assert first.tree_sha256 == second.tree_sha256
            assert all("__pycache__" not in row["path"] and not row["path"].endswith(".pyc") for row in manifest["entries"])
        finally:
            second.close()
    finally:
        first.close()


def test_source_snapshot_seals_a_contained_canonical_manifest(tmp_path: Path) -> None:
    snapshot = _sealed_snapshot(tmp_path)
    manifest_raw = snapshot.manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)

    assert snapshot.root.is_dir()
    assert snapshot.manifest_path == snapshot.root / "source-manifest.json"
    assert snapshot.file_sha256 == _sha(manifest_raw)
    assert manifest["entries"]
    assert all(not Path(row["path"]).is_absolute() for row in manifest["entries"])
    assert all(
        stat.S_IMODE(directory.stat().st_mode) == 0o700
        for directory in snapshot.root.rglob("*") if directory.is_dir()
    )
    assert verify_source_snapshot_v3(snapshot) == snapshot
    snapshot.close()


def test_snapshot_verification_does_not_leak_directory_descriptors(tmp_path: Path) -> None:
    """Catches verifier scans that retain a descriptor for every source directory."""
    snapshot = _sealed_snapshot(tmp_path)
    try:
        before = len(os.listdir("/proc/self/fd"))
        for _ in range(3):
            verify_source_snapshot_v3(snapshot)
        assert len(os.listdir("/proc/self/fd")) <= before + 2
    finally:
        snapshot.close()


def test_snapshot_rejects_same_inode_post_hash_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path / "inputs")
    engine = Path(plan.engine_entry_point)
    engine_identity = os.stat(engine)
    original_fstat = worker_v3.os.fstat
    state = {"seen": False, "rewritten": False}

    def fstat_with_rewrite(descriptor: int):
        current = original_fstat(descriptor)
        if (
            state["seen"] and not state["rewritten"]
            and (current.st_dev, current.st_ino) == (engine_identity.st_dev, engine_identity.st_ino)
        ):
            engine.write_bytes(b"engine-v1-rewritten-in-place")
            state["rewritten"] = True
            current = original_fstat(descriptor)
        elif (current.st_dev, current.st_ino) == (engine_identity.st_dev, engine_identity.st_ino):
            state["seen"] = True
        return current

    monkeypatch.setattr(worker_v3.os, "fstat", fstat_with_rewrite)
    with pytest.raises(ValueError, match="changed while being snapshotted"):
        seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "stage")
    assert state["rewritten"] is True


def test_snapshot_manifest_rejects_symlink_and_escape(tmp_path: Path) -> None:
    snapshot = _sealed_snapshot(tmp_path)
    (snapshot.root / "src" / "outside.py").symlink_to("/etc/passwd")

    with pytest.raises(ValueError, match="snapshot entry"):
        verify_source_snapshot_v3(snapshot)


def test_snapshot_verification_rejects_directory_replaced_after_lstat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _sealed_snapshot(tmp_path)
    source_directory = snapshot.root / "src"
    outside_tree = tmp_path / "outside-src"
    shutil.copytree(source_directory, outside_tree)
    original_lstat = worker_v3.os.lstat
    original_open = worker_v3.os.open
    replaced = False

    def replace_directory() -> None:
        nonlocal replaced
        if replaced:
            return
        hidden_source = tmp_path / "original-src"
        source_directory.rename(hidden_source)
        source_directory.symlink_to(outside_tree, target_is_directory=True)
        replaced = True

    def lstat_then_replace(path: str | os.PathLike[str], *args, **kwargs):
        result = original_lstat(path, *args, **kwargs)
        if Path(path) == source_directory:
            replace_directory()
        return result

    def open_then_replace(path: str | os.PathLike[str], *args, **kwargs):
        if path == "src" and kwargs.get("dir_fd") is not None:
            replace_directory()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(worker_v3.os, "lstat", lstat_then_replace)
    monkeypatch.setattr(worker_v3.os, "open", open_then_replace)
    with pytest.raises(ValueError, match="snapshot entry"):
        verify_source_snapshot_v3(snapshot)
    assert replaced is True


def test_snapshot_capability_ignores_root_path_replacement_after_verification(
    tmp_path: Path,
) -> None:
    snapshot = _sealed_snapshot(tmp_path)
    original_main = read_source_snapshot_entry_v3(snapshot, "main.py")
    outside_tree = tmp_path / "outside-snapshot"
    try:
        verify_source_snapshot_v3(snapshot)
        shutil.copytree(snapshot.root, outside_tree)
        snapshot.root.rename(tmp_path / "sealed-snapshot")
        snapshot.root.symlink_to(outside_tree, target_is_directory=True)
        (outside_tree / "main.py").write_bytes(b"external-tree-replacement")

        assert read_source_snapshot_entry_v3(snapshot, "main.py") == original_main
        assert verify_source_snapshot_v3(snapshot) == snapshot
    finally:
        snapshot.close()


def test_snapshot_sealing_rejects_source_tree_replaced_after_metadata_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path / "inputs")
    fake_repo = tmp_path / "fake-repo"
    source_tree = fake_repo / "src" / "mage_ptcg"
    source_tree.mkdir(parents=True)
    (source_tree / "module.py").write_bytes(b"sealed-source")
    _write_minimal_cg_source(fake_repo)
    (fake_repo / "scripts").mkdir()
    (fake_repo / "scripts" / "test_sim.py").write_bytes(b"runner")
    (fake_repo / "main.py").write_bytes(b"baseline")
    outside_tree = tmp_path / "outside-mage-ptcg"
    shutil.copytree(source_tree, outside_tree)
    (outside_tree / "module.py").write_bytes(b"external-source")
    original_lstat = worker_v3.os.lstat
    original_open = worker_v3.os.open
    replaced = False

    def replace_source_tree() -> None:
        nonlocal replaced
        if replaced:
            return
        source_tree.rename(tmp_path / "original-mage-ptcg")
        source_tree.symlink_to(outside_tree, target_is_directory=True)
        replaced = True

    def lstat_then_replace(path: str | os.PathLike[str], *args, **kwargs):
        result = original_lstat(path, *args, **kwargs)
        if Path(path) == source_tree:
            replace_source_tree()
        return result

    def open_then_replace(path: str | os.PathLike[str], *args, **kwargs):
        if path == "mage_ptcg" and kwargs.get("dir_fd") is not None:
            replace_source_tree()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(worker_v3, "_repo_root", lambda: fake_repo)
    monkeypatch.setattr(worker_v3.os, "lstat", lstat_then_replace)
    monkeypatch.setattr(worker_v3.os, "open", open_then_replace)
    with pytest.raises(ValueError, match="source tree|symlink|snapshotted"):
        seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "stage")
    assert replaced is True


def test_profiles_freeze_pre_result_panel_and_exact_cardinality(tmp_path: Path) -> None:
    calibration = _plan(tmp_path / "calibration", "calibration")
    full = _plan(tmp_path / "full", "full")

    assert len(calibration.panel) == 6
    assert len(calibration.logical_games) == 24
    assert len(full.logical_games) == 384
    assert {game.arm for game in full.logical_games} == {"teacher", "rule-v0-baseline"}
    assert {game.repetition for game in full.logical_games} == set(range(8))
    assert {game.seat for game in full.logical_games} == {0, 1}
    assert not ({"teacher-a", "teacher-b"} & {item.opponent_id for item in full.panel})
    assert full.to_payload()["hard_teacher_confidence"] == {"status": "unavailable"}
    assert "quality_weight" not in json.dumps(full.to_payload())
    assert "policy_path" not in json.dumps(full.to_payload())

    teacher = next(game for game in full.logical_games if game.lane == "alakazam" and game.arm == "teacher")
    baseline = next(
        game for game in full.logical_games
        if game.lane == teacher.lane and game.arm == "rule-v0-baseline"
        and game.opponent.opponent_id == teacher.opponent.opponent_id
        and game.seat == teacher.seat and game.repetition == teacher.repetition
    )
    assert teacher.subject_deck_sha256 == baseline.subject_deck_sha256
    assert teacher.opponent == baseline.opponent
    assert teacher.engine_sha256 == baseline.engine_sha256
    assert teacher.source_commit_sha256 == baseline.source_commit_sha256
    assert teacher.environment_seed != baseline.environment_seed
    assert teacher.agent_sampling_seed != baseline.agent_sampling_seed


def test_fault_retry_is_retained_and_resume_does_not_rerun(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "inputs")
    output = tmp_path / "output"
    calls: list[tuple[str, int]] = []

    def runner(game, retry_index: int) -> AttemptObservationV3:
        calls.append((game.logical_game_id, retry_index))
        if retry_index == 0:
            return AttemptObservationV3.faulted(
                kind="engine_error", exception_class="RuntimeError", message="boom",
                source_exception="RuntimeError: boom", exit_code=9,
                traceback_sha256="b" * 64, elapsed_seconds=2.0,
            )
        return AttemptObservationV3.completed("win", elapsed_seconds=1.0)

    manifest = collect_teacher_quality_evidence_v3(plan=plan, output_dir=output, runner=runner)
    rows = read_attempt_ledger_v3(output / "attempts.jsonl", plan=plan)

    assert manifest["logical_games"] == 24
    assert manifest["attempts"] == 48
    assert manifest["attempt_faults"] == 24
    assert len(rows) == 48
    first, retry = rows[0], rows[1]
    assert first["logical_game_id"] == retry["logical_game_id"]
    assert first["retry_index"] == 0 and first["fault"]["exit_code"] == 9
    assert first["outcome"] is None
    assert retry["retry_index"] == 1 and retry["outcome"] == "win"
    assert retry["fault"] is None
    assert first["environment_seed"] == retry["environment_seed"]
    assert first["agent_sampling_seed"] == retry["agent_sampling_seed"]

    calls_before = len(calls)
    collect_teacher_quality_evidence_v3(
        plan=plan, output_dir=output,
        runner=lambda *_: pytest.fail("completed resume must not invoke runner"),
    )
    assert len(calls) == calls_before


def test_resume_rejects_noncanonical_or_extra_attempt_fields(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "inputs")
    output = tmp_path / "output"
    collect_teacher_quality_evidence_v3(
        plan=plan, output_dir=output,
        runner=lambda *_: AttemptObservationV3.completed("draw", elapsed_seconds=0.5),
    )
    ledger = output / "attempts.jsonl"
    row = json.loads(ledger.read_text().splitlines()[0])
    row["quality_weight"] = 1.0
    ledger.write_bytes(_canonical(row) + b"\n")

    with pytest.raises(ValueError, match="closed key set"):
        read_attempt_ledger_v3(ledger, plan=plan)


def test_live_collector_rejects_smoke_failed_subject_before_worker(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="smoke_ok"):
        build_live_attempt_runner_v3(plan=_smoke_failed_plan(tmp_path))


def test_live_runner_binds_snapshot_and_worker_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(tmp_path / "inputs")
    calls: list[dict[str, object]] = []

    def fake_worker(request_path: Path, *, timeout_seconds: float) -> dict[str, object]:
        request_raw = request_path.read_bytes()
        request = json.loads(request_raw)
        calls.append({"request": request, "timeout_seconds": timeout_seconds})
        return {
            "schema": "meta-specialist-teacher-quality-worker-response-v3",
            "request_sha256": _sha(request_raw),
            "snapshot_sha256": request["snapshot"]["manifest_sha256"],
            "rng": {"python": request["agent_sampling_seed"], "numpy": request["agent_sampling_seed"], "torch": request["agent_sampling_seed"]},
            "engine_randomness": "unattested",
            "outcome": "win",
            "game": {
                "engine_status": "DONE",
                "winner": request["subject_seat"],
                "subject_seat": request["subject_seat"],
                "subject_outcome": "win",
            },
            "elapsed_seconds": 0.25,
        }

    monkeypatch.setattr(evidence_v3, "run_teacher_quality_attempt_worker_v3", fake_worker)
    runner = build_live_attempt_runner_v3(
        plan=plan, transient_root=tmp_path / "transient", worker_timeout_seconds=7,
    )
    try:
        manifest = collect_teacher_quality_evidence_v3(
            plan=plan, output_dir=tmp_path / "output", runner=runner,
        )
        campaign = json.loads((tmp_path / "output" / "campaign.json").read_text())
        result = json.loads((tmp_path / "output" / "result.json").read_text())

        assert len(calls) == len(plan.logical_games)
        assert calls[0]["timeout_seconds"] == 7
        assert calls[0]["request"]["game"]["max_steps"] == 10_000
        assert calls[0]["request"]["game"]["environment_seed"] in {
            item.environment_seed for item in plan.logical_games
        }
        assert campaign["source_snapshot_file_sha256"] == manifest["source_snapshot_file_sha256"]
        assert campaign["source_snapshot_tree_sha256"] == manifest["source_snapshot_tree_sha256"]
        assert campaign["engine_seed_capability"] == "unattested"
        assert result["worker_provenance"] == manifest["worker_provenance"]
        assert manifest["worker_provenance"]["attempt_protocol"] == "fresh-worker-v3"
    finally:
        runner.close()


def test_live_runner_uses_presealed_dirty_source_after_original_mutates(tmp_path: Path) -> None:
    """The live preflight and worker must never re-read the source worktree."""
    plan = _bridge_plan(tmp_path / "inputs")
    snapshot = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "snapshots")
    game = next(item for item in plan.logical_games if item.arm == "teacher" and item.seat == 0)
    Path(game.teacher_instance.policy_path).write_text("raise RuntimeError('live worktree fallback')\n", encoding="utf-8")
    runner = build_live_attempt_runner_v3(plan=plan, source_snapshot=snapshot, max_steps=77)
    try:
        observation = runner(game, 0)
    finally:
        runner.close()
        snapshot.close()

    assert observation.outcome == "win"


def test_full_result_rederives_complete_strata_bootstrap_and_runtime(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "inputs", "full")
    output = tmp_path / "output"

    def runner(game, _retry_index: int) -> AttemptObservationV3:
        if game.arm == "teacher":
            outcome = "win" if game.repetition < 4 else "draw"
        else:
            outcome = "win" if game.repetition < 2 else "loss"
        return AttemptObservationV3.completed(outcome, elapsed_seconds=1.0 + game.seat)

    manifest = collect_teacher_quality_evidence_v3(plan=plan, output_dir=output, runner=runner)
    result = json.loads((output / "result.json").read_text())

    assert manifest["logical_games"] == 384
    assert manifest["attempts"] == 384
    assert manifest["ledger_sha256"] == _sha((output / "attempts.jsonl").read_bytes())
    assert result["status"] == "PERFORMANCE_EVIDENCE_ONLY"
    assert result["hard_teacher_confidence"] == {"status": "unavailable"}
    assert "quality_weight" not in json.dumps(result)
    assert result["bootstrap"] == {"seed": 20260809, "replicates": 20000}
    assert result["lanes"]["alakazam"]["macro_delta"] == pytest.approx(0.25)
    assert len(result["lanes"]["alakazam"]["strata"]) == 12
    assert result["runtime"]["p50_attempt_seconds"] == 1.5
    assert result["runtime"]["p95_attempt_seconds"] == 2.0


def test_calibration_is_not_claimed_as_complete_performance_evidence(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "inputs", "calibration")
    output = tmp_path / "output"

    manifest = collect_teacher_quality_evidence_v3(
        plan=plan, output_dir=output,
        runner=lambda *_: AttemptObservationV3.completed("draw", elapsed_seconds=0.5),
    )

    assert manifest["strata_complete"] is False
    with pytest.raises(ValueError, match="full performance evidence"):
        read_ready_teacher_quality_manifest_v3(
            output / "manifest.json",
            expected_manifest_file_sha256=_sha((output / "manifest.json").read_bytes()),
            expected_manifest_sha256=manifest["manifest_sha256"],
        )


def test_output_lock_rejects_a_concurrent_collector(tmp_path: Path) -> None:
    import fcntl
    import os

    plan = _plan(tmp_path / "inputs", "calibration")
    output = tmp_path / "output"
    output.mkdir()
    descriptor = os.open(output / ".teacher-quality-evidence-v3.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="already in use"):
            collect_teacher_quality_evidence_v3(
                plan=plan, output_dir=output,
                runner=lambda *_: AttemptObservationV3.completed("draw", elapsed_seconds=0.5),
            )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_output_root_replacement_during_campaign_fails_closed(tmp_path: Path) -> None:
    """The lock, ledger and manifest must remain bound to one output directory FD."""
    plan = _plan(tmp_path / "inputs", "calibration")
    output = tmp_path / "output"
    outside = tmp_path / "outside"
    outside.mkdir()
    replaced = False

    def runner(_game, _retry_index: int) -> AttemptObservationV3:
        nonlocal replaced
        if not replaced:
            output.rename(tmp_path / "original-output")
            output.symlink_to(outside, target_is_directory=True)
            replaced = True
        return AttemptObservationV3.completed("draw", elapsed_seconds=0.1)

    with pytest.raises(ValueError, match="output root.*changed|symlink"):
        collect_teacher_quality_evidence_v3(plan=plan, output_dir=output, runner=runner)
    assert replaced is True
    assert not (outside / "attempts.jsonl").exists()


@pytest.mark.parametrize(
    "leaf",
    ("result.json", "manifest.json"),
    ids=("result_leaf_symlink", "manifest_leaf_symlink"),
)
def test_collection_rejects_preexisting_final_evidence_leaf_symlink(
    tmp_path: Path, leaf: str,
) -> None:
    """A final evidence leaf must never replace a pre-existing symlink."""
    plan = _plan(tmp_path / "inputs", "calibration")
    output = tmp_path / "output"
    output.mkdir()
    outside_sentinel = tmp_path / f"outside-{leaf}"
    outside_sentinel.write_bytes(b"outside sentinel must remain unchanged")
    destination = output / leaf
    destination.symlink_to(outside_sentinel)

    with pytest.raises(ValueError, match="not a regular file|symlink"):
        collect_teacher_quality_evidence_v3(
            plan=plan,
            output_dir=output,
            runner=lambda *_: AttemptObservationV3.completed("draw", elapsed_seconds=0.1),
        )

    assert destination.is_symlink()
    assert outside_sentinel.read_bytes() == b"outside sentinel must remain unchanged"


@pytest.mark.parametrize("kind", ("directory", "fifo"), ids=("directory", "fifo"))
def test_atomic_write_rejects_non_regular_output_leaf(tmp_path: Path, kind: str) -> None:
    """A direct evidence publication must leave non-regular targets intact."""
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "result.json"
    if kind == "directory":
        destination.mkdir()
    else:
        os.mkfifo(destination)
    root = evidence_v3._OutputRootV3.open(output)
    try:
        with pytest.raises(ValueError, match="not a regular file|symlink"):
            root.atomic_write("result.json", b"replacement bytes")
    finally:
        root.close()

    mode = os.lstat(destination).st_mode
    assert stat.S_ISDIR(mode) if kind == "directory" else stat.S_ISFIFO(mode)


def test_atomic_write_rejects_destination_identity_change_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leaf replaced during the write cannot be silently overwritten."""
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "result.json"
    destination.write_bytes(b"original regular leaf")
    outside_sentinel = tmp_path / "outside-result"
    outside_sentinel.write_bytes(b"outside sentinel must remain unchanged")
    original_fsync = evidence_v3.os.fsync
    substituted = False

    def fsync_then_substitute(descriptor: int) -> None:
        nonlocal substituted
        original_fsync(descriptor)
        if not substituted:
            destination.unlink()
            destination.symlink_to(outside_sentinel)
            substituted = True

    monkeypatch.setattr(evidence_v3.os, "fsync", fsync_then_substitute)
    root = evidence_v3._OutputRootV3.open(output)
    try:
        with pytest.raises(ValueError, match="changed|not a regular file|symlink"):
            root.atomic_write("result.json", b"replacement bytes")
    finally:
        root.close()

    assert substituted is True
    assert destination.is_symlink()
    assert outside_sentinel.read_bytes() == b"outside sentinel must remain unchanged"


def test_default_live_runner_defers_policy_factory_import_to_fresh_worker(
    tmp_path: Path,
) -> None:
    """The collector must not construct an external policy in its own process."""
    plan = _plan(tmp_path / "inputs")
    runner = build_live_attempt_runner_v3(plan=plan)
    runner.close()


def test_cli_lane_parser_requires_exact_two_lane_inputs(tmp_path: Path) -> None:
    from scripts.run_meta_specialist_teacher_quality_evidence_v3 import _lane, main

    deck = tmp_path / "deck.csv"
    deck.write_text("1\n" * 60, encoding="utf-8")
    parsed = _lane(f"alakazam=teacher-a=r1={deck}")
    assert parsed.lane == "alakazam"
    with pytest.raises(SystemExit):
        main([
            "--profile", "calibration", "--output", str(tmp_path / "out"),
            "--lane", f"alakazam=teacher-a=r1={deck}",
            "--schedule-sha256", "0" * 64,
            "--pool-manifest-sha256", "1" * 64,
            "--engine-sha256", "2" * 64,
            "--source-commit-sha256", "3" * 64,
            "--plan-only",
        ])


def test_cli_plan_only_never_constructs_a_live_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import run_meta_specialist_teacher_quality_evidence_v3 as cli

    deck = tmp_path / "deck.csv"
    deck.write_text("1\n" * 60, encoding="utf-8")

    class Plan:
        def to_payload(self) -> dict[str, object]:
            return {"schema": "fixture-plan", "campaign_id": "fixture"}

    class Snapshot:
        file_sha256 = "4" * 64
        tree_sha256 = "5" * 64

        def close(self) -> None:
            return None

    monkeypatch.setattr(cli, "build_campaign_plan_v3", lambda **_kwargs: Plan())
    monkeypatch.setattr(cli, "seal_teacher_quality_source_snapshot_v3", lambda **_kwargs: Snapshot())
    monkeypatch.setattr(
        cli, "build_live_attempt_runner_v3",
        lambda **_kwargs: pytest.fail("--plan-only must not construct a live worker"),
    )

    assert cli.main([
        "--profile", "calibration", "--output", str(tmp_path / "out"),
        "--lane", f"alakazam=teacher-a=r-a={deck}",
        "--lane", f"archaludon=teacher-b=r-b={deck}",
        "--schedule-sha256", "0" * 64,
        "--pool-manifest-sha256", "1" * 64,
        "--engine-sha256", "2" * 64,
        "--source-commit", "a" * 40,
        "--source-commit-sha256", _sha(("a" * 40).encode()),
        "--plan-only",
    ]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "campaign_id": "fixture", "schema": "fixture-plan",
        "source_snapshot_file_sha256": "4" * 64,
        "source_snapshot_tree_sha256": "5" * 64,
    }


def test_cli_allows_dirty_source_only_through_sealed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import run_meta_specialist_teacher_quality_evidence_v3 as cli

    deck = tmp_path / "deck.csv"
    deck.write_text("1\n" * 60, encoding="utf-8")

    class Plan:
        def to_payload(self) -> dict[str, object]:
            return {"schema": "fixture-plan", "campaign_id": "dirty-fixture"}

    sealed: list[object] = []

    class Snapshot:
        file_sha256 = "6" * 64
        tree_sha256 = "7" * 64

        def close(self) -> None:
            sealed.append("closed")

    monkeypatch.setattr(cli, "build_campaign_plan_v3", lambda **_kwargs: Plan())
    monkeypatch.setattr(cli, "seal_teacher_quality_source_snapshot_v3", lambda **_kwargs: Snapshot())

    assert cli.main([
        "--profile", "calibration", "--output", str(tmp_path / "out"),
        "--lane", f"alakazam=teacher-a=r-a={deck}",
        "--lane", f"archaludon=teacher-b=r-b={deck}",
        "--schedule-sha256", "0" * 64,
        "--pool-manifest-sha256", "1" * 64,
        "--engine-sha256", "2" * 64,
        "--source-commit", "a" * 40,
        "--source-commit-sha256", _sha(("a" * 40).encode()),
        "--plan-only",
    ]) == 0

    assert json.loads(capsys.readouterr().out)["source_snapshot_file_sha256"] == "6" * 64
    assert sealed == ["closed"]
