"""Run the C2b capability probe without changing any Kaggle competition state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from mage_ptcg.competition.archive import ArchiveSafetyError, DuplicateProbeError  # noqa: E402
from mage_ptcg.competition.probe import ProbeRunner  # noqa: E402


class ProbeArgumentParser(argparse.ArgumentParser):
    """Reserve exit code 3 for invalid CLI arguments as documented by C2b."""

    def error(self, message: str) -> None:
        self.exit(3, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = ProbeArgumentParser(description=__doc__)
    parser.add_argument("--competition", required=True, help="Kaggle competition slug")
    parser.add_argument("--output-dir", default="artifacts/competition/probes", help="ignored archive root")
    parser.add_argument("--offline", action="store_true", help="archive only structured offline results")
    parser.add_argument("--metadata-only", action="store_true", help="run only the metadata capability")
    parser.add_argument("--timeout", type=float, default=20.0, help="per-action timeout in seconds")
    parser.add_argument("--force", action="store_true", help="explicitly replace same-ID archive directories")
    parser.add_argument("--probe-id-prefix", help="safe deterministic prefix for tests/reproducible runs")
    parser.add_argument("--json-summary", action="store_true", help="write report JSON to stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = ProbeRunner().run(
            competition=args.competition,
            output_dir=args.output_dir,
            timeout=args.timeout,
            metadata_only=args.metadata_only,
            offline=args.offline,
            force=args.force,
            probe_id_prefix=args.probe_id_prefix,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 3
    except ArchiveSafetyError as exc:
        print(f"artifact safety failure: {exc}", file=sys.stderr)
        return 4
    except DuplicateProbeError as exc:
        print(f"artifact safety failure: {exc}", file=sys.stderr)
        return 4
    if args.json_summary:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"classified_mode={report['classified_mode']}")
    errors = [entry["error_type"] for entry in report["actions"] if entry["error_type"]]
    return 2 if errors and all(error in {"dependency_missing", "offline", "official_action_unavailable"} for error in errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
