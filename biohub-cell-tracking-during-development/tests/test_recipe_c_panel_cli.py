from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from biohub.recipe_c.protocol import PANEL_V1

SCRIPT = Path(__file__).parents[1] / "scripts" / "run_biohub_095.py"


def _load_cli() -> Any:
    spec = importlib.util.spec_from_file_location("run_biohub_095_panel_cli_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _infer_args(cli: Any, tmp_path: Path) -> Any:
    return cli._build_parser().parse_args(_infer_argv(tmp_path))


def _infer_argv(tmp_path: Path) -> list[str]:
    return [
        "infer-panel",
        "--source",
        str(tmp_path / "source"),
        "--primary-support",
        str(tmp_path / "primary"),
        "--secondary-support",
        str(tmp_path / "secondary"),
        "--selection-lock",
        str(tmp_path / "selection-lock.json"),
        "--stage-destination",
        str(tmp_path / "stage"),
        "--image-root",
        str(tmp_path / "images"),
        "--output-root",
        str(tmp_path / "output"),
    ]


def _evaluate_args(cli: Any, tmp_path: Path) -> Any:
    return cli._build_parser().parse_args(
        [
            "evaluate-panel",
            "--selection-lock",
            str(tmp_path / "selection-lock.json"),
            "--prediction-root",
            str(tmp_path / "predictions"),
            "--inference-receipt",
            str(tmp_path / "inference-receipt.json"),
            "--gt-root",
            str(tmp_path / "ground-truth"),
            "--output",
            str(tmp_path / "panel.json"),
            "--reproduction-command",
            "recipe-command",
        ],
    )


def test_infer_panel_parser_is_full_fixed_panel_without_overrides(tmp_path: Path) -> None:
    cli = _load_cli()
    args = _infer_args(cli, tmp_path)

    assert args.command == "infer-panel"
    assert args.handler is cli._infer_panel
    for forbidden in (
        "sample_id",
        "max_frames",
        "ground_truth",
        "metric_config",
        "exclude",
        "subset",
        "resume",
    ):
        assert not hasattr(args, forbidden)

    with pytest.raises(SystemExit):
        cli._build_parser().parse_args([*_infer_argv(tmp_path), "--max-frames", "6"])


@dataclass(frozen=True)
class _Receipt:
    status: str = "READY"
    mode: str = "full"
    max_frames: int | None = None
    sample_ids: tuple[str, ...] = PANEL_V1
    raw_geffs: dict[str, str] | None = None
    final_geffs: dict[str, str] | None = None
    manifests: dict[str, str] | None = None
    ground_truth_open_count: int = 0
    ground_truth_opened: bool = False
    metric_call_count: int = 0
    metric_status: str = "not_run_gt_guard"
    failure: None = None

    def __post_init__(self) -> None:
        if self.raw_geffs is None:
            object.__setattr__(self, "raw_geffs", {sample: f"raw/{sample}.geff" for sample in PANEL_V1})
        if self.final_geffs is None:
            object.__setattr__(self, "final_geffs", {sample: f"predictions/{sample}.geff" for sample in PANEL_V1})
        if self.manifests is None:
            object.__setattr__(
                self,
                "manifests",
                {sample: f"predictions/{sample}.manifest.json" for sample in PANEL_V1},
            )


def test_infer_panel_runs_one_full_panel_inference_and_closes_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load_cli()
    args = _infer_args(cli, tmp_path)
    events: list[tuple[str, object]] = []

    class Stage:
        def close(self) -> None:
            events.append(("close", None))

    stage = Stage()

    def fake_stage(*stage_args: object) -> Stage:
        events.append(("stage", stage_args))
        return stage

    def fake_inference(*run_args: object) -> _Receipt:
        events.append(("run", run_args))
        return _Receipt()

    monkeypatch.setattr(cli, "stage_recipe_c_runtime", fake_stage)
    import biohub.recipe_c.runner as runner_module

    monkeypatch.setattr(runner_module, "run_recipe_c_inference", fake_inference)

    assert cli._infer_panel(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "READY"
    assert payload["mode"] == "full"
    assert payload["max_frames"] is None
    assert payload["sample_ids"] == list(PANEL_V1)
    assert tuple(payload["raw_geffs"]) == PANEL_V1
    assert tuple(payload["final_geffs"]) == PANEL_V1
    assert tuple(payload["manifests"]) == PANEL_V1
    assert payload["ground_truth_open_count"] == 0
    assert payload["ground_truth_opened"] is False
    assert payload["metric_call_count"] == 0
    assert payload["metric_status"] == "not_run_gt_guard"
    assert payload["failure"] is None

    assert [kind for kind, _value in events] == ["stage", "run", "close"]
    run_args = events[1][1]
    assert isinstance(run_args, tuple)
    assert run_args[0] == Path(args.image_root)
    assert run_args[1] == PANEL_V1
    assert run_args[2] is stage
    assert run_args[3] == Path(args.selection_lock)
    assert run_args[4] == Path(args.output_root)
    assert run_args[5] is None


@pytest.mark.parametrize("destination", ["stage_destination", "output_root"])
def test_infer_panel_requires_both_destinations_to_be_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    cli = _load_cli()
    args = _infer_args(cli, tmp_path)
    existing = Path(getattr(args, destination))
    existing.mkdir()
    called = False

    def fail_stage(*_args: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("stage must not run for a non-fresh destination")

    monkeypatch.setattr(cli, "stage_recipe_c_runtime", fail_stage)
    with pytest.raises(ValueError, match="fresh"):
        cli._infer_panel(args)
    assert called is False


@pytest.mark.parametrize(
    ("stage_relative", "output_relative"),
    [("shared", "shared"), ("stage", "stage/output"), ("output/stage", "output")],
)
def test_infer_panel_rejects_overlapping_stage_and_output_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage_relative: str,
    output_relative: str,
) -> None:
    cli = _load_cli()
    args = _infer_args(cli, tmp_path)
    args.stage_destination = tmp_path / stage_relative
    args.output_root = tmp_path / output_relative
    monkeypatch.setattr(
        cli,
        "stage_recipe_c_runtime",
        lambda *_args: pytest.fail("stage must not run for overlapping destinations"),
    )

    with pytest.raises(ValueError, match="overlap"):
        cli._infer_panel(args)


def test_infer_panel_closes_stage_when_runner_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _load_cli()
    args = _infer_args(cli, tmp_path)
    closed = False

    class Stage:
        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(cli, "stage_recipe_c_runtime", lambda *_args: Stage())
    import biohub.recipe_c.runner as runner_module

    monkeypatch.setattr(
        runner_module,
        "run_recipe_c_inference",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("runner failed")),
    )

    with pytest.raises(RuntimeError, match="runner failed"):
        cli._infer_panel(args)
    assert closed is True


def test_infer_panel_rejects_symlinked_output_parent_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _load_cli()
    args = _infer_args(cli, tmp_path)
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    args.output_root = linked_parent / "output"
    monkeypatch.setattr(
        cli,
        "stage_recipe_c_runtime",
        lambda *_args: pytest.fail("stage must not run through a symlinked output parent"),
    )

    with pytest.raises(ValueError, match="symlinked parent"):
        cli._infer_panel(args)


def test_evaluate_panel_builds_ordered_gt_map_and_preflights_all_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _load_cli()
    args = _evaluate_args(cli, tmp_path)
    import biohub.recipe_c.evaluation as evaluation_module

    safe_targets: list[Path] = []
    calls: list[dict[str, object]] = []

    def fake_safe_target(path: Path) -> Path:
        safe_targets.append(Path(path))
        return Path(path)

    def fake_evaluate(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"status": "READY", "panel_status": "READY"}

    monkeypatch.setattr(evaluation_module, "_safe_write_target", fake_safe_target)
    monkeypatch.setattr(evaluation_module, "evaluate_panel", fake_evaluate)

    assert cli._evaluate_panel(args) == 0
    assert len(calls) == 1
    call = calls[0]
    assert tuple(call["ground_truth_map"]) == PANEL_V1
    assert call["ground_truth_map"] == {
        sample: Path(args.gt_root) / f"{sample}.geff" for sample in PANEL_V1
    }
    output = Path(args.output)
    assert safe_targets == [
        output,
        *(output.parent / f"{sample}.metric_receipt.json" for sample in PANEL_V1),
    ]
    assert "metric_config" not in call


@pytest.mark.parametrize(
    ("status", "panel_status", "expected"),
    [("READY", "READY", 0), ("READY", "INCOMPLETE", 2), ("FAILED", "INCOMPLETE", 2)],
)
def test_evaluate_panel_exit_code_requires_ready_panel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    panel_status: str,
    expected: int,
) -> None:
    cli = _load_cli()
    args = _evaluate_args(cli, tmp_path)
    import biohub.recipe_c.evaluation as evaluation_module

    monkeypatch.setattr(evaluation_module, "_safe_write_target", lambda path: Path(path))
    monkeypatch.setattr(
        evaluation_module,
        "evaluate_panel",
        lambda **_kwargs: {"status": status, "panel_status": panel_status},
    )

    assert cli._evaluate_panel(args) == expected


@pytest.mark.parametrize("target_kind", ["panel", "sidecar"])
def test_evaluate_panel_rejects_existing_output_before_api_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_kind: str
) -> None:
    cli = _load_cli()
    args = _evaluate_args(cli, tmp_path)
    output = Path(args.output)
    existing = (
        output
        if target_kind == "panel"
        else output.parent / f"{PANEL_V1[0]}.metric_receipt.json"
    )
    existing.write_text("existing\n", encoding="utf-8")
    import biohub.recipe_c.evaluation as evaluation_module

    monkeypatch.setattr(evaluation_module, "evaluate_panel", lambda **_kwargs: pytest.fail("API called"))

    with pytest.raises(ValueError, match="fresh"):
        cli._evaluate_panel(args)


def test_evaluate_panel_rejects_panel_sidecar_target_collision_before_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _load_cli()
    args = _evaluate_args(cli, tmp_path)
    args.output = Path(args.output).parent / f"{PANEL_V1[0]}.metric_receipt.json"
    import biohub.recipe_c.evaluation as evaluation_module

    monkeypatch.setattr(evaluation_module, "evaluate_panel", lambda **_kwargs: pytest.fail("API called"))

    with pytest.raises(ValueError, match="distinct"):
        cli._evaluate_panel(args)
