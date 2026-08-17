"""Phase 3 Audit trails, lineage graphs, config linter, and CLI connectivity tests.
"""

from __future__ import annotations

import tempfile
import json
from pathlib import Path
import pytest
import tarfile

from mage_ptcg.offline_training_v1_support.audit_log import AuditLogger
from mage_ptcg.offline_training_v1_support.lineage import LineageGraph
from mage_ptcg.offline_training_v1_support.config_lint import ConfigLinter
from mage_ptcg.offline_training_v1_support.reproducibility import ReproducibilityBundleManager
from mage_ptcg.offline_training_v1_support.contracts import SupportContractError
from mage_ptcg.offline_training_v1_support.fuzz import run_fuzz_tests
from mage_ptcg.offline_training_v1_support.scale_check import run_scale_check
from mage_ptcg.offline_training_v1_support.cli import main


def test_audit_logger_hash_chain():
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_file = Path(tmp_dir) / "audit.jsonl"
        logger = AuditLogger(log_file)

        logger.log_event("run-schedule", "user", "schedule", "s1", [], [], "SUCCESS", "Generated schedule")
        logger.log_event("mine-records", "user", "dataset", "d1", [], [], "SUCCESS", "Mined records")

        # Validate chain
        errors = logger.verify_chain()
        assert not errors

        # Tamper with file
        lines = log_file.read_text(encoding="utf-8").splitlines()
        # Change event_hash of first event
        evt = json.loads(lines[0])
        evt["event_hash"] = "invalid_hash_value"
        lines[0] = json.dumps(evt)
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Verify should detect mismatch
        errors_tampered = logger.verify_chain()
        assert len(errors_tampered) > 0


def test_lineage_graph_cycle_detection():
    g = LineageGraph()
    g.add_node("dataset_a", "dataset")
    g.add_node("model_b", "model")
    g.add_node("dataset_c", "dataset")

    g.add_edge("dataset_a", "model_b", "trained_on")
    g.add_edge("model_b", "dataset_c", "derived_from")

    # OK graph
    assert not g.find_cycles()
    sorted_nodes = g.get_topological_order()
    assert sorted_nodes[0] == "dataset_a"

    # Add cycle
    g.add_edge("dataset_c", "dataset_a", "derived_from")
    assert len(g.find_cycles()) > 0
    with pytest.raises(SupportContractError):
        g.get_topological_order()


def test_config_linter():
    linter = ConfigLinter()

    # Valid
    valid_cfg = {
        "schema_version": "support-config-v1",
        "seed": 42,
        "worker_count": 4,
        "split_ratios": {"train": 0.8, "val": 0.1, "test": 0.1},
    }
    res = linter.lint(valid_cfg)
    assert res["status"] == "VALID"

    # Invalid - missing field
    invalid_cfg = {
        "worker_count": 4,
    }
    res_inv = linter.lint(invalid_cfg)
    assert res_inv["status"] == "INVALID"
    assert len(res_inv["errors"]) > 0


def test_reproducibility_bundle_adversarial_cases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        manager = ReproducibilityBundleManager(tmp_path)

        metadata = {
            "resolved_config": {},
            "git_commit": "abc",
            "environment_summary": {},
            "dataset_manifest_summary": {},
            "model_manifest_summary": {},
            "evaluation_summary": {},
        }

        tar_out = tmp_path / "bundle.tar.gz"
        res = manager.assemble_bundle(tar_out, metadata)
        assert res["bundle_sha256"]

        # Test verification
        v_res = manager.verify_bundle(tar_out)
        assert v_res["valid"]

        # 1. Test duplicate members rejection
        # Manually create a corrupt tar file containing duplicate name members
        dup_tar = tmp_path / "dup_member.tar.gz"
        with tarfile.open(dup_tar, "w:gz") as tar:
            f1 = tmp_path / "f1.json"
            f1.write_text('{"a":1}', encoding="utf-8")
            tar.add(f1, arcname="bundle_manifest.json")
            tar.add(f1, arcname="bundle_manifest.json") # duplicate

        v_dup = manager.verify_bundle(dup_tar)
        assert not v_dup["valid"]
        assert any("Duplicate member" in err for err in v_dup["errors"])

        # 2. Test symlink rejection
        sym_tar = tmp_path / "sym_member.tar.gz"
        with tarfile.open(sym_tar, "w:gz") as tar:
            f1 = tmp_path / "f1.json"
            f1.write_text('{"a":1}', encoding="utf-8")
            tar.add(f1, arcname="bundle_manifest.json")
            # add symlink
            tarinfo = tarfile.TarInfo(name="dangerous_link")
            tarinfo.type = tarfile.SYMTYPE
            tarinfo.linkname = "bundle_manifest.json"
            tar.addfile(tarinfo)

        v_sym = manager.verify_bundle(sym_tar)
        assert not v_sym["valid"]
        assert any("Symlink or hardlink detected" in err for err in v_sym["errors"])


def test_fuzz_and_scale_units():
    fuzz_res = run_fuzz_tests(seed=123)
    assert fuzz_res["status"] == "SUCCESS"

    scale_res = run_scale_check(record_count=100)
    assert scale_res["status"] == "SUCCESS"


def test_cli_connectivity():
    # Verify we can execute the added commands via in-process main routing without crashes
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # census
        assert main(["census"]) == 0

        # traceability
        assert main(["traceability"]) == 0

        # fuzz
        assert main(["fuzz", "--seed", "42"]) == 0

        # scale-check
        assert main(["scale-check", "--records", "5"]) == 0

        # plan-evaluation
        assert main(["plan-evaluation", "--baseline-win-rate", "0.5", "--target-improvement", "0.05"]) == 0

        # config-lint
        cfg_file = tmp_path / "cfg.json"
        cfg_file.write_text('{"schema_version": "support-config-v1", "seed": 42}', encoding="utf-8")
        assert main(["config-lint", "--config", str(cfg_file)]) == 0

        # lineage
        assert main(["lineage"]) == 0
