from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import CgBestKnownLoopError
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from mage_ptcg.opponent_ingest.self_owned_failure_adapter_v1 import (
    FailureAdapterMetaError,
    VARIANT_IDS,
    build_failure_adapter_split_v1,
    render_failure_adapter_variant_v1,
    seal_failure_adapter_meta_v1,
)
from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import scan_source_text


BASE_SOURCE = """\nfrom cg.api import OptionType\n\ndef _mine(obs):\n    return obs.current.players[obs.current.yourIndex]\n\ndef _opponent(obs):\n    return obs.current.players[1 - obs.current.yourIndex]\n\ndef _energy_count(card):\n    return len(getattr(card, 'energyCards', []) or [])\n\ndef _available_attack_damage(option):\n    return int(getattr(option, 'damage', 0) or 0)\n\ndef _main_score(obs, option):\n    return 0\n\ndef _score(obs, option):\n    return _main_score(obs, option)\n\ndef agent(obs_dict):\n    return []\n"""


def _package(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "main.py").write_text(BASE_SOURCE, encoding="utf-8")
    (root / "deck.csv").write_text("\n".join(["1"] * 59 + ["1247"]) + "\n", encoding="utf-8")
    return root


def test_variants_are_deterministic_and_public_state_only() -> None:
    for variant in VARIANT_IDS:
        first = render_failure_adapter_variant_v1(BASE_SOURCE.encode("utf-8"), variant)
        second = render_failure_adapter_variant_v1(BASE_SOURCE.encode("utf-8"), variant)
        assert first == second
        assert variant.encode("utf-8") in first
        findings, _imports = scan_source_text(first.decode("utf-8"))
        assert findings == []
        assert b"private" not in first.lower()


def test_unknown_or_incomplete_base_fails_closed() -> None:
    with pytest.raises(FailureAdapterMetaError, match="unknown failure adapter"):
        render_failure_adapter_variant_v1(BASE_SOURCE.encode("utf-8"), "unknown")
    with pytest.raises(FailureAdapterMetaError, match="lacks adapter contract"):
        render_failure_adapter_variant_v1(b"def agent(obs):\n    return []\n", VARIANT_IDS[0])


def test_seal_and_rebind_require_runtime_smoke(tmp_path: Path) -> None:
    source = _package(tmp_path, "p1-source")
    p1 = _package(tmp_path, "p1")
    generated = tmp_path / "generated"
    report = seal_failure_adapter_meta_v1(
        source_package=source,
        output_root=generated,
        source_epoch="failure-adapter-test",
        seed_namespace="seed-test",
        p1_package=p1,
    )
    assert report["accepted_count"] == 4
    rows = json.loads((generated / "pool_manifest.json").read_text(encoding="utf-8"))
    assert len(rows) == 4
    assert all(row["smoke_ok"] is False for row in rows)
    assert len({row["policy_hash"] for row in rows}) == 4
    with pytest.raises(CgBestKnownLoopError, match="not smoke-qualified"):
        from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import build_fresh_meta_batch_v1

        build_fresh_meta_batch_v1(
            manifest_path=generated / "fresh_meta.json",
            pool_manifest_path=generated / "pool_manifest.json",
        )
    with pytest.raises(FailureAdapterMetaError, match="after smoke promotion"):
        build_failure_adapter_split_v1(output_root=generated, p1_package=p1)

    promoted = tmp_path / "promoted"
    shutil.copytree(generated, promoted)
    pool_path = promoted / "pool_manifest.json"
    promoted_rows = json.loads(pool_path.read_text(encoding="utf-8"))
    for row in promoted_rows:
        row["smoke_ok"] = True
    pool_path.write_text(json.dumps(promoted_rows, sort_keys=True) + "\n", encoding="utf-8")
    fresh_path = promoted / "fresh_meta.json"
    fresh = json.loads(fresh_path.read_text(encoding="utf-8"))
    fresh["pool_manifest_sha256"] = hashlib.sha256(pool_path.read_bytes()).hexdigest()
    fresh_path.write_text(json.dumps(fresh, sort_keys=True) + "\n", encoding="utf-8")

    rebound = build_failure_adapter_split_v1(output_root=promoted, p1_package=p1)
    assert rebound["status"] == "SEALED"
    split = load_weekend_split(promoted / "cg_failure_adapter_split.json")
    assert len(split.ids("META_TRAIN")) == 2
    assert len(split.ids("META_DEV")) == 1
    assert len(split.ids("META_FINAL")) == 1
