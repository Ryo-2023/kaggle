from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import build_fresh_meta_batch_v1
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.derived_internal_meta_v1 import (
    DerivedInternalMetaError,
    _replace_rocket_theta,
    seal_derived_internal_meta_v1,
)


def _base(tmp_path: Path) -> Path:
    root = tmp_path / "base"
    root.mkdir()
    main = """_THETA_GENERAL = {\"x\": 1}\n_THETA_LUCMIX = {\"x\": 2}\n_THETA_A09_MERGED = {\"x\": 3}\n_THETA_A07_MERGED = {\"x\": 4}\n_THETA_ABOMASNOW_R2 = {\"x\": 5}\nPARAMS = {}\nfor _param_name, _param_value in _THETA_GENERAL.items():\n    PARAMS[_param_name] = _param_value\n\ndef agent(obs):\n    return []\n"""
    deck = "\n".join(str(index) for index in range(1, 61)) + "\n"
    (root / "main.py").write_text(main, encoding="utf-8")
    (root / "deck.csv").write_text(deck, encoding="utf-8")
    source_sha = hashlib.sha256(main.encode()).hexdigest()
    deck_sha = hashlib.sha256(deck.encode()).hexdigest()
    canonical = canonical_deck_sha256(list(range(1, 61)))
    (root / "SOURCE.md").write_text(
        "\n".join(
            [
                "# Internal source snapshot",
                "",
                "- branch: `agents/test-rocket`",
                "- commit: `0123456789abcdef0123456789abcdef01234567`",
                f"- source policy SHA-256: `{source_sha}`",
                f"- staged policy SHA-256: `{source_sha}`",
                f"- deck bytes SHA-256: `{deck_sha}`",
                f"- canonical deck SHA-256: `{canonical}`",
                "- localization patch: `NONE` (0 replacement(s))",
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
    (root / "deck.csv").write_text("\n".join(str(index) for index in range(1, 61)) + "\n", encoding="utf-8")
    return root


def test_fixed_recipe_is_exact_and_fail_closed() -> None:
    source = b"_THETA_GENERAL = {}\n_THETA_LUCMIX = {}\nfor _param_name, _param_value in _THETA_GENERAL.items():\n    pass\n"
    transformed, recipe = _replace_rocket_theta(source, "LUCMIX")
    assert recipe == "ROCKET_THETA_SELECTION_V1:LUCMIX"
    assert b"_THETA_LUCMIX.items()" in transformed
    with pytest.raises(DerivedInternalMetaError):
        _replace_rocket_theta(source, "UNKNOWN")
    with pytest.raises(DerivedInternalMetaError):
        _replace_rocket_theta(source.replace(b"for _param_name", b"for other"), "LUCMIX")


def test_seal_builds_loadable_pool_fresh_batch_and_custom_split(tmp_path: Path) -> None:
    base = _base(tmp_path)
    current_pool = tmp_path / "current" / "pool_manifest.json"
    current_pool.parent.mkdir()
    current_pool.write_text("[]\n", encoding="utf-8")
    output = tmp_path / "derived"

    report = seal_derived_internal_meta_v1(
        base_root=base,
        output_root=output,
        source_epoch="derived-epoch",
        seed_namespace="derived-seed",
        current_pool_manifest=current_pool,
        p1_package=_p1(tmp_path),
    )

    assert report["status"] == "SEALED"
    assert report["accepted_count"] == 5
    pool = load_opponent_pool_v1(output)
    assert len(pool) == 5
    batch = build_fresh_meta_batch_v1(
        manifest_path=output / "fresh_meta.json",
        pool_manifest_path=output / "pool_manifest.json",
    )
    assert len(batch.reference_ids) == 5
    split = load_weekend_split(output / "cg_derived_split.json")
    assert len(split.ids("META_TRAIN")) == 2
    assert len(split.ids("META_DEV")) == 1
    assert len(split.ids("META_FINAL")) == 2
    assert set(split.ids("META_TRAIN")).isdisjoint(split.ids("META_DEV"))
    assert set(split.ids("META_TRAIN")).isdisjoint(split.ids("META_FINAL"))


def test_existing_policy_hash_is_not_accepted_as_base(tmp_path: Path) -> None:
    base = _base(tmp_path)
    policy_sha = hashlib.sha256((base / "main.py").read_bytes()).hexdigest()
    current_pool = tmp_path / "current" / "pool_manifest.json"
    current_pool.parent.mkdir()
    current_pool.write_text(f"[{{\"id\":\"old\",\"policy_hash\":\"{policy_sha}\"}}]\n", encoding="utf-8")
    with pytest.raises(DerivedInternalMetaError, match="already present"):
        seal_derived_internal_meta_v1(
            base_root=base,
            output_root=tmp_path / "derived",
            source_epoch="derived-epoch",
            seed_namespace="derived-seed",
            current_pool_manifest=current_pool,
            p1_package=_p1(tmp_path),
        )
