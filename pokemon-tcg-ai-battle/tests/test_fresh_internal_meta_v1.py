from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import (
    build_fresh_meta_batch_v1,
)
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.fresh_internal_meta_v1 import (
    FreshInternalMetaError,
    _static_findings,
    _strip_readonly_telemetry,
    seal_fresh_internal_meta_v1,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _repo(tmp_path: Path, *, policy: str | None = None, deck: str | None = None) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.parent.mkdir(parents=True, exist_ok=True)
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "data/raw").mkdir(parents=True)
    (repo / "data/raw/EN_Card_Data.csv").write_text(
        "\n".join(f"{index},card" for index in range(1, 61)) + "\n", encoding="utf-8"
    )
    (repo / "main.py").write_text(policy or "def agent(obs):\n    return []\n", encoding="utf-8")
    (repo / "deck.csv").write_text(deck or "\n".join(str(index) for index in range(1, 61)) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "data/raw/EN_Card_Data.csv", "main.py", "deck.csv"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)
    commit = _git(repo, "rev-parse", "HEAD")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/agents/test-agent", commit],
        cwd=repo,
        check=True,
    )
    return repo, commit


def _empty_pool(tmp_path: Path) -> Path:
    path = tmp_path / "pool" / "pool_manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]\n", encoding="utf-8")
    return path


def _run(repo: Path, pool: Path, output: Path, **kwargs: object) -> dict[str, object]:
    return seal_fresh_internal_meta_v1(
        repo=repo,
        pool_manifest_path=pool,
        output_root=output,
        source_epoch="internal-epoch-20260815",
        seed_namespace="internal-seed-1",
        scan_roots=(),
        **kwargs,
    )


def test_seal_pairs_assets_from_same_commit_and_builds_fresh_batch(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    pool = _empty_pool(tmp_path)
    output = tmp_path / "staged"

    report = _run(repo, pool, output)

    assert report["status"] == "SEALED"
    assert report["accepted_count"] == 1
    candidate_id = report["accepted_ids"][0]
    row = json.loads((output / "pool_manifest.json").read_text(encoding="utf-8"))[0]
    assert row["source_commit"] == commit
    assert row["source_branch"] == "agents/test-agent"
    assert row["source"] == "internal_agents"
    assert row["usage_boundary"] == "local_eval_only"
    policy = output / candidate_id / "main.py"
    deck = output / candidate_id / "deck.csv"
    assert hashlib.sha256(policy.read_bytes()).hexdigest() == row["policy_hash"]
    assert canonical_deck_sha256([int(value) for value in deck.read_text().split()]) == row["canonical_deck_hash"]
    assert _git(repo, "rev-parse", "HEAD") == commit

    fresh = output / "fresh_meta.json"
    batch = build_fresh_meta_batch_v1(manifest_path=fresh, pool_manifest_path=output / "pool_manifest.json")
    assert batch.reference_ids == (candidate_id,)
    assert batch.research_only is True


def test_reused_source_or_identity_is_rejected_and_reason_is_recorded(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    policy = (repo / "main.py").read_bytes()
    cards = [int(value) for value in (repo / "deck.csv").read_text().split()]
    pool = tmp_path / "pool" / "pool_manifest.json"
    pool.parent.mkdir(parents=True)
    pool.write_text(
        json.dumps(
            [
                {
                    "id": "old-agent",
                    "policy_hash": hashlib.sha256(policy).hexdigest(),
                    "canonical_deck_hash": canonical_deck_sha256(cards),
                    "source": "internal_agents",
                    "source_commit": commit,
                    "usage_boundary": "local_eval_only",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "staged"

    report = _run(repo, pool, output)

    assert report["status"] == "BLOCKED_NO_SAFE_CANDIDATES"
    assert report["accepted_count"] == 0
    reasons = report["rejections"]["test-agent"]
    assert {"source_commit_reused", "policy_identity_reused"} <= set(reasons)
    assert "deck_identity_reused" not in reasons
    assert (output / "intake_report.json").is_file()


def test_excluded_ref_and_unsafe_policy_fail_closed(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path, policy="import requests\ndef agent(obs):\n    return []\n")
    pool = _empty_pool(tmp_path)
    output = tmp_path / "staged"

    report = _run(repo, pool, output, excluded_refs=("refs/remotes/origin/agents/test-agent",))
    assert report["status"] == "BLOCKED_NO_SAFE_CANDIDATES"
    assert report["rejections"]["test-agent"] == ["ref_excluded"]

    repo2, _ = _repo(tmp_path / "unsafe", policy="import requests\ndef agent(obs):\n    return []\n")
    output2 = tmp_path / "staged-unsafe"
    report2 = _run(repo2, _empty_pool(tmp_path / "unsafe-pool"), output2)
    assert report2["rejections"]["test-agent"] == ["network_import"]


def test_output_is_no_clobber(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    pool = _empty_pool(tmp_path)
    output = tmp_path / "staged"
    _run(repo, pool, output)
    with pytest.raises(FileExistsError):
        _run(repo, pool, output)


def test_invalid_deck_is_rejected(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path, deck="\n".join(str(index) for index in range(1, 60)) + "\n")
    report = _run(repo, _empty_pool(tmp_path), tmp_path / "staged")
    assert report["rejections"]["test-agent"] == ["invalid_deck"]


def test_missing_root_asset_and_subprocess_are_rejected(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path, policy="import subprocess\ndef agent(obs):\n    return []\n")
    subprocess.run(["git", "rm", "-q", "deck.csv"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "remove deck"], cwd=repo, check=True)
    new_commit = _git(repo, "rev-parse", "HEAD")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/agents/test-agent", new_commit],
        cwd=repo,
        check=True,
    )
    report = _run(repo, _empty_pool(tmp_path), tmp_path / "staged")
    assert report["rejections"]["test-agent"] == ["missing_root_asset"]

    repo2, _ = _repo(tmp_path / "subprocess", policy="import subprocess\ndef agent(obs):\n    return []\n")
    report2 = _run(repo2, _empty_pool(tmp_path / "subprocess-pool"), tmp_path / "staged-subprocess")
    assert report2["rejections"]["test-agent"] == ["subprocess_import"]


def test_readonly_telemetry_patch_is_explicit_and_removes_only_file_side_effect(tmp_path: Path) -> None:
    policy = """import json\nimport os\n\nclass Runtime:\n    match_serial = 0\n    telemetry = []\n\n\ndef _shadow_telemetry(event: dict, runtime: Runtime = Runtime()) -> None:\n    record = dict(event)\n    record.setdefault(\"match_id\", runtime.match_serial)\n    runtime.telemetry.append(record)\n    path = os.environ.get(\"GRIMMSNARL_PLAN_TELEMETRY\")\n    if not path:\n        return\n    try:\n        with open(path, \"a\", encoding=\"utf-8\") as handle:\n            handle.write(json.dumps(record) + \"\\n\")\n    except Exception:\n        pass\n\n\ndef _raw_overage(obs: dict) -> float | None:\n    return None\n\n\ndef agent(obs):\n    return []\n"""
    patched, marker, count = _strip_readonly_telemetry(policy.encode("utf-8"))
    assert marker == "LOCAL_READONLY_TELEMETRY_V1"
    assert count == 1
    findings, _, environment_keys = _static_findings(patched.decode("utf-8"))
    assert findings == []
    assert environment_keys == ()
    assert b"runtime.telemetry.append(record)" in patched
    assert b"open(path, \"a\"" not in patched

    repo, _ = _repo(tmp_path / "telemetry", policy=policy)
    report = _run(
        repo,
        _empty_pool(tmp_path / "telemetry-pool"),
        tmp_path / "staged-telemetry",
        readonly_telemetry_refs=("refs/remotes/origin/agents/test-agent",),
    )
    assert report["status"] == "SEALED"
    candidate_id = str(report["accepted_ids"][0])
    evidence = json.loads((tmp_path / "staged-telemetry" / "evidence" / f"{candidate_id}.json").read_text())
    assert evidence["readonly_telemetry_patch"] == marker


def test_readonly_telemetry_patch_does_not_apply_without_explicit_ref(tmp_path: Path) -> None:
    policy = """import os\n\ndef _shadow_telemetry(event, runtime):\n    path = os.environ.get(\"GRIMMSNARL_PLAN_TELEMETRY\")\n    with open(path, \"a\") as handle:\n        handle.write(\"x\")\n\ndef agent(obs):\n    return []\n"""
    repo, _ = _repo(tmp_path, policy=policy)
    report = _run(repo, _empty_pool(tmp_path), tmp_path / "staged")
    assert report["rejections"]["test-agent"] == ["filesystem_write"]


def test_opt_in_first_parent_history_seals_distinct_snapshots_without_checkout(tmp_path: Path) -> None:
    repo, first_commit = _repo(tmp_path)
    (repo / "main.py").write_text("def agent(obs):\n    return [1]\n", encoding="utf-8")
    subprocess.run(["git", "add", "main.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second snapshot"], cwd=repo, check=True)
    second_commit = _git(repo, "rev-parse", "HEAD")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/agents/test-agent", second_commit],
        cwd=repo,
        check=True,
    )
    before = _git(repo, "rev-parse", "HEAD")

    report = _run(
        repo,
        _empty_pool(tmp_path / "history-pool"),
        tmp_path / "history-staged",
        include_refs=("refs/remotes/origin/agents/test-agent",),
        history_depth=2,
        max_candidates=2,
    )

    assert report["status"] == "SEALED"
    assert report["accepted_count"] == 2
    assert report["history_depth"] == 2
    rows = json.loads((tmp_path / "history-staged" / "pool_manifest.json").read_text())
    assert {row["source_commit"] for row in rows} == {first_commit, second_commit}
    assert _git(repo, "rev-parse", "HEAD") == before
