from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_self_owned_cg_deck_v1 import main
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import verify_self_owned_cg_package_v1


ROOT = Path(__file__).resolve().parents[2]
CARD_DB = ROOT / "data/raw/EN_Card_Data.csv"
SPEC = ROOT / "configs/meta_specialist/self_owned_cg_deck_spec_v1.json"
SPEC_V2 = ROOT / "configs/meta_specialist/self_owned_cg_deck_spec_v2.json"
SOURCE_PACKAGE = ROOT / "runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1"


def test_cli_requires_explicit_execute(tmp_path, capsys):
    output = tmp_path / "candidate"
    status = main(
        [
            "--output",
            str(output),
            "--card-db",
            str(CARD_DB),
            "--spec",
            str(SPEC),
            "--source-package",
            str(SOURCE_PACKAGE),
        ]
    )
    assert status == 2
    assert not output.exists()
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED_EXECUTE_REQUIRED"


def test_cli_materializes_self_owned_candidate(tmp_path, capsys):
    output = tmp_path / "candidate"
    public_scan = tmp_path / "empty-public-scan"
    public_scan.mkdir()
    status = main(
        [
            "--execute",
            "--output",
            str(output),
            "--seed",
            "20260816",
            "--ordinal",
            "3",
            "--card-db",
            str(CARD_DB),
            "--spec",
            str(SPEC),
            "--source-package",
            str(SOURCE_PACKAGE),
            "--public-scan-root",
            str(public_scan),
        ]
    )
    assert status == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "COMPLETE"
    manifest = json.loads((output / "generation_manifest.json").read_text(encoding="utf-8"))
    assert manifest["parent_deck"] is None
    assert manifest["public_parent_read"] is False
    assert manifest["public_collision_count"] == 0
    assert manifest["authority"]["submission_allowed"] is False
    package_manifest = verify_self_owned_cg_package_v1(output / "package")
    assert package_manifest["canonical_deck_sha256"] == manifest["canonical_deck_sha256"]
    assert (output / "deck-artifact/deck.csv").read_bytes() == (output / "package/deck.csv").read_bytes()


def test_cli_refuses_existing_output(tmp_path, capsys):
    output = tmp_path / "candidate"
    output.mkdir()
    (output / "sentinel").write_text("keep", encoding="utf-8")
    status = main(
        [
            "--execute",
            "--output",
            str(output),
            "--card-db",
            str(CARD_DB),
            "--spec",
            str(SPEC),
            "--source-package",
            str(SOURCE_PACKAGE),
        ]
    )
    assert status == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED"
    assert (output / "sentinel").read_text(encoding="utf-8") == "keep"


def test_v2_role_spec_produces_a_distinct_official_data_deck(tmp_path, capsys):
    output = tmp_path / "candidate-v2"
    public_scan = tmp_path / "empty-public-scan"
    public_scan.mkdir()
    status = main(
        [
            "--execute",
            "--output",
            str(output),
            "--seed",
            "20260830",
            "--ordinal",
            "0",
            "--card-db",
            str(CARD_DB),
            "--spec",
            str(SPEC_V2),
            "--source-package",
            str(SOURCE_PACKAGE),
            "--public-scan-root",
            str(public_scan),
        ]
    )
    assert status == 0
    result = json.loads(capsys.readouterr().out)
    assert result["canonical_deck_sha256"] != "2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19"
    assert result["public_collision_count"] == 0
    assert json.loads((output / "generation_manifest.json").read_text(encoding="utf-8"))["parent_deck"] is None
