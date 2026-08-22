"""Command line entry points for submission packaging and validation.

    python -m biohub.submission.cli validate  --csv submission.csv
    python -m biohub.submission.cli package   --run-dir <dir> --method harmonic_v1 --csv out.csv
    python -m biohub.submission.cli fixture   --out-dir <dir>

``validate`` is the only subcommand that runs on the standard library alone.

None of these submit anything.  There is deliberately no ``submit`` subcommand:
sending an artefact to Kaggle requires the user's explicit instruction and is a
separate manual operation (``AGENTS.md`` section 9).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .schema import REFERENCE_SHAPE_TZYX


def _add_validate(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("validate", help="Validate a candidate submission.csv.")
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument(
        "--expect-datasets",
        default=None,
        help="Comma-separated dataset names the submission must cover exactly.",
    )
    p.add_argument(
        "--expect-from-zarr-dir",
        type=Path,
        default=None,
        help="Directory of test *.zarr; dataset names are taken from it.",
    )
    p.add_argument(
        "--shape",
        default=",".join(str(v) for v in REFERENCE_SHAPE_TZYX),
        help="Assumed (T,Z,Y,X) for bounds checks, or 'none' to skip.",
    )
    p.add_argument(
        "--gt-dir",
        type=Path,
        default=None,
        help="Directory of ground-truth *.geff, used ONLY to detect GT leakage.",
    )
    p.add_argument("--require-divisions", action="store_true")
    p.add_argument("--json", type=Path, default=None, help="Write the report as JSON.")


def _add_package(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("package", help="Build submission.csv from prediction GEFFs.")
    p.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="Directory laid out as <run-dir>/<sample>/<method>.geff.",
    )
    p.add_argument("--method", required=True, help="Method id, e.g. harmonic_v1.")
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--no-roundtrip", action="store_true")


def _add_fixture(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("fixture", help="Write SYNTHETIC fixtures (tests only).")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--broken", default=None, help="Name of a single breakage to emit.")


def _parse_shape(raw: str) -> tuple[int, int, int, int] | None:
    if raw.strip().lower() == "none":
        return None
    parts = tuple(int(v) for v in raw.split(","))
    if len(parts) != 4:
        raise ValueError(f"--shape needs 4 comma-separated ints, got {raw!r}")
    return parts  # type: ignore[return-value]


def _cmd_validate(args: argparse.Namespace) -> int:
    from .validator import datasets_from_zarr_dir, load_ground_truth_nodes, validate_submission, write_report_json

    expected = None
    if args.expect_datasets:
        expected = [s.strip() for s in args.expect_datasets.split(",") if s.strip()]
    elif args.expect_from_zarr_dir:
        expected = datasets_from_zarr_dir(args.expect_from_zarr_dir)

    gt = load_ground_truth_nodes(args.gt_dir) if args.gt_dir else None

    report = validate_submission(
        args.csv,
        expected_datasets=expected,
        volume_shape_tzyx=_parse_shape(args.shape),
        ground_truth_nodes=gt,
        require_divisions=args.require_divisions,
    )
    print(report.render())
    if args.json:
        write_report_json(report, args.json)
        print(f"\nwrote {args.json}")
    return 0 if report.ok else 1


def _cmd_package(args: argparse.Namespace) -> int:
    from .packaging import (
        check_roundtrip,
        collect_method_geffs,
        write_provenance,
        write_submission_csv,
    )

    geffs = collect_method_geffs(args.run_dir, args.method)
    print(f"packaging {len(geffs)} dataset(s) with method {args.method!r}")
    csv_path, packaged = write_submission_csv(geffs, args.csv)
    for p in packaged:
        print(f"  {p.dataset}: {p.n_nodes} nodes, {p.n_edges} edges")
    prov = write_provenance(csv_path, packaged, method_id=args.method)
    print(f"wrote {csv_path}\nwrote {prov}")

    if not args.no_roundtrip:
        rt = check_roundtrip(csv_path, packaged)
        if rt.matches_source:
            print("round-trip: node/edge counts and id references intact")
        else:
            print("round-trip FAILED:")
            for m in rt.mismatches:
                print(f"  {m}")
            return 1
    print(
        "\nNOTE: this artefact has NOT been submitted. Submitting requires the "
        "user's explicit instruction."
    )
    return 0


def _cmd_fixture(args: argparse.Namespace) -> int:
    from .fixture import BREAKAGES, write_broken_submission, write_valid_submission

    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.broken:
        path = write_broken_submission(args.out_dir / f"broken_{args.broken}.csv", args.broken)
        print(f"wrote SYNTHETIC broken fixture: {path}")
        return 0
    path = write_valid_submission(args.out_dir / "submission.csv")
    print(f"wrote SYNTHETIC valid fixture: {path}")
    for name in BREAKAGES:
        p = write_broken_submission(args.out_dir / f"broken_{name}.csv", name)
        print(f"wrote SYNTHETIC broken fixture: {p}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="biohub.submission")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_validate(sub)
    _add_package(sub)
    _add_fixture(sub)
    args = parser.parse_args(argv)

    return {
        "validate": _cmd_validate,
        "package": _cmd_package,
        "fixture": _cmd_fixture,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
