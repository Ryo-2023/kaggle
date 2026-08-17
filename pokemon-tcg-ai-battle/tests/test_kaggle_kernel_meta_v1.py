from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from dataclasses import asdict
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import (
    KaggleKernelMetaError,
    KernelSourceSpec,
    _entrypoint_reason,
    load_candidate_agent,
    safe_extract_kernel_tar,
    scan_source_text,
    seal_kaggle_kernel_meta_v1,
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _deck_text(start: int = 1) -> str:
    return "\n".join(str(value) for value in range(start, start + 60)) + "\n"


def _tar(path: Path, members: dict[str, bytes], *, symlink: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if symlink is not None:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "main.py"
            archive.addfile(info)
    return path


def _spec(tmp_path: Path, candidate_id: str, *, policy: str | None = None, deck: str | None = None, tar_path: Path | None = None) -> KernelSourceSpec:
    policy_bytes = (policy or "def agent(obs):\n    return []\n").encode("utf-8")
    deck_bytes = (deck or _deck_text()).encode("utf-8")
    path = tar_path or _tar(
        tmp_path / f"{candidate_id}.tar.gz",
        {"main.py": policy_bytes, "deck.csv": deck_bytes, "helper.py": b"VALUE = 1\n"},
    )
    return KernelSourceSpec(
        candidate_id=candidate_id,
        kernel_ref=f"owner/{candidate_id}",
        source_url=f"https://www.kaggle.com/code/owner/{candidate_id}",
        tar_path=path,
        tar_sha256=_sha256_bytes(path.read_bytes()),
        fetched_at_utc="2026-08-15T00:00:00Z",
    )


def _pool(tmp_path: Path, rows: list[dict[str, object]] | None = None) -> Path:
    path = tmp_path / "pool" / "pool_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows or []) + "\n", encoding="utf-8")
    return path


def test_rejects_tar_path_traversal_and_symlink(tmp_path: Path) -> None:
    traversal = _spec(tmp_path, "traversal", tar_path=_tar(
        tmp_path / "traversal.tar.gz",
        {"../escape.py": b"x", "main.py": b"def agent(obs): return []\n", "deck.csv": _deck_text().encode()},
    ))
    with pytest.raises(KaggleKernelMetaError, match="unsafe tar member"):
        safe_extract_kernel_tar(traversal, tmp_path / "out-traversal" / "payload")

    linked = _spec(tmp_path, "linked", tar_path=_tar(
        tmp_path / "linked.tar.gz",
        {"main.py": b"def agent(obs): return []\n", "deck.csv": _deck_text().encode()},
        symlink="link.py",
    ))
    with pytest.raises(KaggleKernelMetaError, match="link"):
        safe_extract_kernel_tar(linked, tmp_path / "out-linked" / "payload")


def test_rejects_static_network_write_and_dynamic_import_but_allows_list_remove() -> None:
    assert "network_import" in scan_source_text("import requests\ndef agent(obs): return []")[0]
    assert "filesystem_write" in scan_source_text("from pathlib import Path\nPath('x').write_text('x')")[0]
    assert "dynamic_import" in scan_source_text("import importlib\nimportlib.import_module('x')")[0]
    assert scan_source_text("def agent(obs):\n  xs=[1]\n  xs.remove(1)\n  return []")[0] == []


def test_accepts_explicit_agent_reexport_without_accepting_module_import() -> None:
    assert _entrypoint_reason("from agent import agent\n") is None
    assert _entrypoint_reason("from helper import agent as agent\n") is None
    assert _entrypoint_reason("import agent\n") == "missing_agent_entrypoint"
    assert _entrypoint_reason("from agent import helper\n") == "missing_agent_entrypoint"


def test_seal_writes_hash_bound_local_eval_pool_and_fresh_meta(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "candidate-a")
    output = tmp_path / "sealed"
    report = seal_kaggle_kernel_meta_v1(
        specs=[spec],
        pool_manifest_path=_pool(tmp_path),
        output_root=output,
        source_epoch="kaggle-public-20260815-a",
        seed_namespace="kernel-seed-a",
    )

    assert report["status"] == "SEALED"
    row = json.loads((output / "pool_manifest.json").read_text(encoding="utf-8"))[0]
    assert row["usage_boundary"] == "local_eval_only"
    policy_path = output / "candidate-a" / "main.py"
    assert row["policy_hash"] == _sha256_bytes(policy_path.read_bytes())
    deck = [int(value) for value in (output / "candidate-a" / "deck.csv").read_text().split()]
    assert row["canonical_deck_hash"] == canonical_deck_sha256(deck)
    fresh = json.loads((output / "fresh_meta.json").read_text(encoding="utf-8"))
    assert fresh["references"][0]["fresh"] is True
    assert fresh["references"][0]["unused_before_run"] is True
    assert fresh["authority"]["submission_allowed"] is False
    assert (output / "candidate-a" / "payload" / "original_main.py").is_file()


def test_seal_rejects_duplicate_identity_within_batch(tmp_path: Path) -> None:
    shared = _tar(
        tmp_path / "shared.tar.gz",
        {"main.py": b"def agent(obs): return []\n", "deck.csv": _deck_text().encode()},
    )
    first = _spec(tmp_path, "candidate-a", tar_path=shared)
    second = _spec(tmp_path, "candidate-b", tar_path=shared)
    report = seal_kaggle_kernel_meta_v1(
        specs=[first, second],
        pool_manifest_path=_pool(tmp_path),
        output_root=tmp_path / "sealed",
        source_epoch="epoch",
        seed_namespace="seed",
    )
    assert report["accepted_ids"] == ["candidate-a"]
    assert report["rejections"]["candidate-b"] == ["batch_identity_reused"]


def test_wrapper_loads_two_candidates_without_payload_module_collision(tmp_path: Path) -> None:
    first_payload = tmp_path / "pool" / "one" / "payload"
    second_payload = tmp_path / "pool" / "two" / "payload"
    for payload, value in ((first_payload, 11), (second_payload, 22)):
        payload.mkdir(parents=True)
        (payload / "helper.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
        (payload / "original_main.py").write_text(
            "from helper import VALUE\n"
            "def agent(obs):\n"
            "    return [VALUE]\n",
            encoding="utf-8",
        )
        wrapper = payload.parent / "main.py"
        from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import write_candidate_wrapper

        write_candidate_wrapper(payload.parent.name, payload, wrapper)

    first = load_candidate_agent(tmp_path / "pool" / "one" / "main.py")
    second = load_candidate_agent(tmp_path / "pool" / "two" / "main.py")
    assert first({}) == [11]
    assert second({}) == [22]


def test_wrapper_does_not_forward_configuration_to_one_argument_payload(tmp_path: Path) -> None:
    payload = tmp_path / "one" / "payload"
    payload.mkdir(parents=True)
    (payload / "original_main.py").write_text(
        "def agent(observation):\n"
        "    return [observation.get('value', 0)]\n",
        encoding="utf-8",
    )
    wrapper = payload.parent / "main.py"
    from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import write_candidate_wrapper

    write_candidate_wrapper("one", payload, wrapper)
    agent = load_candidate_agent(wrapper)
    assert agent({"value": 7}, {"configuration": True}) == [7]


def test_wrapper_forwards_configuration_to_two_argument_payload(tmp_path: Path) -> None:
    payload = tmp_path / "two" / "payload"
    payload.mkdir(parents=True)
    (payload / "original_main.py").write_text(
        "def agent(observation, configuration=None):\n"
        "    return [configuration['value']]\n",
        encoding="utf-8",
    )
    wrapper = payload.parent / "main.py"
    from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import write_candidate_wrapper

    write_candidate_wrapper("two", payload, wrapper)
    agent = load_candidate_agent(wrapper)
    assert agent({}, {"value": 9}) == [9]


def test_output_is_no_clobber_and_current_pool_identity_is_rejected(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "candidate-a")
    first = seal_kaggle_kernel_meta_v1(
        specs=[spec], pool_manifest_path=_pool(tmp_path), output_root=tmp_path / "first",
        source_epoch="epoch-a", seed_namespace="seed-a",
    )
    row = json.loads((tmp_path / "first" / "pool_manifest.json").read_text(encoding="utf-8"))[0]
    with pytest.raises(FileExistsError):
        seal_kaggle_kernel_meta_v1(
            specs=[spec], pool_manifest_path=_pool(tmp_path), output_root=tmp_path / "first",
            source_epoch="epoch-b", seed_namespace="seed-b",
        )
    existing = _pool(tmp_path / "existing", [row])
    report = seal_kaggle_kernel_meta_v1(
        specs=[spec], pool_manifest_path=existing, output_root=tmp_path / "second",
        source_epoch="epoch-c", seed_namespace="seed-c",
    )
    assert report["status"] == "BLOCKED_NO_SAFE_CANDIDATES"
    assert "policy_identity_reused" in report["rejections"]["candidate-a"]


def test_seal_rejects_source_policy_matching_legacy_pool_policy_hash(tmp_path: Path) -> None:
    policy = "def agent(obs):\n    return []\n"
    source_sha = _sha256_bytes(policy.encode("utf-8"))
    spec = _spec(tmp_path, "candidate-legacy", policy=policy, deck=_deck_text(start=100))
    existing = _pool(tmp_path / "legacy", [{"id": "legacy-source", "policy_hash": source_sha}])

    report = seal_kaggle_kernel_meta_v1(
        specs=[spec],
        pool_manifest_path=existing,
        output_root=tmp_path / "sealed-legacy",
        source_epoch="epoch-legacy",
        seed_namespace="seed-legacy",
    )

    assert report["accepted_ids"] == []
    assert "source_identity_reused" in report["rejections"]["candidate-legacy"]


def test_seal_rejects_source_policy_found_in_prior_artifact_root(tmp_path: Path) -> None:
    policy = "def agent(obs):\n    return []\n"
    source_sha = _sha256_bytes(policy.encode("utf-8"))
    spec = _spec(tmp_path, "candidate-prior", policy=policy, deck=_deck_text(start=200))
    prior = tmp_path / "prior"
    prior.mkdir()
    (prior / "evidence.json").write_text(
        json.dumps({"source_policy_sha256": source_sha}) + "\n", encoding="utf-8"
    )

    report = seal_kaggle_kernel_meta_v1(
        specs=[spec],
        pool_manifest_path=_pool(tmp_path / "current"),
        output_root=tmp_path / "sealed-prior",
        source_epoch="epoch-prior",
        seed_namespace="seed-prior",
        scan_roots=[prior],
    )

    assert report["accepted_ids"] == []
    assert "source_identity_reused" in report["rejections"]["candidate-prior"]


def test_seal_rejects_more_than_one_ace_spec_from_local_card_catalog(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    card_catalog = repo / "data" / "raw" / "EN_Card_Data.csv"
    card_catalog.parent.mkdir(parents=True)
    rows = [
        "Card ID,Card Name,Expansion,Collection No.,Stage (Pokémon)/Type (Energy and Trainer),Rule,Category",
        *[f"{card_id},Card {card_id},SET,{card_id},Item,n/a,n/a" for card_id in range(1, 61)],
        "1100,Energy Search Pro,SSP,176,Item,ACE SPEC,n/a",
    ]
    card_catalog.write_text("\n".join(rows) + "\n", encoding="utf-8")
    deck = [1100, 1100, 1100, 1100, *range(1, 57)]
    spec = _spec(tmp_path, "ace-spec-overflow", deck="\n".join(str(card) for card in deck) + "\n")
    pool = repo / "opponents" / "pool_manifest.json"
    pool.parent.mkdir(parents=True)
    pool.write_text("[]\n", encoding="utf-8")

    report = seal_kaggle_kernel_meta_v1(
        specs=[spec],
        pool_manifest_path=pool,
        output_root=tmp_path / "sealed-ace-spec",
        source_epoch="epoch-ace-spec",
        seed_namespace="seed-ace-spec",
    )

    assert report["accepted_ids"] == []
    assert report["rejections"]["ace-spec-overflow"] == ["invalid_ace_spec_count"]


def test_seal_allows_new_policy_over_deck_seen_in_prior_artifact(tmp_path: Path) -> None:
    old_policy = "def agent(obs):\n    return []\n"
    new_policy = "def agent(obs):\n    return [1]\n"
    deck = _deck_text(start=300)
    prior = tmp_path / "prior-deck"
    prior.mkdir()
    (prior / "manifest.json").write_text(
        json.dumps({
            "canonical_deck_hash": canonical_deck_sha256([int(v) for v in deck.split()]),
            "policy_sha256": _sha256_bytes(old_policy.encode("utf-8")),
        }) + "\n",
        encoding="utf-8",
    )
    spec = _spec(tmp_path, "candidate-new-policy", policy=new_policy, deck=deck)

    report = seal_kaggle_kernel_meta_v1(
        specs=[spec],
        pool_manifest_path=_pool(tmp_path / "current-deck"),
        output_root=tmp_path / "sealed-new-policy",
        source_epoch="epoch-new-policy",
        seed_namespace="seed-new-policy",
        scan_roots=[prior],
    )

    assert report["accepted_ids"] == ["candidate-new-policy"]


def test_cli_dry_run_accepts_hash_bound_config(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "candidate-cli")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "source_epoch": "epoch-cli",
        "seed_namespace": "seed-cli",
        "pool_manifest": str(_pool(tmp_path / "current")),
        "output_root": str(tmp_path / "output"),
        "sources": [{**asdict(spec), "tar_path": str(spec.tar_path)}],
    }), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "scripts/generate_kaggle_kernel_meta_v1.py", "--config", str(config), "--dry-run"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["network_access"] is False


def test_seal_supports_explicit_non_agent_entrypoint_without_changing_source_bytes(tmp_path: Path) -> None:
    source = b"def mega_lopunny_cleanroom_entrypoint(observation):\n    return [7]\n"
    tar_path = _tar(
        tmp_path / "alias.tar.gz",
        {"main.py": source, "deck.csv": _deck_text().encode("utf-8")},
    )
    spec = KernelSourceSpec(
        candidate_id="alias-candidate",
        kernel_ref="owner/alias-candidate",
        source_url="https://www.kaggle.com/code/owner/alias-candidate",
        tar_path=tar_path,
        tar_sha256=_sha256_bytes(tar_path.read_bytes()),
        fetched_at_utc="2026-08-16T00:00:00Z",
        entrypoint_name="mega_lopunny_cleanroom_entrypoint",
    )

    output = tmp_path / "sealed-alias"
    report = seal_kaggle_kernel_meta_v1(
        specs=[spec],
        pool_manifest_path=_pool(tmp_path),
        output_root=output,
        source_epoch="kaggle-public-alias-20260816",
        seed_namespace="alias-seed",
    )

    assert report["accepted_ids"] == ["alias-candidate"]
    candidate = output / "alias-candidate"
    assert (candidate / "payload" / "original_main.py").read_bytes() == source
    evidence = json.loads((output / "evidence" / "alias-candidate.json").read_text(encoding="utf-8"))
    assert evidence["entrypoint_name"] == "mega_lopunny_cleanroom_entrypoint"
    assert evidence["source_policy_sha256"] == _sha256_bytes(source)
    agent = load_candidate_agent(candidate / "main.py")
    assert agent({}) == [7]


def test_seal_rejects_explicit_entrypoint_with_multiple_required_arguments(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        "bad-alias",
        policy="def mega_lopunny_cleanroom_entrypoint(observation, configuration):\n    return []\n",
    )
    spec = KernelSourceSpec(
        candidate_id=spec.candidate_id,
        kernel_ref=spec.kernel_ref,
        source_url=spec.source_url,
        tar_path=spec.tar_path,
        tar_sha256=spec.tar_sha256,
        fetched_at_utc=spec.fetched_at_utc,
        entrypoint_name="mega_lopunny_cleanroom_entrypoint",
    )

    report = seal_kaggle_kernel_meta_v1(
        specs=[spec],
        pool_manifest_path=_pool(tmp_path),
        output_root=tmp_path / "sealed-bad-alias",
        source_epoch="alias-bad",
        seed_namespace="alias-bad-seed",
    )

    assert report["accepted_ids"] == []
    assert report["rejections"]["bad-alias"] == ["invalid_entrypoint_signature"]
