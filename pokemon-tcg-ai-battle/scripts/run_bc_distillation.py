"""封印済み teacher snapshot から θ0 を BC 蒸留で作る。

正典 §1 の Foundation θ0 を、乱数初期化ではなく既知の強い teacher の複製として
作る。RL は θ0 の後段 (`train_from_trajectories_v1`) が担う。

長時間実行を想定している。進捗は progress_summary.json へ atomic に書き、
端末へは状態遷移だけを出す (AGENTS.md「長時間実験の端末表示」)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

import torch

from mage_ptcg.meta_specialist.actor_pool_v1 import _build_actor_pool_deck_binding_v1
from mage_ptcg.meta_specialist.bc_distill_v1 import foundation_init_from_snapshot_v1
from mage_ptcg.meta_specialist.foundation_init_v1 import DERIVATION_QUALIFIED_V1
from mage_ptcg.meta_specialist.neural_adapter_v1 import (
    make_specialist_row_logits_v1,
    make_specialist_state_values_v1,
)
from mage_ptcg.meta_specialist.neural_checkpoint_v1 import (
    build_checkpoint_payload_v1,
    build_training_identity_v1,
    publish_checkpoint_v1,
)
from mage_ptcg.meta_specialist.neural_learner_v1 import training_step_v1
from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1
from mage_ptcg.meta_specialist.neural_model_v1 import (
    SpecialistModelConfigV1,
    build_specialist_policy_model_v1,
)
from mage_ptcg.meta_specialist.training_snapshot_v1 import (
    read_sharded_split_examples_v1,
    read_training_snapshot_v1,
    snapshot_examples_for_split_v1,
)


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--archetype-id", required=True)
    parser.add_argument("--deck-csv", required=True)
    parser.add_argument("--teacher-id", required=True)
    parser.add_argument("--decision-ref", required=True,
                        help="teacher の派生資格を記録した判断記録のパス")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--max-steps", type=int, required=True)
    parser.add_argument("--examples-per-step", type=int, default=64)
    parser.add_argument("--microbatch-examples", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--max-gradient-norm", type=float, default=1.0)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--card-dim", type=int, default=64)
    parser.add_argument("--symbol-dim", type=int, default=16)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--checkpoint-interval-steps", type=int, default=50)
    parser.add_argument(
        "--value-coefficient", type=float, default=0.5,
        help="value head (critic) の損失重み。0 で critic を学習しない。"
             "既定で学習するのは、学習しないと θ0 が乱数初期化の critic を RL へ渡し、"
             "V-trace が baseline を一から学ぶ間 policy gradient が雑音を basline に "
             "するため (docs/evidence/vtrace-degenerate-collapse-20260804.md)",
    )
    parser.add_argument("--usage-boundary", default="local_eval_only")
    parser.add_argument("--output-base", default="runs/meta-specialist-bc-distill")
    parser.add_argument(
        "--progress-path", default="",
        help="進捗を atomic に書く JSON。並列 supervisor はこれを読んで描画する",
    )
    parser.add_argument(
        "--torch-threads", type=int, default=0,
        help="torch の intra-op スレッド数。0 で torch 既定 (全コア) だが、既定のままに "
             "してはいけない。このモデルのテンソルは 1 マイクロバッチ 16 例 x 最大 30 "
             "トークン x hidden 128 と小さく、OpenMP のバリア同期が演算量を上回るため、"
             "スレッドを増やすほど遅くなる。28 コア機での実測は 2 スレッドが最速で、"
             "4 で 1.04 倍、7 で 1.97 倍、14 で 4.33 倍、28 で 37 倍遅い "
             "(docs/evidence/bc-thread-oversubscription-20260807.md)。2〜4 を渡すこと",
    )
    parser.add_argument(
        "--read-workers", type=int, default=0,
        help="corpus shard を読む並列プロセス数。0 で全コア。--torch-threads とは別物で、"
             "こちらはプロセス並列なのでコア数まで素直に速くなる。両者を同じ値に縛ると、"
             "計算を速くするために下げた値が起動時の shard 読み込みまで遅くする",
    )
    args = parser.parse_args()

    if args.torch_threads:
        if args.torch_threads < 1:
            raise SystemExit("--torch-threads must be a positive int (or 0 for the default)")
        torch.set_num_threads(args.torch_threads)
    if args.read_workers < 0:
        raise SystemExit("--read-workers must be nonnegative (0 for every core)")

    # A sharded corpus is named by its index; a single corpus by its snapshot.
    if Path(args.snapshot).name == "snapshot_index.json":
        # Revalidating every shard dominates start-up -- measured 4.6 ms per
        # example, i.e. 21 minutes for a 190,000-example training split -- and
        # shards are independent, so use the core budget this run was given.
        train = read_sharded_split_examples_v1(
            Path(args.snapshot), "train",
            workers=args.read_workers or (os.cpu_count() or 1),
        )
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        # θ0 の由来は index が記録した source_artifacts から引く。identity は
        # shard の集合そのものにするため、index の内容から決める。
        snapshot["snapshot_id"] = hashlib.sha256(
            json.dumps(snapshot["shards"], sort_keys=True).encode("utf-8")
        ).hexdigest()
    else:
        snapshot = read_training_snapshot_v1(Path(args.snapshot))
        train = snapshot_examples_for_split_v1(snapshot, "train")
    if not train:
        raise SystemExit("snapshot has no train split examples")

    foundation_init = foundation_init_from_snapshot_v1(
        snapshot,
        teacher_id=args.teacher_id,
        usage_boundary=args.usage_boundary,
        derivation_boundary=DERIVATION_QUALIFIED_V1,
        decision_ref=args.decision_ref,
        notes=f"BC distilled from {args.teacher_id} snapshot {snapshot['snapshot_id'][:16]}",
    )

    _q, _lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id=args.archetype_id,
        deck_csv_path=Path(args.deck_csv),
        source_commit="0" * 40,
    )
    config = SpecialistModelConfigV1(
        card_vocabulary_size=max(vocabulary.recognized_card_ids),
        hidden_dim=args.hidden_dim, card_dim=args.card_dim, symbol_dim=args.symbol_dim,
    )
    model = build_specialist_policy_model_v1(config, seed=args.seed)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    row_logits = make_specialist_row_logits_v1(model)
    state_values = (
        make_specialist_state_values_v1(model) if args.value_coefficient > 0.0 else None
    )

    recipe = {
        "objective": "behavior_cloning",
        "learning_rate": args.learning_rate,
        "examples_per_step": args.examples_per_step,
        "max_gradient_norm": args.max_gradient_norm,
        "teacher_id": args.teacher_id,
        "snapshot_id": snapshot["snapshot_id"],
        "value_coefficient": args.value_coefficient,
    }
    identity = build_training_identity_v1(
        snapshot_id=snapshot["snapshot_id"], config=config, recipe=recipe, seed=args.seed
    )

    out = Path(args.output_base) / args.run_name
    out.mkdir(parents=True, exist_ok=True)
    reporter = ProgressReporterV1(
        total=args.max_steps, desc=f"bc-distill {args.run_name}",
        progress_path=args.progress_path or None,
    )
    reporter.note(f"[bc-distill] start run={args.run_name} train_examples={len(train)} "
                  f"steps={args.max_steps} teacher={args.teacher_id}")

    generator = torch.Generator().manual_seed(args.seed)
    started = time.time()
    history: list[dict] = []
    last_path = None
    for step in range(1, args.max_steps + 1):
        idx = torch.randint(
            0, len(train), (min(args.examples_per_step, len(train)),), generator=generator
        )
        batch = [train[int(i)] for i in idx]
        result = training_step_v1(
            batch, model=model, optimizer=optimizer, row_logits=row_logits,
            microbatch_examples=args.microbatch_examples,
            max_gradient_norm=args.max_gradient_norm,
            state_values=state_values,
            value_coefficient=args.value_coefficient,
        )
        history.append({
            "step": step,
            "loss": float(result.loss),
            "value_loss": float(result.value_loss),
            "grad_norm": float(result.gradient_norm),
            "applied": not bool(result.skipped),
            "rows": int(result.rows),
        })
        reporter.update(
            1, loss=float(result.loss), vloss=float(result.value_loss),
            grad=float(result.gradient_norm),
            rows=int(result.rows), skipped=sum(1 for h in history if not h["applied"]),
        )
        if step % 10 == 0 or step == args.max_steps:
            _atomic_write_json(out / "progress_summary.json", {
                "completed": step, "total": args.max_steps,
                "elapsed_seconds": round(time.time() - started, 1),
                "recent": history[-10:],
            })
        if step % args.checkpoint_interval_steps == 0 or step == args.max_steps:
            payload = build_checkpoint_payload_v1(
                model=model, optimizer=optimizer, scheduler=None, identity=identity,
                recipe=recipe, step=step, sampler_cursor=step,
                foundation_init=foundation_init,
            )
            last_path = publish_checkpoint_v1(out / "checkpoints", payload)
            reporter.note(f"[bc-distill] checkpoint step={step} {last_path.name}")

    summary = {
        "schema_version": "specialist-bc-distill-run-v1",
        "run_name": args.run_name,
        "archetype_id": args.archetype_id,
        "teacher_id": args.teacher_id,
        "snapshot_id": snapshot["snapshot_id"],
        "train_examples": len(train),
        "steps": args.max_steps,
        "elapsed_seconds": round(time.time() - started, 1),
        "first_loss": history[0]["loss"] if history else None,
        "last_loss": history[-1]["loss"] if history else None,
        "steps_skipped": sum(1 for h in history if not h["applied"]),
        "foundation_init": foundation_init.to_dict(),
        "foundation_init_id": foundation_init.foundation_init_id(),
        "final_checkpoint": None if last_path is None else str(last_path),
    }
    reporter.close()
    _atomic_write_json(out / "run_summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "foundation_init"},
                     ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
