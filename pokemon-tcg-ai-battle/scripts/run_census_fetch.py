"""正典 §16 の census 取得を回し、§2.3 の seal 判定まで出す。

`census_pipeline_v1` に本物の transport を渡す層。resume 可能で、途中経過は
SQLite に確定してから次の要求を出すため、いつ止めても再開できる。

## 認証と取得元

`--transport kaggle` は Kaggle の認証情報を要求する。credential が無い環境では
起動時に不足を述べて終了し、ダミー応答で代用しない。取得可否は正典どおり
C3 / C4 / C5 の開始条件にしない。

`--transport replay-dir` は、すでに手元にある取得済み JSON を読む。ネットワークも
認証も使わないため、状態機械と seal 判定の end-to-end 確認に使える。

## 提出はしない

この runner は取得と集計だけを行う。Kaggle への提出は一切行わない。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.census_fetch_v1 import CensusPacerV1, CensusStateStoreV1
from mage_ptcg.meta_specialist.census_pipeline_v1 import (
    run_census_fetch_pass_v1,
    seal_census_from_store_v1,
)
from mage_ptcg.meta_specialist.census_v1 import save_census_report_v1
from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1


def _tier_of_rank_from_spec(spec: str):
    """Build ``rank -> medal band`` from an explicit ``Gold:N,Silver:M,Bronze:K``.

    The cut points are supplied rather than guessed: a medal band is a property
    of the leaderboard on the day it was read, and inventing thresholds here
    would make the census's meaning depend on this script's constants.
    """
    bounds: list[tuple[str, int]] = []
    running = 0
    for chunk in spec.split(","):
        name, _, count = chunk.partition(":")
        name = name.strip()
        if name not in ("Gold", "Silver", "Bronze") or not count.strip().isdigit():
            raise SystemExit(f"bad --medal-bands entry {chunk!r}; expected e.g. 'Gold:22'")
        running += int(count)
        bounds.append((name, running))

    def tier_of_rank(rank: int) -> str:
        for name, upper in bounds:
            if rank <= upper:
                return name
        return bounds[-1][0]

    return tier_of_rank


def _replay_dir_transport(root: Path):
    """Read ``<root>/<stage>/<rank>-<team_id>.json`` instead of calling a service."""

    def transport(stage: str, row):
        path = root / stage / f"{int(row['rank'])}-{row['team_id']}.json"
        if not path.is_file():
            return 404, {}
        return 200, json.loads(path.read_text(encoding="utf-8"))

    return transport


def _kaggle_transport():
    try:
        from mage_ptcg.competition_intelligence.live_payloads import (  # type: ignore
            census_transport_v1,
        )
    except ImportError as exc:
        raise SystemExit(
            "Kaggle transport is unavailable: "
            f"{exc}. Provide credentials and a live_payloads.census_transport_v1, "
            "or use --transport replay-dir with already-fetched payloads. "
            "This runner does not substitute placeholder responses."
        ) from exc
    return census_transport_v1()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True, help="SQLite 状態ストアのパス")
    parser.add_argument("--census-id", required=True,
                        help="leaderboard snapshot の識別子。resume 中は変えられない")
    parser.add_argument("--leaderboard-json", default="",
                        help="rank/team_id/score/timestamp の配列。初回のみ必要")
    parser.add_argument("--transport", choices=("kaggle", "replay-dir"), required=True)
    parser.add_argument("--replay-dir", default="")
    parser.add_argument("--medal-bands", required=True,
                        help="例: 'Gold:22,Silver:283,Bronze:206'")
    parser.add_argument("--max-requests", type=int, default=200)
    parser.add_argument("--output", required=True, help="seal レポートの出力先 JSON")
    args = parser.parse_args()

    tier_of_rank = _tier_of_rank_from_spec(args.medal_bands)
    if args.transport == "replay-dir":
        if not args.replay_dir:
            raise SystemExit("--transport replay-dir requires --replay-dir")
        transport = _replay_dir_transport(Path(args.replay_dir))
    else:
        transport = _kaggle_transport()

    with CensusStateStoreV1(args.store) as store:
        store.seal_census_id(args.census_id)
        if args.leaderboard_json:
            rows = json.loads(Path(args.leaderboard_json).read_text(encoding="utf-8"))
            added = store.enqueue_rows(rows)
            print(f"[census] enqueued {added} new rows (of {len(rows)})", flush=True)

        pending = sum(
            count for state, count in store.state_counts().items()
            if state not in ("qualified", "terminal_failure")
        )
        reporter = ProgressReporterV1(
            total=min(args.max_requests, max(1, pending * 5)), desc=f"census {args.census_id}"
        )
        reporter.note(f"[census] transport={args.transport} pending_rows={pending}")

        progress = run_census_fetch_pass_v1(
            store, transport=transport, pacer=CensusPacerV1(),
            max_requests=args.max_requests, sleep=time.sleep,
            on_progress=lambda payload: reporter.update(
                1, advanced=payload["advanced"], rate_limited=payload["rate_limited"]
            ),
        )
        reporter.close()
        print(json.dumps(progress.to_dict(), ensure_ascii=False, indent=2), flush=True)

        report = seal_census_from_store_v1(store, tier_of_rank=tier_of_rank)
        save_census_report_v1(report, Path(args.output))
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), flush=True)
        if not report.is_sealed:
            print(
                "[census] not sealed yet: rerun to continue from the stored state. "
                "Downstream reports must not treat these numbers as current fact.",
                file=sys.stderr, flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
