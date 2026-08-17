"""Static contract for the pre-registered public confidence/OOD policy."""

from __future__ import annotations

import json
from pathlib import Path


def test_policy_manifest_is_hash_bound_and_fail_closed() -> None:
    path = Path(__file__).resolve().parents[2] / "configs/meta_specialist/public_confidence_ood_policy_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "meta-specialist-public-confidence-ood-policy-v1"
    assert payload["promotion_authority"] is False
    assert payload["source"]["reference_artifact_sha256"] == "7dcf1cef7639a6a8b14ffcf6591bf3808fb7bed8edd85d82da369b3b6f511cda"
    assert payload["source"]["reference_source_list_sha256"] == "b21c329a2ab599ba80e02294052a19ed4d770ac0a7335fc6197f33e3567206cb"
    assert payload["source"]["reference_source_sha256s"] == [
        "2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce",
        "2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26",
    ]
    assert payload["source"]["reference_source_count"] == 2
    assert payload["bucket_policy"] == {"rare_count_threshold": 2, "focus_on_ood": True}
    assert payload["confidence_policy"] == {"min_normalized_surprisal": 0.5, "max_top1_top2_margin": None}
    assert payload["loss_mask_semantics"] == {
        "forced_domain": "context_only",
        "ineligible": "context_only",
        "eligible": "loss_bearing",
        "context_rows_in_loss_denominator": False,
        "context_rows_advance_recurrent_state": True,
    }
    assert payload["gate"]["longrun_allowed"] is False
