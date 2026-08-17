from __future__ import annotations

import json
from pathlib import Path

from scripts.build_self_owned_cg_policy_factorial_split_v1 import build_split_v1
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split


ROOT = Path(__file__).resolve().parents[1]
P1 = ROOT / "runs/cg-self-owned-cg-policy-cem-v2-p1-source/package"


def test_build_split_binds_smoke_promoted_factorial_pool(tmp_path: Path) -> None:
    source = tmp_path / "promoted"
    source.mkdir()
    rows = []
    references = []
    for index in range(3):
        source_id = f"factorial-{index}"
        rows.append(
            {
                "id": source_id,
                "policy_hash": f"{index + 1:064x}",
                "canonical_deck_hash": f"{index + 10:064x}",
                "source": "self_owned_official_card_data_deck_policy_factorial",
                "usage_boundary": "local_eval_only",
                "smoke_ok": True,
                "source_manifest_sha256": f"{index + 20:064x}",
            }
        )
        references.append(
            {
                "id": source_id,
                "fresh": True,
                "unused_before_run": True,
                "source_sha256": f"{index + 20:064x}",
            }
        )
    (source / "pool_manifest.json").write_text(json.dumps(rows), encoding="utf-8")
    (source / "fresh_meta.json").write_text(
        json.dumps(
            {
                "schema_version": "meta-specialist-cg-fresh-meta-batch-v1",
                "source_epoch": "test",
                "seed_namespace": "test",
                "references": references,
            }
        ),
        encoding="utf-8",
    )
    result = build_split_v1(output_root=source, p1_package=P1)
    assert result["train_count"] == 1
    assert result["dev_count"] == 1
    assert result["final_count"] == 1
    split = json.loads((source / "cg_self_owned_weekend_split.json").read_text(encoding="utf-8"))
    assert split["splits"]["META_TRAIN"][0]["training_exposure"] == 0
    assert split["bindings"]["p1_policy_sha256"] == "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"

