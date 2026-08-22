"""Fail-closed tests for the immutable Recipe C selection protocol."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import subprocess
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import biohub.recipe_c.protocol as protocol_module
from biohub.recipe_c.protocol import (
    PANEL_V1,
    ExperimentSpec,
    build_selection_lock,
    canonical_lock_json,
    recompute_selection_lock_id,
    validate_selection_lock,
    validate_selection_lock_payload,
    write_selection_lock,
)
from biohub.recipe_c.source import RECIPE_C_SOURCE
from biohub.reproducibility.digest import directory_digest_report
from biohub.reproducibility.gt_guard import prediction_manifest_path


def _source_receipt() -> dict[str, object]:
    return {
        "source_url": RECIPE_C_SOURCE.source_url,
        "source_commit": RECIPE_C_SOURCE.source_commit,
        "license": RECIPE_C_SOURCE.license,
        "license_relative_path": RECIPE_C_SOURCE.license_relative_path,
        "license_sha256": RECIPE_C_SOURCE.license_sha256,
        "config_relative_path": RECIPE_C_SOURCE.config_relative_path,
        "config_sha256": RECIPE_C_SOURCE.config_sha256,
        "notebook_relative_path": RECIPE_C_SOURCE.notebook_relative_path,
        "notebook_sha256": RECIPE_C_SOURCE.notebook_sha256,
        "predictor_relative_path": RECIPE_C_SOURCE.predictor_relative_path,
        "predictor_sha256": RECIPE_C_SOURCE.predictor_sha256,
        "primary_checkpoint_relative_path": RECIPE_C_SOURCE.primary_checkpoint_relative_path,
        "primary_checkpoint_sha256": RECIPE_C_SOURCE.primary_checkpoint_sha256,
        "secondary_checkpoint_relative_path": RECIPE_C_SOURCE.secondary_checkpoint_relative_path,
        "secondary_checkpoint_sha256": RECIPE_C_SOURCE.secondary_checkpoint_sha256,
        "secondary_staging_relative_path": RECIPE_C_SOURCE.secondary_staging_relative_path,
        "primary_dataset": RECIPE_C_SOURCE.primary_dataset,
        "primary_dataset_version": RECIPE_C_SOURCE.primary_dataset_version,
        "primary_dataset_license": RECIPE_C_SOURCE.primary_dataset_license,
        "secondary_dataset": RECIPE_C_SOURCE.secondary_dataset,
        "secondary_dataset_version": RECIPE_C_SOURCE.secondary_dataset_version,
        "secondary_dataset_license": RECIPE_C_SOURCE.secondary_dataset_license,
    }


def _experiment(**changes: object) -> ExperimentSpec:
    values: dict[str, object] = {
        "experiment_id": "exp_recipe_c_001",
        "method_family": "recipe_c_unet_transformer_ilp",
        "hypothesis": "dual seed logits improve temporal edge calibration",
        "expected_gain": 0.05,
        "cost": "one fixed panel run",
        "risk": "compute cost without score gain",
        "novelty": "public recipe adaptation",
        "changes": "blend the two pinned public checkpoints",
        "control_id": "control_recipe_c_v1",
        "acceptance_criteria": "all five samples complete and macro improves",
        "prior_evidence_receipt_hash": None,
    }
    values.update(changes)
    return ExperimentSpec(**values)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "recipe-c.yaml"
    canonical = Path(__file__).parents[1] / "configs" / "biohub_095_recipe_c.yaml"
    path.write_bytes(canonical.read_bytes())
    return path


@pytest.fixture
def valid_lock(config_path: Path) -> dict[str, object]:
    return build_selection_lock(
        _source_receipt(),
        config_path,
        "a" * 40,
        "auto",
        _experiment(),
    )


def test_panel_v1_is_exactly_ordered() -> None:
    assert PANEL_V1 == (
        "44b6_0113de3b",
        "44b6_0b24845f",
        "44b6_0c582fdc",
        "44b6_0db75fae",
        "44b6_12dfb391",
    )


def test_experiment_spec_is_frozen_and_rejects_empty_or_nonfinite() -> None:
    spec = _experiment()
    with pytest.raises(FrozenInstanceError):
        spec.experiment_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="empty"):
        _experiment(hypothesis=" ")
    with pytest.raises(ValueError, match="finite"):
        _experiment(expected_gain=float("nan"))


def test_selection_lock_rejects_changed_panel(valid_lock: dict[str, object]) -> None:
    panel = valid_lock["panel"]
    assert isinstance(panel, dict)
    panel["sample_ids"] = list(PANEL_V1[:-1])
    with pytest.raises(ValueError, match="PANEL_V1"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_rejects_reordered_panel(valid_lock: dict[str, object]) -> None:
    panel = valid_lock["panel"]
    assert isinstance(panel, dict)
    panel["sample_ids"] = list(reversed(PANEL_V1))
    with pytest.raises(ValueError, match="PANEL_V1"):
        validate_selection_lock_payload(valid_lock)


@pytest.mark.parametrize(
    "field", ("ground_truth_used_for_prediction", "ground_truth_used_for_parameter_fitting")
)
def test_selection_lock_rejects_forbidden_gt_usage(valid_lock: dict[str, object], field: str) -> None:
    usage = valid_lock["ground_truth_usage"]
    assert isinstance(usage, dict)
    usage[field] = True
    with pytest.raises(ValueError, match=r"ground truth|GT"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_recomputes_id(valid_lock: dict[str, object]) -> None:
    experiment = valid_lock["experiment"]
    assert isinstance(experiment, dict)
    experiment["hypothesis"] = "post-hoc mutation"
    with pytest.raises(ValueError, match="selection_lock_id"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_id_is_canonical_sha256(valid_lock: dict[str, object]) -> None:
    expected = hashlib.sha256(canonical_lock_json(valid_lock, without_id=True).encode()).hexdigest()
    assert valid_lock["selection_lock_id"] == expected
    assert recompute_selection_lock_id(valid_lock) == expected


def test_source_identity_uses_direct_keys_not_aliases(config_path: Path) -> None:
    source = _source_receipt()
    source["source"] = {"commit": source.pop("source_commit")}
    with pytest.raises(ValueError, match="source_commit"):
        build_selection_lock(source, config_path, "a" * 40, "cpu", _experiment())


def test_config_hash_is_raw_bytes(config_path: Path, valid_lock: dict[str, object]) -> None:
    original = valid_lock["config_sha256"]
    config_path.write_bytes(b"inference: {edge_threshold: 0.4}\n")
    assert original == RECIPE_C_SOURCE.config_sha256
    with pytest.raises(ValueError, match=r"config.*RECIPE_C_SOURCE|pinned config"):
        build_selection_lock(_source_receipt(), config_path, "a" * 40, "cpu", _experiment())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_selection_lock_rejects_nonfinite_payload(valid_lock: dict[str, object], value: float) -> None:
    experiment = valid_lock["experiment"]
    assert isinstance(experiment, dict)
    experiment["expected_gain"] = value
    with pytest.raises(ValueError, match=r"finite|canonical|NaN|Inf"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_rejects_unknown_field(valid_lock: dict[str, object]) -> None:
    valid_lock["unexpected_field"] = "must fail"
    with pytest.raises(ValueError, match=r"unknown|unexpected"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_rejects_absolute_and_credential_paths(valid_lock: dict[str, object]) -> None:
    valid_lock["config_relative_path"] = "/Users/private/.kaggle/kaggle.json"
    with pytest.raises(ValueError, match=r"path|credential|absolute"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_is_write_once_and_reread(tmp_path: Path, valid_lock: dict[str, object]) -> None:
    path = write_selection_lock(tmp_path / "selection_lock.json", valid_lock)
    assert validate_selection_lock(path) == valid_lock
    with pytest.raises(FileExistsError):
        write_selection_lock(path, valid_lock)
    assert path.read_text(encoding="utf-8") == canonical_lock_json(valid_lock)


@pytest.mark.parametrize("existing_kind", ["file", "directory", "symlink"])
def test_selection_lock_does_not_replace_existing_target(
    tmp_path: Path, valid_lock: dict[str, object], existing_kind: str
) -> None:
    path = tmp_path / "selection_lock.json"
    if existing_kind == "file":
        path.write_text("sentinel", encoding="utf-8")
    elif existing_kind == "directory":
        path.mkdir()
    else:
        target = tmp_path / "target"
        target.write_text("sentinel", encoding="utf-8")
        path.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_selection_lock(path, valid_lock)


def _strong_prior_receipt(tmp_path: Path, *, future: bool = False) -> dict[str, object]:
    base = datetime.now(UTC) - timedelta(minutes=10)
    if future:
        base = datetime.now(UTC) + timedelta(hours=1)
    samples: list[dict[str, object]] = []
    for index, sample_id in enumerate(PANEL_V1):
        prediction = tmp_path / "predictions" / sample_id
        prediction.mkdir(parents=True)
        (prediction / "prediction.bin").write_bytes(f"prediction-{sample_id}".encode())
        report = directory_digest_report(prediction)
        manifest_path = prediction_manifest_path(prediction)
        manifest_created = base + timedelta(seconds=index * 3)
        persisted = manifest_created + timedelta(seconds=1)
        gt_opened = persisted + timedelta(seconds=1)
        manifest = {
            "prediction_path": str(prediction),
            "directory_sha256": report["directory_sha256"],
            "files": report["files"],
            "total_bytes": report["total_bytes"],
            "ground_truth_included": False,
            "manifest_created_at": manifest_created.isoformat(),
        }
        _write_canonical_receipt(manifest_path, manifest)
        samples.append(
            {
                "sample_id": sample_id,
                "prediction_path": str(prediction),
                "prediction_manifest_path": str(manifest_path),
                "prediction_directory_sha256": report["directory_sha256"],
                "prediction_files": report["files"],
                "prediction_total_bytes": report["total_bytes"],
                "prediction_manifest_created_at": manifest_created.isoformat(),
                "prediction_persisted_at": persisted.isoformat(),
                "ground_truth_opened_at": gt_opened.isoformat(),
                "ordering_enforced_by": "biohub.reproducibility.gt_guard.open_ground_truth",
            },
        )
    return {
        "schema_version": 1,
        "receipt_type": "panel_evaluation",
        "panel": {"panel_id": "PANEL_V1", "sample_ids": list(PANEL_V1)},
        "ground_truth_used_for_prediction": False,
        "ground_truth_used_for_parameter_fitting": False,
        "ground_truth_usage_scope": "post_prediction_analysis_only",
        "gt_guard": samples,
    }


def _write_canonical_receipt(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    return path


def test_prior_evidence_requires_strong_panel_ordering_schema(config_path: Path) -> None:
    weak = {"final_score": 0.9, "sample_ids": list(PANEL_V1)}
    with pytest.raises(ValueError, match=r"prior|schema|PANEL_V1"):
        build_selection_lock(_source_receipt(), config_path, "a" * 40, "cpu", _experiment(), [weak])


def test_prior_evidence_rejects_in_memory_self_claim(config_path: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"persisted|JSON path|receipt"):
        build_selection_lock(
            _source_receipt(),
            config_path,
            "a" * 40,
            "cpu",
            _experiment(),
            [_strong_prior_receipt(tmp_path)],
        )


def test_prior_evidence_sets_post_prediction_scope_without_copying_receipt(
    config_path: Path, tmp_path: Path,
) -> None:
    strong = _strong_prior_receipt(tmp_path)
    strong["metrics"] = {"final_score": 0.9}
    receipt_path = _write_canonical_receipt(tmp_path / "prior.json", strong)
    lock = build_selection_lock(
        _source_receipt(), config_path, "a" * 40, "cpu", _experiment(), [receipt_path]
    )
    usage = lock["ground_truth_usage"]
    assert isinstance(usage, dict)
    assert usage["ground_truth_used_for_method_family_selection"] is True
    assert usage["ground_truth_usage_scope"] == "post_prediction_analysis_only"
    assert "metrics" not in json.dumps(lock)
    assert "prediction_manifest_path" not in json.dumps(lock)
    assert str(tmp_path) not in json.dumps(lock)


def test_prior_evidence_revalidates_real_prediction_and_manifest(
    config_path: Path, tmp_path: Path,
) -> None:
    strong = _strong_prior_receipt(tmp_path)
    guard = strong["gt_guard"]
    assert isinstance(guard, list)
    first = guard[0]
    assert isinstance(first, dict)
    first["prediction_directory_sha256"] = "1" * 64
    receipt_path = _write_canonical_receipt(tmp_path / "prior-tampered.json", strong)
    with pytest.raises(ValueError, match=r"prediction|digest|manifest"):
        build_selection_lock(
            _source_receipt(), config_path, "a" * 40, "cpu", _experiment(), [receipt_path]
        )


def test_prior_evidence_rejects_missing_prediction_artifact(
    config_path: Path, tmp_path: Path,
) -> None:
    strong = _strong_prior_receipt(tmp_path)
    guard = strong["gt_guard"]
    assert isinstance(guard, list)
    first = guard[0]
    assert isinstance(first, dict)
    manifest_path = Path(first["prediction_manifest_path"])
    manifest_path.unlink()
    receipt_path = _write_canonical_receipt(tmp_path / "prior-missing.json", strong)
    with pytest.raises(ValueError, match=r"prediction|manifest|persist"):
        build_selection_lock(
            _source_receipt(), config_path, "a" * 40, "cpu", _experiment(), [receipt_path]
        )


def test_prior_evidence_rejects_future_prediction_manifest(
    config_path: Path, tmp_path: Path,
) -> None:
    strong = _strong_prior_receipt(tmp_path, future=True)
    receipt_path = _write_canonical_receipt(tmp_path / "prior-future.json", strong)
    with pytest.raises(ValueError, match=r"future|timestamp|prediction"):
        build_selection_lock(
            _source_receipt(), config_path, "a" * 40, "cpu", _experiment(), [receipt_path]
        )


@pytest.mark.parametrize("missing_field", ["prediction_path", "prediction_manifest_path"])
def test_prior_evidence_requires_persisted_paths(
    config_path: Path, tmp_path: Path, missing_field: str,
) -> None:
    strong = _strong_prior_receipt(tmp_path)
    guard = strong["gt_guard"]
    assert isinstance(guard, list)
    first = guard[0]
    assert isinstance(first, dict)
    del first[missing_field]
    receipt_path = _write_canonical_receipt(tmp_path / f"prior-{missing_field}.json", strong)
    with pytest.raises(ValueError, match=r"required|missing|prediction"):
        build_selection_lock(
            _source_receipt(), config_path, "a" * 40, "cpu", _experiment(), [receipt_path]
        )


def test_validate_selection_lock_rejects_tampered_file(tmp_path: Path, valid_lock: dict[str, object]) -> None:
    path = write_selection_lock(tmp_path / "selection_lock.json", valid_lock)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["requested_device"] = "cuda"
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(ValueError, match=r"selection_lock_id|device"):
        validate_selection_lock(path)


def test_selection_lock_write_failure_is_atomic_and_retryable(
    tmp_path: Path, valid_lock: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "selection_lock.json"
    original_write = protocol_module.os.write
    injected = False

    def flaky_write(fd: int, payload: bytes) -> int:
        nonlocal injected
        if not injected:
            injected = True
            original_write(fd, payload[:5])
            raise OSError("injected write failure")
        return original_write(fd, payload)

    monkeypatch.setattr(protocol_module.os, "write", flaky_write)
    with pytest.raises(OSError, match="injected"):
        write_selection_lock(path, valid_lock)
    assert not path.exists()
    assert not list(tmp_path.glob(".selection_lock.*"))


def test_selection_lock_publication_failure_cleans_temp_and_final(
    tmp_path: Path, valid_lock: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "selection_lock.json"

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected publication failure")

    monkeypatch.setattr(protocol_module.os, "link", fail_link)
    with pytest.raises(OSError, match="publication"):
        write_selection_lock(path, valid_lock)
    assert not path.exists()
    assert not list(tmp_path.glob(".selection_lock.*"))


def test_selection_lock_post_publish_failure_keeps_valid_final(
    tmp_path: Path, valid_lock: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "selection_lock.json"
    original_validate = protocol_module._validate_lock_bytes
    calls = 0

    def fail_after_publish(raw: bytes) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected post-publish failure")
        return original_validate(raw)

    monkeypatch.setattr(protocol_module, "_validate_lock_bytes", fail_after_publish)
    with pytest.raises(OSError, match="post-publish"):
        write_selection_lock(path, valid_lock)
    monkeypatch.undo()
    assert path.is_file()
    assert validate_selection_lock(path) == valid_lock
    assert not list(tmp_path.glob(".selection_lock.*"))


def test_selection_lock_rejects_symlinked_parent(
    tmp_path: Path, valid_lock: dict[str, object]
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match=r"symlink|parent"):
        write_selection_lock(linked_parent / "selection_lock.json", valid_lock)
    assert not (outside / "selection_lock.json").exists()


def test_selection_lock_parent_rename_swap_cannot_redirect_write(
    tmp_path: Path, valid_lock: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    path = parent / "selection_lock.json"
    (parent / "recipe-c.yaml").write_bytes((tmp_path / "recipe-c.yaml").read_bytes())
    (outside / "recipe-c.yaml").write_bytes((tmp_path / "recipe-c.yaml").read_bytes())
    original_open_parent = protocol_module._open_safe_parent

    def swap_after_check(candidate: Path) -> tuple[int, str]:
        parent_fd, target_name = original_open_parent(candidate)
        moved = tmp_path / "moved-parent"
        parent.rename(moved)
        parent.symlink_to(outside, target_is_directory=True)
        return parent_fd, target_name

    monkeypatch.setattr(protocol_module, "_open_safe_parent", swap_after_check)
    write_selection_lock(path, valid_lock)
    assert not (outside / "selection_lock.json").exists()
    assert (tmp_path / "moved-parent" / "selection_lock.json").is_file()


def test_selection_lock_fsyncs_containing_directory(
    tmp_path: Path, valid_lock: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_fsync = protocol_module.os.fsync
    directory_fsyncs = 0

    def recording_fsync(fd: int) -> None:
        nonlocal directory_fsyncs
        if stat.S_ISDIR(protocol_module.os.fstat(fd).st_mode):
            directory_fsyncs += 1
        original_fsync(fd)

    monkeypatch.setattr(protocol_module.os, "fsync", recording_fsync)
    write_selection_lock(tmp_path / "selection_lock.json", valid_lock)
    assert directory_fsyncs >= 2


def test_selection_lock_rejects_nested_gt_alias_path(valid_lock: dict[str, object]) -> None:
    experiment = valid_lock["experiment"]
    assert isinstance(experiment, dict)
    experiment["changes"] = {"gt_root": "labels/gt.geff"}
    valid_lock["selection_lock_id"] = recompute_selection_lock_id(valid_lock)
    with pytest.raises(ValueError, match=r"ground truth|GT"):
        validate_selection_lock_payload(valid_lock)


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "gt_directory",
        "gt_dir",
        "groundtruth_dir",
        "truth_path",
        "annotation_path",
        "gt_uri",
        "credential",
        "credentials",
        "credential_path",
        "secret_file",
        "token_path",
        "api_key_path",
    ],
)
def test_selection_lock_rejects_nested_gt_and_credential_key_vocabulary(
    config_path: Path, forbidden_key: str,
) -> None:
    with pytest.raises(ValueError, match=r"ground truth|credential|secret|token|path"):
        build_selection_lock(
            _source_receipt(),
            config_path,
            "a" * 40,
            "cpu",
            _experiment(changes={forbidden_key: "relative/reference"}),
        )


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_selection_lock_schema_version_requires_exact_integer(
    valid_lock: dict[str, object], schema_version: object,
) -> None:
    valid_lock["schema_version"] = schema_version
    valid_lock["selection_lock_id"] = recompute_selection_lock_id(valid_lock)
    with pytest.raises(ValueError, match=r"schema_version|schema"):
        validate_selection_lock_payload(valid_lock)


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_prior_receipt_schema_version_requires_exact_integer(
    config_path: Path, tmp_path: Path, schema_version: object,
) -> None:
    strong = _strong_prior_receipt(tmp_path)
    strong["schema_version"] = schema_version
    receipt_path = _write_canonical_receipt(tmp_path / "prior-schema.json", strong)
    with pytest.raises(ValueError, match=r"schema_version|schema"):
        build_selection_lock(
            _source_receipt(), config_path, "a" * 40, "cpu", _experiment(), [receipt_path]
        )


def test_selection_lock_rejects_credential_path_inside_experiment_list(config_path: Path) -> None:
    with pytest.raises(ValueError, match=r"absolute|credential|path"):
        build_selection_lock(
            _source_receipt(),
            config_path,
            "a" * 40,
            "cpu",
            _experiment(changes=["/root/.kaggle/kaggle.json"]),
        )


def _load_freeze_cli() -> object:
    script = Path(__file__).parents[1] / "scripts" / "run_biohub_095.py"
    spec = importlib.util.spec_from_file_location("run_biohub_095_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init_clean_git_checkout(root: Path) -> None:
    root.mkdir()
    (root / "tracked.txt").write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)


def test_freeze_cli_requires_substantive_experiment_args() -> None:
    cli = _load_freeze_cli()
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "freeze",
                "--source", "source",
                "--primary-support", "primary",
                "--secondary-support", "secondary",
                "--config", "configs/biohub_095_recipe_c.yaml",
            ],
        )


def test_freeze_cli_defaults_are_project_rooted(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = _load_freeze_cli()
    monkeypatch.chdir("/")
    args = cli._build_parser().parse_args(
        [
            "freeze",
            "--source", "source",
            "--primary-support", "primary",
            "--secondary-support", "secondary",
            "--experiment-id", "exp",
            "--method-family", "family",
            "--hypothesis", "hypothesis",
            "--expected-gain", "0.1",
            "--cost", "cost",
            "--risk", "risk",
            "--novelty", "novelty",
            "--changes", "changes",
            "--control-id", "control",
            "--acceptance-criteria", "accept",
        ],
    )
    assert args.output == cli.PROJECT_ROOT / "artifacts" / "biohub_095" / "selection_lock.json"
    assert args.config == cli.PROJECT_ROOT / "configs" / "biohub_095_recipe_c.yaml"


@pytest.mark.parametrize("index_flag", ["--assume-unchanged", "--skip-worktree"])
def test_freeze_cli_rejects_hidden_index_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, index_flag: str
) -> None:
    cli = _load_freeze_cli()
    _init_clean_git_checkout(tmp_path / "repo")
    root = tmp_path / "repo"
    subprocess.run(["git", "-C", str(root), "update-index", index_flag, "tracked.txt"], check=True)
    (root / "tracked.txt").write_text("tampered\n", encoding="utf-8")
    monkeypatch.setattr(cli, "PROJECT_ROOT", root)
    with pytest.raises(ValueError, match=r"index|assume|skip|clean"):
        cli._git_commit_and_clean()


def test_freeze_cli_uses_read_only_git_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _load_freeze_cli()
    root = tmp_path / "repo"
    _init_clean_git_checkout(root)
    monkeypatch.setattr(cli, "PROJECT_ROOT", root)
    original_run = cli.subprocess.run
    environments: list[dict[str, str] | None] = []

    def recording_run(*args: object, **kwargs: object) -> object:
        environments.append(kwargs.get("env"))  # type: ignore[arg-type]
        return original_run(*args, **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", recording_run)
    cli._git_commit_and_clean()
    assert environments
    assert all(env is not None and env.get("GIT_OPTIONAL_LOCKS") == "0" for env in environments)


def test_freeze_cli_rejects_external_config_before_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _load_freeze_cli()
    external = tmp_path / "external.yaml"
    external.write_bytes((Path(__file__).parents[1] / "configs" / "biohub_095_recipe_c.yaml").read_bytes())
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "freeze",
            "--source", "source",
            "--primary-support", "primary",
            "--secondary-support", "secondary",
            "--config", str(external),
            "--output", str(tmp_path / "selection_lock.json"),
            "--experiment-id", "exp",
            "--method-family", "family",
            "--hypothesis", "hypothesis",
            "--expected-gain", "0.1",
            "--cost", "cost",
            "--risk", "risk",
            "--novelty", "novelty",
            "--changes", "changes",
            "--control-id", "control",
            "--acceptance-criteria", "accept",
        ],
    )
    monkeypatch.setattr(cli, "_git_commit_and_clean", lambda: "a" * 40)
    monkeypatch.setattr(cli, "validate_source_checkout", lambda _path: _source_receipt())
    monkeypatch.setattr(cli, "validate_support_artifacts", lambda *_paths: {})
    with pytest.raises(ValueError, match=r"--config|canonical|project"):
        cli._freeze(args)
    assert not (tmp_path / "selection_lock.json").exists()


@pytest.mark.parametrize(
    "config_value",
    [
        Path("/tmp/external.yaml"),
        Path("../configs/biohub_095_recipe_c.yaml"),
        Path("configs/../configs/biohub_095_recipe_c.yaml"),
    ],
)
def test_freeze_cli_rejects_external_or_traversal_config(config_value: Path) -> None:
    cli = _load_freeze_cli()
    with pytest.raises(ValueError, match=r"--config|canonical|project"):
        cli._resolve_config_path(config_value)
