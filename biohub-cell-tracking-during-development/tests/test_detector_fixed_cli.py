from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "run_detector_fixed_race.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("detector_fixed_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_associate_cli_rejects_ground_truth_argument() -> None:
    cli = _load_cli()
    with pytest.raises(SystemExit):
        cli._build_parser().parse_args(
            [
                "associate",
                "--cache",
                "cache",
                "--output",
                "out",
                "--upstream-root",
                "upstream",
                "--ground-truth",
                "truth.geff",
            ]
        )


def test_freeze_panel_parser_has_no_metric_option() -> None:
    cli = _load_cli()
    args = cli._build_parser().parse_args(
        [
            "freeze-panel",
            "--train-root",
            "train",
            "--gt-root",
            "gt",
            "--development-sample",
            "dev",
            "--output",
            "panel.json",
        ]
    )
    assert args.command == "freeze-panel"
    assert not hasattr(args, "score")


def test_materialize_parser_defaults_to_accelerator_auto_selection() -> None:
    cli = _load_cli()
    args = cli._build_parser().parse_args(
        [
            "materialize",
            "--sample",
            "sample",
            "--train-root",
            "train",
            "--upstream-root",
            "upstream",
            "--checkpoint",
            "checkpoint.pth",
            "--output",
            "out",
        ]
    )
    assert args.device == "auto"


def test_dev_race_parser_accepts_harmonic_reverse_weight() -> None:
    cli = _load_cli()
    args = cli._build_parser().parse_args(
        [
            "dev-race",
            "--sample",
            "sample",
            "--cache",
            "cache",
            "--output",
            "out",
            "--ground-truth",
            "truth.geff",
            "--upstream-root",
            "upstream",
            "--methods",
            "harmonic_v1",
            "--harmonic-reverse-weight",
            "0.30",
        ]
    )
    assert args.harmonic_reverse_weight == pytest.approx(0.30)
