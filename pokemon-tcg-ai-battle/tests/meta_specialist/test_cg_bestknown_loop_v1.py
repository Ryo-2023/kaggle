from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_alternating_runtime_v1 import (
    CG_DECK_FIXED_LONG_V1,
    CG_POLICY_FIXED_SHORT_V1,
    CgPackageSpecV1,
)
from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import (
    CgBestKnownLoopError,
    FRESH_META_SCHEMA_V1,
    build_fresh_meta_batch_v1,
    run_bestknown_loop_v1,
)
from src.mage_ptcg.observability.cabt_trace import canonical_deck_sha256


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package(tmp_path: Path, name: str, *, policy: str, card: int) -> CgPackageSpecV1:
    root = tmp_path / name / "package"
    root.mkdir(parents=True)
    (root / "main.py").write_text(policy, encoding="utf-8")
    (root / "deck.csv").write_text((f"{card}\n" * 60), encoding="utf-8")
    archive = root.parent / "submission.tar.gz"
    archive.write_bytes(f"archive-{name}".encode())
    manifest = root.parent / "candidate_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "candidate_id": name,
                "deck_sha256": _sha(root / "deck.csv"),
                "archive": {"path": "submission.tar.gz", "sha256": _sha(archive)},
                "policy_source_sha256": _sha(root / "main.py"),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return CgPackageSpecV1.from_package(root)


def _pool(tmp_path: Path, ids: tuple[str, ...]) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "pool"
    rows: list[dict[str, object]] = []
    policies: dict[str, str] = {}
    for index, opponent_id in enumerate(ids, start=1):
        asset = root / opponent_id
        asset.mkdir(parents=True)
        deck = asset / "deck.csv"
        deck.write_text((f"{index}\n" * 60), encoding="utf-8")
        policy = asset / "main.py"
        policy.write_text("def agent(obs): return []\n", encoding="utf-8")
        policy_sha = _sha(policy)
        policies[opponent_id] = policy_sha
        rows.append(
            {
                "id": opponent_id,
                "canonical_deck_hash": canonical_deck_sha256([index] * 60),
                "policy_hash": policy_sha,
                "smoke_ok": True,
                "source": "public",
                "usage_boundary": "local_eval_only",
            }
        )
    manifest = root / "pool_manifest.json"
    manifest.write_text(json.dumps(rows, sort_keys=True) + "\n", encoding="utf-8")
    return manifest, policies


def _fresh_manifest(
    tmp_path: Path,
    pool_manifest: Path,
    ids: tuple[str, ...],
    policies: dict[str, str],
) -> Path:
    pool_rows = {row["id"]: row for row in json.loads(pool_manifest.read_text(encoding="utf-8"))}
    evidence = tmp_path / "freshness-ledger.json"
    evidence.write_text(json.dumps({"ids": list(ids), "status": "unused"}, sort_keys=True) + "\n", encoding="utf-8")
    payload = {
        "schema_version": FRESH_META_SCHEMA_V1,
        "batch_id": "batch-20260815-a",
        "source_epoch": "source-epoch-1",
        "seed_namespace": "seed-namespace-1",
        "seed_plan_sha256": "b" * 64,
        "reference_ids": list(ids),
        "pool_manifest_sha256": _sha(pool_manifest),
        "freshness_basis": "new source ledger with no prior CABT artifact references",
        "references": [
            {
                "id": opponent_id,
                "fresh": True,
                "unused_before_run": True,
                "freshness_evidence_sha256": _sha(evidence),
                "freshness_evidence_path": evidence.name,
                "policy_sha256": policies[opponent_id],
                "canonical_deck_hash": pool_rows[opponent_id]["canonical_deck_hash"],
                "source": "public",
            }
            for opponent_id in ids
        ],
        "authority": {
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
            "longrun_allowed": False,
        },
        "research_only": True,
    }
    manifest = tmp_path / "fresh-meta.json"
    manifest.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def test_fresh_batch_verifies_canonical_identity_and_unused_ledger(tmp_path: Path) -> None:
    pool_manifest, policies = _pool(tmp_path, ("meta-a", "meta-b"))
    fresh_manifest = _fresh_manifest(tmp_path, pool_manifest, ("meta-a", "meta-b"), policies)

    batch = build_fresh_meta_batch_v1(
        manifest_path=fresh_manifest,
        pool_manifest_path=pool_manifest,
    )

    assert batch.reference_ids == ("meta-a", "meta-b")
    assert batch.to_dict()["research_only"] is True
    with pytest.raises(CgBestKnownLoopError, match="already consumed"):
        build_fresh_meta_batch_v1(
            manifest_path=fresh_manifest,
            pool_manifest_path=pool_manifest,
            consumed_ids=("meta-a",),
        )
    with pytest.raises(CgBestKnownLoopError, match="seed namespace already consumed"):
        build_fresh_meta_batch_v1(
            manifest_path=fresh_manifest,
            pool_manifest_path=pool_manifest,
            consumed_seed_namespaces=("seed-namespace-1",),
        )


def test_fresh_batch_rejects_raw_hash_in_canonical_field(tmp_path: Path) -> None:
    pool_manifest, policies = _pool(tmp_path, ("meta-a",))
    fresh_manifest = _fresh_manifest(tmp_path, pool_manifest, ("meta-a",), policies)
    payload = json.loads(fresh_manifest.read_text(encoding="utf-8"))
    payload["references"][0]["canonical_deck_hash"] = _sha(pool_manifest.parent / "meta-a" / "deck.csv")
    fresh_manifest.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(CgBestKnownLoopError, match="canonical deck hash"):
        build_fresh_meta_batch_v1(
            manifest_path=fresh_manifest,
            pool_manifest_path=pool_manifest,
        )


def test_bestknown_loop_alternates_policy_deck_policy_and_updates_parent(tmp_path: Path) -> None:
    pool_manifest, policies = _pool(tmp_path, ("meta-a", "meta-b"))
    fresh_manifest = _fresh_manifest(tmp_path, pool_manifest, ("meta-a", "meta-b"), policies)
    batch = build_fresh_meta_batch_v1(
        manifest_path=fresh_manifest,
        pool_manifest_path=pool_manifest,
    )
    parent = _package(tmp_path, "parent", policy="a\n", card=1)
    policy_child = _package(tmp_path, "policy-child", policy="b\n", card=1)
    deck_child = _package(tmp_path, "deck-child", policy="b\n", card=2)
    final_policy_child = _package(tmp_path, "final-policy-child", policy="c\n", card=2)
    candidates = {
        CG_DECK_FIXED_LONG_V1: policy_child,
        CG_POLICY_FIXED_SHORT_V1: deck_child,
    }
    calls: list[str] = []

    def runner(**kwargs: object) -> dict[str, object]:
        phase = str(kwargs["phase"])
        calls.append(phase)
        candidate = candidates[phase]
        return {
            "candidate": candidate,
            "summary": {
                "decision": "POSITIVE_CONTINUE",
                "faults": 0,
                "candidate_delta": 0.02,
                "candidate_seat_gap": 0.01,
            },
        }

    # The third cycle deliberately returns a policy child based on the deck
    # child; this verifies that the loop's incumbent, rather than the initial
    # parent, is passed to the runner.
    def runner_three_cycles(**kwargs: object) -> dict[str, object]:
        phase = str(kwargs["phase"])
        calls.append(phase)
        cycle = int(kwargs["cycle_index"])
        candidate = final_policy_child if cycle == 2 else candidates[phase]
        return {
            "candidate": candidate,
            "summary": {
                "decision": "POSITIVE_CONTINUE",
                "faults": 0,
                "candidate_delta": 0.02,
                "candidate_seat_gap": 0.01,
            },
        }

    result = run_bestknown_loop_v1(
        incumbent=parent,
        fresh_meta=batch,
        candidate_runner=runner_three_cycles,
        output_root=tmp_path / "loop",
        max_cycles=3,
        execute=True,
    )

    assert calls == [CG_DECK_FIXED_LONG_V1, CG_POLICY_FIXED_SHORT_V1, CG_DECK_FIXED_LONG_V1]
    assert result["status"] == "BOUNDARY"
    assert result["bestknown_candidate_id"] == "final-policy-child"
    assert result["consumed_reference_ids"] == ["meta-a", "meta-b"]
    assert len(result["checkpoints"]) == 3


def test_bestknown_loop_stops_without_promoting_faulted_candidate(tmp_path: Path) -> None:
    pool_manifest, policies = _pool(tmp_path, ("meta-a",))
    fresh_manifest = _fresh_manifest(tmp_path, pool_manifest, ("meta-a",), policies)
    batch = build_fresh_meta_batch_v1(
        manifest_path=fresh_manifest,
        pool_manifest_path=pool_manifest,
    )
    parent = _package(tmp_path, "parent", policy="a\n", card=1)
    candidate = _package(tmp_path, "candidate", policy="b\n", card=1)

    def runner(**kwargs: object) -> dict[str, object]:
        return {
            "candidate": candidate,
            "summary": {
                "decision": "INVALID_FAULT",
                "faults": 1,
                "candidate_delta": 0.2,
                "candidate_seat_gap": 0.0,
            },
        }

    result = run_bestknown_loop_v1(
        incumbent=parent,
        fresh_meta=batch,
        candidate_runner=runner,
        output_root=tmp_path / "loop",
        max_cycles=2,
        execute=True,
    )

    assert result["status"] == "STOP_FAULT"
    assert result["bestknown_candidate_id"] == "parent"
    assert result["last_decision"] == "INVALID_FAULT"
    assert len(result["checkpoints"]) == 1
