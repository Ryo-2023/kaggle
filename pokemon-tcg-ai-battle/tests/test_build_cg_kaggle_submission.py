from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tarfile

from scripts import verify_kaggle_submission as verifier


_RUNTIME_MEMBERS = (
    "cg/__init__.py",
    "cg/api.py",
    "cg/libcg.so",
    "cg/sim.py",
    "cg/utils.py",
    "deck.csv",
    "main.py",
)


def _make_source_candidate(root: Path) -> Path:
    package = root / "package"
    package.mkdir(parents=True)
    files: dict[str, dict[str, object]] = {}
    for name in _RUNTIME_MEMBERS:
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = b"1\n" * 60 if name == "deck.csv" else b"x"
        if name == "main.py":
            data = b"from cg.api import all_attack\ndef agent(obs):\n    return []\n"
        path.write_bytes(data)
        files[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }

    archive_path = root / "submission.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name in _RUNTIME_MEMBERS:
            archive.add(package / name, arcname=name)
    archive_sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    deck_sha = hashlib.sha256((package / "deck.csv").read_bytes()).hexdigest()
    inner_manifest = {
        "schema_version": "meta-specialist-root-cg-policy-screen-v1-test",
        "archive": {
            "path": "submission.tar.gz",
            "sha256": archive_sha,
            "members": list(_RUNTIME_MEMBERS),
        },
        "files": files,
        "deck_sha256": deck_sha,
        "source_deck_sha256": deck_sha,
        "policy_source_sha256": hashlib.sha256((package / "main.py").read_bytes()).hexdigest(),
    }
    (root / "candidate_manifest.json").write_text(
        json.dumps(inner_manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def test_build_cg_package_materializes_standard_wrapper(tmp_path: Path) -> None:
    from scripts.build_cg_kaggle_submission import build_cg_package

    source = _make_source_candidate(tmp_path / "candidate")
    output = tmp_path / "wrapper"
    inner = build_cg_package(
        output,
        source_candidate=source,
        contract={
            "submission_method": "UNKNOWN",
            "archive_type": "UNKNOWN",
            "entrypoint": "main.py",
        },
        source_head="0" * 40,
    )

    assert inner["schema_version"].startswith("meta-specialist-root-cg-")
    manifest, snapshot = verifier._load_kaggle_package_manifest_snapshot(output)
    assert manifest["agent_kind"] == "cg"
    assert manifest["builder_result"] == inner
    assert snapshot.member_bytes("main.py").startswith(b"from cg.api")
    assert snapshot.member_bytes("submission.tar.gz")


def test_generic_kaggle_builder_accepts_cg_config(tmp_path: Path) -> None:
    from scripts.build_kaggle_submission import main

    source = _make_source_candidate(tmp_path / "candidate")
    output = tmp_path / "wrapper"
    config = tmp_path / "cg-config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "kaggle-agent-package-config-v1",
                "competition_slug": "pokemon-tcg-ai-battle",
                "agent_kind": "cg",
                "output_dir": str(output),
                "source_candidate": str(source),
                "contract": {
                    "submission_method": "UNKNOWN",
                    "archive_type": "UNKNOWN",
                    "entrypoint": "main.py",
                },
            }
        ),
        encoding="utf-8",
    )

    assert main(["--config", str(config)]) == 0
    manifest, _snapshot = verifier._load_kaggle_package_manifest_snapshot(output)
    assert manifest["agent_kind"] == "cg"
