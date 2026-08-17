"""複数レーンの学習・探索を 1 コマンドで並列に走らせ、1 画面で監視する。

## 端末表示の方針 (AGENTS.md「長時間実験の端末表示」)

- 端末を所有するのは**この supervisor だけ**である。各レーンの子プロセスは標準出力・
  標準エラーをレーンごとのログファイルへ落とし、端末には一切書かない。`tee` も
  pipe もしない。
- supervisor は**同じ数行を上書き更新する**。進捗バーと集計を別々に流さず、1 つの
  ダッシュボードとして毎秒書き換える。したがってログに埋もれない。
- 各レーンの数値は子プロセスが atomic に書く `progress.json` から読む
  (`ProgressReporterV1(progress_path=...)`)。端末出力を parse しない。
- 非 TTY (リダイレクト・CI) では上書きが壊れるため、既定 30 秒ごとの集約
  スナップショットへ自動で落とす。

## 性能が上がっているかの確認

`--eval-every-steps` を指定すると、各レーンの新しい checkpoint が出るたびに固定
相手集合との座席均等対戦を回し、ダッシュボードへ **現在値 / 最良値 / 初回からの差**
を出す。評価は学習と同じ画面に出るので、別の窓を見に行く必要はない。

評価相手は全レーン共通の固定集合とし、学習中に変えない。変えると「上がった」の
基準が動く。

## スレッド配分

**process 並列と torch の intra-op 並列は別物として配る。** 収集と shard 読み込みは
プロセス並列でコア数まで素直に速くなるので、レーン数で割ったコア予算をそのまま渡す。
torch の intra-op 並列はそうではない。1 マイクロバッチが 16 例 x 最大 30 トークン x
hidden 128 と小さく、OpenMP のバリア同期が演算量を上回るため、スレッドを増やすほど
遅くなる。28 コア機での実測は 2 スレッドが最速で、28 スレッドは **37 倍遅い**。

この 2 つを 1 つの数で配っていたため、レーンを 1 本だけ指定した run が全 28 コアを
受け取り、14 スレッドで走った他レーンより 12 倍遅くなった
(docs/evidence/bc-thread-oversubscription-20260807.md)。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

PARALLEL_LANES_SCHEMA_V1 = "meta-specialist-parallel-lanes-v1"

# レーン既定。deck と teacher は実測に基づく (docs/decisions/2026-08-05-*).
LANE_PRESETS_V1: dict[str, dict[str, str]] = {
    "archaludon": {
        "archetype_id": "archaludon",
        "deck_csv": "opponents/public_archaludon_cinderace_r7/deck.csv",
        "teacher_id": "public_archaludon_cinderace_r7",
        "decision_ref": "docs/decisions/2026-08-05-public-archaludon-r7-seed.md",
    },
    "grimmsnarl": {
        "archetype_id": "grimmsnarl_froslass_munkidori",
        "deck_csv": "opponents/ozawa_grimmsnarl_v2/deck.csv",
        "teacher_id": "ozawa_grimmsnarl_v2",
        "decision_ref": "docs/decisions/2026-08-05-archaludon-teacher-derivation.md",
    },
    "alakazam": {
        "archetype_id": "alakazam",
        "deck_csv": "opponents/nihei_alakazam/deck.csv",
        "teacher_id": "nihei_alakazam",
        "decision_ref": "docs/decisions/2026-08-05-archaludon-teacher-derivation.md",
    },
    "rocket": {
        "archetype_id": "rocket_mewtwo_spidops",
        "deck_csv": "opponents/ozawa_rocket_v2/deck.csv",
        "teacher_id": "ozawa_rocket_v2",
        "decision_ref": "docs/decisions/2026-08-05-archaludon-teacher-derivation.md",
    },
}


@dataclass
class Lane:
    name: str
    stage_dir: Path
    process: subprocess.Popen | None = None
    log_path: Path = field(default_factory=Path)
    progress_path: Path = field(default_factory=Path)
    status: str = "pending"
    eval_history: list[dict] = field(default_factory=list)
    eval_process: subprocess.Popen | None = None
    eval_output: Path = field(default_factory=Path)
    evaluated_checkpoints: set[str] = field(default_factory=set)

    def read_progress(self) -> dict:
        try:
            payload = json.loads(self.progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if type(payload) is not dict:
            return {}
        # The RL stages are the meta-specialist CLI's own runners, which write a
        # `progress_summary.json` with different field names.  Normalise rather
        # than teach the dashboard two schemas.
        if "planned" in payload and "total" not in payload:
            payload["total"] = payload.get("planned")
            payload["rate_per_second"] = payload.get("throughput") or 0.0
            payload.setdefault("fields", {})
        return payload

    def latest_checkpoint(self) -> Path | None:
        directory = self.stage_dir / "checkpoints"
        if not directory.is_dir():
            return None
        files = sorted(directory.glob("checkpoint-*.pt"), key=lambda p: p.stat().st_mtime)
        return files[-1] if files else None


def _latest_checkpoint_in(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    files = sorted(directory.glob("checkpoint-*.pt"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _fmt_seconds(value: float | None) -> str:
    if value is None or value != value or value in (float("inf"), float("-inf")):
        return "--"
    value = int(value)
    if value < 3600:
        return f"{value // 60:d}m{value % 60:02d}s"
    return f"{value // 3600:d}h{(value % 3600) // 60:02d}m"


# The collect stage reports `loss` as *games lost*, which must not be printed
# under a column headed "loss" next to BC's training loss -- the two numbers mean
# entirely different things and one is not a substitute for the other.
_METRIC_LABEL_V1 = {
    "collect": "win/loss", "seal": "-", "bc": "train loss",
    "rl-collect": "games", "rl-train": "steps",
}


def _metric_text_v1(stage: str, fields: dict) -> str:
    if stage == "collect":
        win, loss = fields.get("win"), fields.get("loss")
        if isinstance(win, (int, float)) and isinstance(loss, (int, float)):
            return f"{int(win)}/{int(loss)}"
        return "--"
    if stage == "bc":
        value = fields.get("loss")
        return f"{value:.5g}" if isinstance(value, (int, float)) else "--"
    return "--"


def _bar(fraction: float, width: int = 22) -> str:
    filled = int(max(0.0, min(1.0, fraction)) * width)
    return "#" * filled + "." * (width - filled)


def _render(lanes: list[Lane], started: float, is_tty: bool, stage: str = "bc") -> list[str]:
    lines = [
        f"lanes={len(lanes)}  elapsed={_fmt_seconds(time.monotonic() - started)}"
        f"  (Ctrl-C to stop; per-lane logs under runs/)",
        "",
        f"{'lane':<12}{'progress':<26}{'done':>13}  {'it/s':>6} {'eta':>7}  "
        f"{_METRIC_LABEL_V1.get(stage, 'metric'):>13}  {'status':<9}",
    ]
    for lane in lanes:
        snapshot = lane.read_progress()
        completed = int(snapshot.get("completed", 0) or 0)
        total = int(snapshot.get("total", 0) or 0)
        fraction = completed / total if total else 0.0
        fields = snapshot.get("fields", {}) or {}
        metric_text = _metric_text_v1(stage, fields)
        lines.append(
            f"{lane.name:<12}{_bar(fraction):<24}{fraction*100:5.1f}%"
            f"{completed:>7}/{total:<6}"
            f"{snapshot.get('rate_per_second', 0.0) or 0.0:>6.2f}"
            f"{_fmt_seconds(snapshot.get('eta_seconds')):>8}"
            f"{metric_text:>15}  {lane.status:<9}"
        )

    if stage != "bc":
        return lines
    lines += ["", f"{'lane':<12}{'eval score (fixed opponents, seat-balanced)':<44}{'trend':<22}"]
    for lane in lanes:
        if not lane.eval_history:
            pending = "measuring..." if lane.eval_process is not None else "not yet evaluated"
            lines.append(f"{lane.name:<12}{pending:<44}")
            continue
        first = lane.eval_history[0]
        latest = lane.eval_history[-1]
        best = max(lane.eval_history, key=lambda item: item["score"])
        delta = latest["score"] - first["score"]
        arrow = "up" if delta > 0.005 else ("down" if delta < -0.005 else "flat")
        body = (
            f"now {latest['score']:.3f} [{latest['ci'][0]:.2f},{latest['ci'][1]:.2f}]"
            f"  best {best['score']:.3f}@{best['step']}"
            f"  first {first['score']:.3f} "
        )
        lines.append(f"{lane.name:<12}{body:<44}{arrow} {delta:+.3f} ({len(lane.eval_history)} evals)")
    if not is_tty:
        lines.append("")
    return lines


class Dashboard:
    """Owns the terminal: overwrites the same block, never scrolls it away."""

    def __init__(self, *, is_tty: bool, snapshot_interval: float) -> None:
        self._is_tty = is_tty
        self._height = 0
        self._snapshot_interval = snapshot_interval
        self._last_snapshot = 0.0

    def draw(self, lines: list[str]) -> None:
        if not self._is_tty:
            now = time.monotonic()
            if now - self._last_snapshot < self._snapshot_interval:
                return
            self._last_snapshot = now
            print("\n".join(lines), flush=True)
            return
        out = sys.stdout
        if self._height:
            out.write(f"\033[{self._height}A")
        for line in lines:
            out.write("\033[2K" + line + "\n")
        # Clear anything left from a previously taller frame.
        for _ in range(max(0, self._height - len(lines))):
            out.write("\033[2K\n")
        self._height = max(len(lines), self._height)
        out.flush()

    def finish(self, lines: list[str]) -> None:
        self.draw(lines)
        if self._is_tty:
            sys.stdout.write("\n")
            sys.stdout.flush()


def _torch_threads_v1(args, threads: int) -> int:
    """Cap torch's intra-op threads; `threads` stays the *process* worker budget.

    These are different resources and must not share one number.  Collection and
    shard reading are process-parallel and scale with cores, so they want the
    whole per-lane budget.  torch's intra-op parallelism does not: one microbatch
    is 16 examples x at most 30 tokens x hidden 128, small enough that the OpenMP
    barrier costs more than the arithmetic.  Measured on a 28-core box, 2 threads
    is fastest and 28 is **37x slower** -- which is why a lane handed every core
    ran 12x slower than the same code on 14 threads
    (docs/evidence/bc-thread-oversubscription-20260807.md).
    """
    return max(1, min(args.max_torch_threads, threads))


def _lane_command(lane_name: str, preset: dict, args, threads: int,
                  progress_path: Path) -> list[str]:
    """The child command for one lane at the requested stage.

    `threads` is the per-lane *process* budget (cores per lane).  Anything that
    hands it to torch must route it through `_torch_threads_v1` first.
    """
    run_name = f"{args.run_prefix}-{lane_name}"
    if args.stage == "collect":
        return [
            sys.executable, str(_ROOT / "scripts" / "run_teacher_collection.py"),
            "--archetype-id", preset["archetype_id"],
            "--teacher-id", preset["teacher_id"],
            "--num-games", str(args.num_games),
            "--base-seed", str(args.base_seed),
            "--run-name", run_name,
            "--workers", str(args.collect_workers or threads),
            "--progress-path", str(progress_path),
        ]
    if args.stage == "seal":
        # `main` delegates the whole seal stage to `seal_lanes.py`, which shares
        # one queue across lanes, so no per-lane child is ever built here.
        raise AssertionError("the seal stage does not spawn per-lane children")
    if args.stage in ("rl-collect", "rl-train"):
        checkpoint = _latest_checkpoint_in(
            Path(args.output_base) / f"{args.run_prefix}-{lane_name}" / "checkpoints"
        )
        if checkpoint is None:
            raise SystemExit(
                f"{lane_name}: θ0 checkpoint がありません "
                f"({Path(args.output_base) / f'{args.run_prefix}-{lane_name}' / 'checkpoints'})\n"
                "  --stage bc を先に走らせてください"
            )
        # One run directory per lane, never one shared across lanes:
        # `train-from-trajectories` reads *every* record under `games/`, so a
        # shared directory would fit one model to several archetypes' decks.
        collection_dir = Path("runs/meta-specialist-actor-pool") / f"{args.run_prefix}-rl-{lane_name}"
        if args.stage == "rl-collect":
            return [
                sys.executable, "-m", "mage_ptcg.meta_specialist.cli",
                "collect-trajectories",
                "--lanes", preset["archetype_id"],
                "--num-games", str(args.rl_games),
                "--base-seed", str(args.base_seed),
                "--run-name", collection_dir.name,
                "--workers", str(threads),
                "--behavior-kind", "neural_specialist",
                "--neural-checkpoint-path", str(checkpoint),
                "--decoding-mode", "sample",
            ]
        return [
            sys.executable, "-m", "mage_ptcg.meta_specialist.cli",
            "train-from-trajectories",
            "--collection-run-dir", str(collection_dir),
            "--run-name", f"{args.run_prefix}-rl-{lane_name}",
            "--max-steps", str(args.max_steps),
            "--bootstrap-checkpoint", str(checkpoint),
            "--checkpoint-interval-steps", str(args.checkpoint_interval_steps),
            # Left unset, this defaults to every core and pays the same 37x
            # oversubscription penalty that BC did.
            "--torch-threads", str(_torch_threads_v1(args, threads)),
        ]

    snapshot = Path(args.snapshot_template.format(lane=lane_name, run=run_name))
    if not snapshot.is_file():
        raise SystemExit(
            f"{lane_name}: snapshot not found: {snapshot}\n"
            "  --stage collect と --stage seal を先に走らせてください"
        )
    return [
        sys.executable, str(_ROOT / "scripts" / "run_bc_distillation.py"),
        "--snapshot", str(snapshot),
        "--archetype-id", preset["archetype_id"],
        "--deck-csv", preset["deck_csv"],
        "--teacher-id", preset["teacher_id"],
        "--decision-ref", preset["decision_ref"],
        "--run-name", run_name,
        "--max-steps", str(args.max_steps),
        "--examples-per-step", str(args.examples_per_step),
        "--microbatch-examples", str(args.microbatch_examples),
        "--checkpoint-interval-steps", str(args.checkpoint_interval_steps),
        "--torch-threads", str(_torch_threads_v1(args, threads)),
        # Shard reading is process-parallel and does scale, so it keeps the full
        # per-lane core budget rather than the capped torch number.
        "--read-workers", str(threads),
        "--output-base", str(args.output_base),
        "--progress-path", str(progress_path),
    ]


def _spawn_bc_lane(lane_name: str, preset: dict, args, threads: int) -> tuple[subprocess.Popen, Path, Path, Path]:
    if args.stage in ("collect", "seal"):
        stage_dir = Path("runs/meta-specialist-teacher-records") / f"{args.run_prefix}-{lane_name}"
    elif args.stage == "rl-collect":
        stage_dir = Path("runs/meta-specialist-actor-pool") / f"{args.run_prefix}-rl-{lane_name}"
    elif args.stage == "rl-train":
        stage_dir = Path("runs/meta-specialist-training") / f"{args.run_prefix}-rl-{lane_name}"
    else:
        stage_dir = Path(args.output_base) / f"{args.run_prefix}-{lane_name}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    log_path = stage_dir / f"{args.stage}.log"
    # The RL runners write their own progress file at a fixed name; the others
    # are told where to write theirs.
    progress_path = (
        stage_dir / "progress_summary.json" if args.stage.startswith("rl-")
        else stage_dir / f"{args.stage}-progress.json"
    )
    command = _lane_command(lane_name, preset, args, threads, progress_path)
    environment = dict(os.environ, PYTHONPATH=f"{_ROOT}:{_ROOT / 'src'}")
    handle = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        command, stdout=handle, stderr=subprocess.STDOUT, cwd=str(_ROOT), env=environment,
    )
    return process, log_path, progress_path, stage_dir


def _spawn_eval(lane: Lane, preset: dict, args, checkpoint: Path) -> subprocess.Popen:
    output = lane.stage_dir / f"eval-{checkpoint.stem[-12:]}.json"
    command = [
        sys.executable, str(_ROOT / "scripts" / "measure_opponent_strength.py"),
        "--subject-checkpoint", str(checkpoint),
        "--subject-deck-csv", preset["deck_csv"],
        "--subject-archetype-id", preset["archetype_id"],
        "--opponent-ids", args.eval_opponents,
        "--games-per-opponent-seat", str(args.eval_games_per_seat),
        "--base-seed", str(args.eval_base_seed),
        "--output", str(output),
    ]
    environment = dict(os.environ, PYTHONPATH=f"{_ROOT}:{_ROOT / 'src'}")
    lane.eval_output = output
    with open(lane.stage_dir / "eval.log", "a", encoding="utf-8") as handle:
        return subprocess.Popen(
            command, stdout=handle, stderr=subprocess.STDOUT, cwd=str(_ROOT), env=environment,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lanes", default="all",
                        help="'all' か、カンマ区切りのレーン名 "
                             f"({', '.join(sorted(LANE_PRESETS_V1))})")
    parser.add_argument("--stage",
                        choices=("collect", "seal", "bc", "rl-collect", "rl-train"),
                        default="bc",
                        help="collect: teacher 対局の収集 / seal: 封印 / "
                             "bc: teacher snapshot からの θ0 蒸留 / "
                             "rl-collect: θ0 を behavior とする軌跡収集 / "
                             "rl-train: 収集した軌跡での V-trace 学習")
    parser.add_argument("--rl-games", type=int, default=600,
                        help="--stage rl-collect の 1 レーンあたり局数")
    parser.add_argument("--num-games", type=int, default=300,
                        help="--stage collect のときの 1 レーンあたり局数")
    parser.add_argument("--collect-workers", type=int, default=0,
                        help="--stage collect の 1 レーン内の同時対局数。0 で "
                             "総コア数/レーン数（= 全コアを使い切る）")
    parser.add_argument("--base-seed", type=int, default=5600000)
    parser.add_argument(
        "--shard-max-examples", type=int, default=20000,
        help="--stage seal のとき 1 shard に入れる example の上限。3000 局の corpus は "
             "単一 snapshot に収まらないため既定で shard 化する",
    )
    parser.add_argument(
        "--chunk-max-bytes", type=int, default=64 * 1024 * 1024,
        help="--stage seal のとき dataset chunk 1 個の上限 byte 数。小さいほど並列度が "
             "上がり、封印中の peak RSS は workers x この値で決まる",
    )
    parser.add_argument("--snapshot-template",
                        default="runs/meta-specialist-teacher-records/{run}/snapshot_index.json",
                        help="{lane} と {run} が置換される。shard 化した corpus は index を指す")
    parser.add_argument("--max-steps", type=int, default=2000)
    parser.add_argument("--examples-per-step", type=int, default=64)
    parser.add_argument("--microbatch-examples", type=int, default=16)
    parser.add_argument("--checkpoint-interval-steps", type=int, default=200)
    parser.add_argument("--run-prefix", default="theta0")
    parser.add_argument("--output-base", default="runs/meta-specialist-bc-distill")
    parser.add_argument("--total-threads", type=int, default=os.cpu_count() or 4)
    parser.add_argument(
        "--max-torch-threads", type=int, default=4,
        help="torch の intra-op スレッド数の上限。process 並列 (収集・shard 読み込み) "
             "には掛からない。既定 4 は 28 コア機での実測に基づく: 2 が最速、4 で 1.04 倍、"
             "7 で 1.97 倍、14 で 4.33 倍、28 で 37 倍遅い。上限を外すと、レーンを 1 本だけ "
             "指定した run が全コアを受け取って最も遅くなる",
    )
    parser.add_argument("--eval-every-steps", type=int, default=200,
                        help="0 で評価を行わない")
    parser.add_argument("--eval-opponents",
                        default="kiyotah_lucario,sue124_alakazam,skarin_dragapult,"
                                "ozawa_crustle_v2,nihei_megalopunny,yaroslav_crustleaware_lucario",
                        help="全レーン共通の固定評価相手。学習中に変えないこと")
    parser.add_argument("--eval-games-per-seat", type=int, default=3)
    parser.add_argument("--eval-base-seed", type=int, default=9300000)
    parser.add_argument("--refresh-seconds", type=float, default=1.0)
    parser.add_argument("--snapshot-seconds", type=float, default=30.0,
                        help="非 TTY のときの集約出力間隔")
    args = parser.parse_args()

    names = (
        sorted(LANE_PRESETS_V1) if args.lanes == "all"
        else [x.strip() for x in args.lanes.split(",") if x.strip()]
    )
    unknown = [n for n in names if n not in LANE_PRESETS_V1]
    if unknown:
        raise SystemExit(f"unknown lanes {unknown}; known: {sorted(LANE_PRESETS_V1)}")

    threads = max(1, args.total_threads // max(1, len(names)))
    is_tty = sys.stdout.isatty()

    if args.stage == "seal":
        # Sealing is delegated to a single process that puts every lane's chunks
        # in one queue.  Giving each lane its own pool wasted the difference
        # between them -- the lanes measured here span 3.4 GB to 8.7 GB, so the
        # small lanes' cores went idle while the largest still had most of its
        # work left.  One queue means a worker that runs out of small-lane chunks
        # is already on the large lane.
        return subprocess.call(
            [
                sys.executable, str(_ROOT / "scripts" / "seal_lanes.py"),
                "--lanes", ",".join(names),
                "--run-prefix", args.run_prefix,
                "--shard-max-examples", str(args.shard_max_examples),
                "--chunk-max-bytes", str(args.chunk_max_bytes),
                "--workers", str(args.total_threads),
                "--refresh-seconds", str(args.refresh_seconds),
                "--snapshot-seconds", str(args.snapshot_seconds),
            ],
            cwd=str(_ROOT),
            env=dict(os.environ, PYTHONPATH=f"{_ROOT}:{_ROOT / 'src'}"),
        )

    lanes: list[Lane] = []
    try:
        for name in names:
            process, log_path, progress_path, stage_dir = _spawn_bc_lane(
                name, LANE_PRESETS_V1[name], args, threads
            )
            lanes.append(Lane(
                name=name, stage_dir=stage_dir, process=process,
                log_path=log_path, progress_path=progress_path, status="running",
            ))
    except BaseException:
        # A later lane failing to start (missing snapshot, bad preset) must not
        # leave the already-started ones running with nobody supervising them:
        # they would keep burning cores and writing checkpoints unnoticed.
        for lane in lanes:
            if lane.process is not None:
                lane.process.terminate()
        for lane in lanes:
            if lane.process is not None:
                try:
                    lane.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    lane.process.kill()
        raise

    print(
        f"[parallel] stage={args.stage} lanes={names} cores/lane={threads} "
        f"torch_threads/lane={_torch_threads_v1(args, threads)} "
        f"steps={args.max_steps} eval_every={args.eval_every_steps}",
        flush=True,
    )
    if is_tty:
        print()

    started = time.monotonic()
    dashboard = Dashboard(is_tty=is_tty, snapshot_interval=args.snapshot_seconds)
    try:
        while True:
            for lane in lanes:
                if lane.process is not None and lane.process.poll() is not None:
                    lane.status = "done" if lane.process.returncode == 0 else (
                        f"FAILED({lane.process.returncode})"
                    )
                    lane.process = None

                if lane.eval_process is not None and lane.eval_process.poll() is not None:
                    if lane.eval_output.is_file():
                        try:
                            payload = json.loads(lane.eval_output.read_text(encoding="utf-8"))
                            lane.eval_history.append({
                                "step": int(lane.read_progress().get("completed", 0) or 0),
                                "score": float(payload["score_rate"]),
                                "ci": payload["score_ci95"],
                            })
                        except (OSError, json.JSONDecodeError, KeyError, ValueError):
                            pass
                    lane.eval_process = None

                if args.stage == "bc" and args.eval_every_steps and lane.eval_process is None:
                    checkpoint = lane.latest_checkpoint()
                    if checkpoint is not None and checkpoint.name not in lane.evaluated_checkpoints:
                        lane.evaluated_checkpoints.add(checkpoint.name)
                        lane.eval_process = _spawn_eval(
                            lane, LANE_PRESETS_V1[lane.name], args, checkpoint
                        )

            dashboard.draw(_render(lanes, started, is_tty, args.stage))
            if all(lane.process is None and lane.eval_process is None for lane in lanes):
                break
            time.sleep(args.refresh_seconds)
    except KeyboardInterrupt:
        for lane in lanes:
            for process in (lane.process, lane.eval_process):
                if process is not None:
                    process.terminate()
        print("\n[parallel] interrupted; child processes terminated", flush=True)
        return 130

    dashboard.finish(_render(lanes, started, is_tty, args.stage))
    failed = [lane.name for lane in lanes if lane.status.startswith("FAILED")]
    for lane in lanes:
        print(f"[parallel] {lane.name}: {lane.status}  log={lane.log_path}", flush=True)
    if failed:
        print(f"[parallel] failed lanes: {failed}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
