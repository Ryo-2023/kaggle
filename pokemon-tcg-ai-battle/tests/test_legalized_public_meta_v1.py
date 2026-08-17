from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import CgBestKnownLoopError
from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import build_fresh_meta_batch_v1
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.legalized_public_meta_v1 import (
    DeckRepairSpec,
    ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_V1,
    ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_SINGLE_ARG_V1,
    seal_legalized_public_meta_v1,
)


def _source(tmp_path: Path, name: str = "source") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "main.py").write_text("def agent(obs):\n    return []\n", encoding="utf-8")
    # Deliberately no ACE SPEC: the repair must add exactly one explicitly.
    (root / "deck.csv").write_text("\n".join(["1"] * 59 + ["2"]) + "\n", encoding="utf-8")
    return root


def _pool(tmp_path: Path) -> Path:
    path = tmp_path / "current" / "pool_manifest.json"
    path.parent.mkdir()
    path.write_text("[]\n", encoding="utf-8")
    return path


def test_seal_writes_hash_bound_repaired_source_and_fresh_meta(tmp_path: Path) -> None:
    source = _source(tmp_path)
    output = tmp_path / "sealed"
    spec = DeckRepairSpec(
        candidate_id="legalized-source",
        source_root=source,
        replacements=({"index": 59, "old": 2, "new": 1247},),
        source_ref="owner/source",
        source_commit="tar-sha",
    )

    report = seal_legalized_public_meta_v1(
        specs=(spec,),
        current_pool_manifest=_pool(tmp_path),
        output_root=output,
        source_epoch="legalized-test",
        seed_namespace="legalized-seed",
    )

    assert report["status"] == "SEALED"
    row = json.loads((output / "pool_manifest.json").read_text(encoding="utf-8"))[0]
    cards = [int(value) for value in (output / spec.candidate_id / "deck.csv").read_text().split()]
    assert len(cards) == 60
    assert cards.count(1247) == 1
    assert row["canonical_deck_hash"] == canonical_deck_sha256(cards)
    evidence = json.loads((output / "evidence" / f"{spec.candidate_id}.json").read_text())
    assert evidence["repair_recipe"] == "EXPLICIT_POSITION_REPLACEMENT_V1"
    assert (output / spec.candidate_id / "payload" / "original_main.py").is_file()
    with pytest.raises(CgBestKnownLoopError, match="not smoke-qualified"):
        build_fresh_meta_batch_v1(
            manifest_path=output / "fresh_meta.json",
            pool_manifest_path=output / "pool_manifest.json",
        )


def test_repair_fails_closed_when_declared_old_card_is_stale(tmp_path: Path) -> None:
    report = seal_legalized_public_meta_v1(
        specs=(
            DeckRepairSpec(
                candidate_id="stale",
                source_root=_source(tmp_path),
                replacements=({"index": 59, "old": 999, "new": 1247},),
            ),
        ),
        current_pool_manifest=_pool(tmp_path),
        output_root=tmp_path / "sealed-stale",
        source_epoch="epoch",
        seed_namespace="seed",
    )
    assert report["accepted_count"] == 0
    assert "old card mismatch" in report["rejections"]["stale"][0]


def test_repair_fails_closed_without_exactly_one_ace_spec(tmp_path: Path) -> None:
    source = _source(tmp_path)
    report = seal_legalized_public_meta_v1(
        specs=(
            DeckRepairSpec(
                candidate_id="still-invalid",
                source_root=source,
                replacements=({"index": 59, "old": 2, "new": 1247}, {"index": 58, "old": 1, "new": 1249}),
            ),
        ),
        current_pool_manifest=_pool(tmp_path),
        output_root=tmp_path / "sealed-invalid",
        source_epoch="epoch",
        seed_namespace="seed",
    )
    assert report["accepted_count"] == 0
    assert "exactly one ACE SPEC" in report["rejections"]["still-invalid"][0]


def test_initial_deck_contract_adapter_is_hash_bound_and_returns_repaired_deck(tmp_path: Path) -> None:
    source = _source(tmp_path)
    output = tmp_path / "sealed-adapter"
    spec = DeckRepairSpec(
        candidate_id="legalized-adapter",
        source_root=source,
        replacements=({"index": 59, "old": 2, "new": 1247},),
        entrypoint_adapter=ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_V1,
    )

    report = seal_legalized_public_meta_v1(
        specs=(spec,),
        current_pool_manifest=_pool(tmp_path),
        output_root=output,
        source_epoch="adapter-test",
        seed_namespace="adapter-seed",
    )

    assert report["status"] == "SEALED"
    evidence = json.loads((output / "evidence" / "legalized-adapter.json").read_text())
    assert evidence["entrypoint_adapter"] == ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_V1
    wrapper = (output / "legalized-adapter" / "main.py").read_text(encoding="utf-8")
    assert "_SEALED_DECK" in wrapper
    assert "observation.get(\"select\", _MISSING) is None" in wrapper


def test_single_arg_adapter_does_not_forward_configuration_to_legacy_agent(tmp_path: Path) -> None:
    source = _source(tmp_path)
    output = tmp_path / "sealed-single-arg"
    report = seal_legalized_public_meta_v1(
        specs=(
            DeckRepairSpec(
                candidate_id="legacy-single-arg",
                source_root=source,
                replacements=({"index": 59, "old": 2, "new": 1247},),
                entrypoint_adapter=ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_SINGLE_ARG_V1,
            ),
        ),
        current_pool_manifest=_pool(tmp_path),
        output_root=output,
        source_epoch="single-arg-test",
        seed_namespace="single-arg-seed",
    )
    assert report["status"] == "SEALED"
    evidence = json.loads((output / "evidence" / "legacy-single-arg.json").read_text())
    assert evidence["entrypoint_adapter"] == ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_SINGLE_ARG_V1
    import importlib.util

    wrapper_path = output / "legacy-single-arg" / "main.py"
    module_spec = importlib.util.spec_from_file_location("legacy_single_arg_wrapper", wrapper_path)
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    assert module.agent({"select": None, "logs": [], "current": None}, {"episodeSteps": 1})
    assert module.agent({"select": {"option": [], "minCount": 0, "maxCount": 0}}, {"episodeSteps": 1}) == []
