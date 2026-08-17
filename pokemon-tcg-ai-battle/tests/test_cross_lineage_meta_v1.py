from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import CgBestKnownLoopError, build_fresh_meta_batch_v1
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.cross_lineage_meta_v1 import (
    CrossLineageMetaError,
    build_cross_lineage_split_v1,
    seal_cross_lineage_meta_v1,
)
from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import write_candidate_wrapper


def _source(tmp_path: Path, name: str, *, energy: int) -> Path:
    root = tmp_path / name
    payload = root / "payload"
    payload.mkdir(parents=True)
    (payload / "original_main.py").write_text("def agent(obs):\n    return []\n", encoding="utf-8")
    write_candidate_wrapper(name, payload, root / "main.py")
    cards = [energy] * 59 + [1247]
    deck_text = "\n".join(str(card) for card in cards) + "\n"
    (root / "deck.csv").write_text(deck_text, encoding="utf-8")
    policy_sha = hashlib.sha256((root / "main.py").read_bytes()).hexdigest()
    deck_sha = canonical_deck_sha256(cards)
    source_policy_sha = hashlib.sha256((payload / "original_main.py").read_bytes()).hexdigest()
    (root / "pool_manifest.json").write_text(
        json.dumps([
            {
                "id": name,
                "policy_hash": policy_sha,
                "source_policy_sha256": source_policy_sha,
                "canonical_deck_hash": deck_sha,
                "smoke_ok": True,
                "source": "test_sealed_source",
                "source_branch": f"test/{name}",
                "source_commit": "a" * 64,
                "usage_boundary": "local_eval_only",
            }
        ])
        + "\n",
        encoding="utf-8",
    )
    (root / "SOURCE.md").write_text(
        "\n".join(
            [
                "# Test source",
                f"- source policy SHA-256: `{source_policy_sha}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def _p1(tmp_path: Path) -> Path:
    root = tmp_path / "p1"
    root.mkdir()
    (root / "main.py").write_text("def agent(obs):\n    return []\n", encoding="utf-8")
    (root / "deck.csv").write_text("\n".join(["1"] * 59 + ["1247"]) + "\n", encoding="utf-8")
    return root


def test_cross_lineage_emits_new_pair_id_and_runtime_gate(tmp_path: Path) -> None:
    policy_a = _source(tmp_path, "policy_a", energy=1)
    policy_b = _source(tmp_path, "policy_b", energy=2)
    deck_c = _source(tmp_path, "deck_c", energy=3)
    current = tmp_path / "current" / "pool_manifest.json"
    current.parent.mkdir()
    current.write_text("[]\n", encoding="utf-8")

    report = seal_cross_lineage_meta_v1(
        policy_roots=(policy_a, policy_b),
        deck_roots=(policy_a, policy_b, deck_c),
        output_root=tmp_path / "generated",
        source_epoch="cross-test",
        seed_namespace="seed-test",
        p1_package=_p1(tmp_path),
        current_pool_manifest=current,
    )

    assert report["status"] == "SEALED"
    assert report["accepted_count"] == 4
    rows = json.loads((tmp_path / "generated" / "pool_manifest.json").read_text(encoding="utf-8"))
    assert all(row["smoke_ok"] is False for row in rows)
    assert all(row["source"] == "internal_cross_lineage_recombined" for row in rows)
    assert all((tmp_path / "generated" / row["id"] / "payload" / "original_main.py").is_file() for row in rows)
    assert len({(row["policy_hash"], row["canonical_deck_hash"]) for row in rows}) == 4
    with pytest.raises(CgBestKnownLoopError, match="not smoke-qualified"):
        build_fresh_meta_batch_v1(
            manifest_path=tmp_path / "generated" / "fresh_meta.json",
            pool_manifest_path=tmp_path / "generated" / "pool_manifest.json",
        )


def test_split_rebind_requires_smoke_and_binds_promoted_pool(tmp_path: Path) -> None:
    policy_a = _source(tmp_path, "policy_a", energy=1)
    policy_b = _source(tmp_path, "policy_b", energy=2)
    deck_c = _source(tmp_path, "deck_c", energy=3)
    generated = tmp_path / "generated"
    p1 = _p1(tmp_path)
    seal_cross_lineage_meta_v1(
        policy_roots=(policy_a, policy_b),
        deck_roots=(policy_a, policy_b, deck_c),
        output_root=generated,
        source_epoch="cross-test",
        seed_namespace="seed-test",
        p1_package=p1,
    )
    with pytest.raises(CrossLineageMetaError, match="after smoke promotion"):
        build_cross_lineage_split_v1(output_root=generated, p1_package=p1)

    promoted = tmp_path / "promoted"
    shutil.copytree(generated, promoted)
    pool_path = promoted / "pool_manifest.json"
    rows = json.loads(pool_path.read_text(encoding="utf-8"))
    for row in rows:
        row["smoke_ok"] = True
    pool_path.write_text(json.dumps(rows, sort_keys=True) + "\n", encoding="utf-8")
    fresh_path = promoted / "fresh_meta.json"
    fresh = json.loads(fresh_path.read_text(encoding="utf-8"))
    fresh["pool_manifest_sha256"] = hashlib.sha256(pool_path.read_bytes()).hexdigest()
    fresh_path.write_text(json.dumps(fresh, sort_keys=True) + "\n", encoding="utf-8")
    (promoted / "meta_manifest.json").unlink()
    (promoted / "cg_historical_split.json").unlink()

    rebound = build_cross_lineage_split_v1(output_root=promoted, p1_package=p1)
    assert rebound["status"] == "SEALED"
    split = load_weekend_split(promoted / "cg_historical_split.json")
    assert len(split.ids("META_TRAIN")) == 2
    assert len(split.ids("META_DEV")) == 1
    assert len(split.ids("META_FINAL")) == 1
