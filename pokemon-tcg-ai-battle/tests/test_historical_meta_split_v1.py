from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from scripts.build_historical_meta_split_v1 import build_historical_meta_split_v1


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_historical_split_binds_exact_fresh_pool_partition(tmp_path: Path) -> None:
    root = tmp_path / "historical"
    root.mkdir()
    ids = ["hist-a", "hist-b", "hist-c"]
    pool = []
    for index, opponent_id in enumerate(ids):
        pool.append(
            {
                "id": opponent_id,
                "policy_hash": _sha(f"policy-{index}"),
                "canonical_deck_hash": _sha(f"deck-{index}"),
                "source_policy_sha256": _sha(f"source-{index}"),
                "source_branch": "agents/test",
                "source_commit": f"{index + 1:040x}",
                "smoke_ok": True,
                "source": "internal_agents",
                "usage_boundary": "local_eval_only",
            }
        )
    (root / "pool_manifest.json").write_text(json.dumps(pool) + "\n", encoding="utf-8")
    (root / "fresh_meta.json").write_text(
        json.dumps({"references": [{"id": opponent_id} for opponent_id in ids]}) + "\n",
        encoding="utf-8",
    )
    p1 = tmp_path / "p1"
    p1.mkdir()
    (p1 / "main.py").write_text("def agent(obs):\n    return []\n", encoding="utf-8")
    (p1 / "deck.csv").write_text("\n".join(str(index) for index in range(1, 61)) + "\n", encoding="utf-8")

    report = build_historical_meta_split_v1(
        pool_root=root,
        fresh_meta_path=root / "fresh_meta.json",
        p1_package=p1,
        train_ids=[ids[0]],
        dev_ids=[ids[1]],
        final_ids=[ids[2]],
    )

    assert report["status"] == "SEALED"
    split = load_weekend_split(root / "cg_historical_split.json")
    assert split.ids("META_TRAIN") == (ids[0],)
    assert split.ids("META_DEV") == (ids[1],)
    assert split.ids("META_FINAL") == (ids[2],)
    assert json.loads((root / "meta_manifest.json").read_text())["rows"][0]["training_exposure"] == 0
