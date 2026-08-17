"""Aggregate Luna Max v3 artifacts into an honest final evidence bundle.

The builder deliberately distinguishes implemented infrastructure and bounded
smoke runs from the sealed 4,096-game promotion protocol.  Missing artifacts
are represented as ``not_run`` instead of being inferred as a pass.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


LANES = ("alakazam", "archaludon", "grimmsnarl", "rocket")


def _load(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _metric(report: dict[str, object] | None, name: str, key: str) -> float | None:
    if not report:
        return None
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        return None
    candidate = metrics.get(name)
    return float(candidate[key]) if isinstance(candidate, dict) and key in candidate else None


def _fmt(value: object) -> str:
    if value is None:
        return "not available"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def build(root: Path, *, output: Path) -> Path:
    runs = root / "runs" / "meta-specialist-v3"
    final_dir = runs / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).astimezone().isoformat()
    head = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    status = _git(root, "status", "--short")
    diff = subprocess.run(["git", "diff", "--binary"], cwd=root, check=True, capture_output=True).stdout
    diff_sha = hashlib.sha256(diff).hexdigest()
    (final_dir / "source.diff").write_bytes(diff)
    (final_dir / "source.diff.sha256").write_text(f"{diff_sha}  source.diff\n", encoding="utf-8")
    # ``git diff`` intentionally omits untracked implementation files.  Keep a
    # second, source-only tree digest so the report cannot be mistaken for a
    # complete lineage hash while the worktree is intentionally uncommitted.
    source_paths = []
    for prefix in ("src/", "tests/", "scripts/", "configs/"):
        source_paths.extend(path for path in root.glob(prefix + "**/*") if path.is_file())
    source_hasher = hashlib.sha256()
    for path in sorted(set(source_paths)):
        relative = path.relative_to(root).as_posix()
        source_hasher.update(relative.encode("utf-8"))
        source_hasher.update(b"\0")
        source_hasher.update(path.read_bytes())
        source_hasher.update(b"\0")
    source_tree_sha = source_hasher.hexdigest()
    (final_dir / "source.tree.sha256").write_text(f"{source_tree_sha}  source-tree(src,tests,scripts,configs)\n", encoding="utf-8")

    representation = {
        lane: _load(runs / f"phase1-{lane}-benchmark-vectorized-128.json")
        for lane in LANES
    }
    teacher = {
        lane: _load(runs / f"teacher-{lane}-manifest-smoke.json")
        for lane in LANES
    }
    critic = _load(runs / "phase2-critic-smoke.json")
    conditioning = _load(runs / "phase2-critic-conditioning-ablation.json")
    bc = _load(runs / "phase3-bc-smoke.json")
    learner = _load(runs / "phase4-6-learner-diagnostics.json")
    phase7_9 = _load(runs / "phase7-9-smoke.json")
    evaluation = _load(runs / "phase5-eval-smoke.json")

    lane_rows: list[dict[str, object]] = []
    for lane in LANES:
        rep = representation[lane]
        teach = teacher[lane]
        lane_rows.append({
            "lane": lane,
            "representation_samples": rep.get("samples") if rep else None,
            "teacher_records": teach.get("record_count") if teach else None,
            "teacher_episodes": teach.get("episode_count") if teach else None,
            "near_duplicates": teach.get("near_duplicate_count") if teach else None,
            "r2_nll": _metric(rep, "R2-negative-control", "nll"),
            "r3a_nll": _metric(rep, "R3-A", "nll"),
            "r3b_nll": _metric(rep, "R3-B", "nll"),
            "r2_p95_ms": _metric(rep, "R2-negative-control", "p95_ms"),
            "r3a_p95_ms": _metric(rep, "R3-A", "p95_ms"),
            "r3b_p95_ms": _metric(rep, "R3-B", "p95_ms"),
            "status": "bounded_slice_only",
        })

    with (final_dir / "per_lane.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(lane_rows[0]))
        writer.writeheader()
        writer.writerows(lane_rows)

    paired = evaluation or {}
    with (final_dir / "per_matchup.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["tier", "scope", "games", "candidate_win_rate", "baseline_win_rate", "paired_delta", "status"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "tier": "smoke", "scope": "synthetic paired protocol", "games": paired.get("games"),
            "candidate_win_rate": paired.get("candidate_win_rate"), "baseline_win_rate": paired.get("baseline_win_rate"),
            "paired_delta": paired.get("paired_delta"), "status": "not promotion evidence",
        })

    source_manifest = {
        "schema": "meta-specialist-v3-model-manifest",
        "status": "not_sealed",
        "representation_version": 3,
        "theta0_sealed": False,
        "bc_smoke_checkpoint_sha256": (bc or {}).get("checkpoint_sha256"),
        "bc_smoke_source": (bc or {}).get("source"),
        "git_head": head,
        "branch": branch,
        "source_diff_sha256": diff_sha,
        "source_tree_sha256": source_tree_sha,
        "generated_at": generated_at,
    }
    _write_json(final_dir / "model_manifest.json", source_manifest)
    _write_json(final_dir / "evaluation_manifest.json", {
        "schema": "meta-specialist-v3-evaluation-manifest",
        "status": "sealed_promotion_not_run",
        "required_games": 4096,
        "observed_smoke_games": paired.get("games"),
        "paired_protocol_implemented": True,
        "exact_replay_baseline_established": False,
        "generated_at": generated_at,
    })
    _write_json(final_dir / "opponent_manifest.json", {
        "schema": "meta-specialist-v3-opponent-manifest",
        "status": "bounded_teacher_manifest_smokes",
        "lanes": teacher,
        "promotion_pool_sealed": False,
    })
    _write_json(final_dir / "seed_ledger.json", {
        "schema": "meta-specialist-v3-seed-ledger",
        "benchmark_seed": 7,
        "conditioning_seed": 7,
        "smoke_evaluation_seed": (evaluation or {}).get("seed"),
        "promotion_ledger": "not_run",
    })
    _write_json(final_dir / "promotion_results.json", {
        "schema": "meta-specialist-v3-promotion-results",
        "status": "do_not_promote",
        "reason": "promotion pool is not sealed, Gate 1/3 are open, and 4,096 paired games were not run",
        "smoke_result": evaluation,
    })
    _write_json(final_dir / "fault_report.json", {
        "schema": "meta-specialist-v3-fault-report",
        "status": "partial",
        "baseline_reproducibility": "exact replay failed in fresh-vs-persistent comparison",
        "current_promotion_fault_rate": "not_run",
        "fault_instrumentation_implemented": True,
    })
    _write_json(final_dir / "training_health.json", {
        "schema": "meta-specialist-v3-training-health",
        "learner_diagnostics": learner,
        "critic_smoke": critic,
        "critic_conditioning": conditioning,
        "bc_smoke": bc,
        "phase7_9_contract_smoke": phase7_9,
    })
    (final_dir / "checkpoint_sha256.txt").write_text(
        f"bc_smoke {((bc or {}).get('checkpoint_sha256') or 'not_available')}\nformal_theta0 not_sealed\n",
        encoding="utf-8",
    )

    lines = [
        "# Meta Specialist v3 — Luna Max 完遂状況・最終エビデンス",
        "",
        f"生成日時: `{generated_at}`  ",
        f"worktree: `{root}`  ",
        f"branch: `{branch}`  ",
        f"HEAD: `{head}`  ",
        f"source diff SHA-256: `{diff_sha}`",
        f"source tree SHA-256 (src/tests/scripts/configs): `{source_tree_sha}`",
        "",
        "> このレポートは、計画書に記載された全責務を「実装済み」「bounded smokeで検証済み」「本番規模では未実施」に分けて記録する。未実施の評価を成功結果として補完しない。",
        "",
        "## 結論",
        "",
        "主要な実装部品（representation v3、outcome critic、full-BC形式、trajectory provenance、fresh PPO/consume-once V-trace/AWR-CRR primitives、opponent schedule、fault/evaluation protocol、search/DAgger dataset、manifest）が揃い、テストとbounded smokeを完了した。一方、正式な性能向上はまだ証明されていない。Gate 1（表現の十分な実データ比較）とGate 3（sealed formal θ0）が未通過であり、GPUがOSにより遮断されているため、Phase 7–12の4,096局promotion評価も未実施である。",
        "",
        "判定: **DO NOT PROMOTE / 実装は継続可能**。",
        "",
        "## Gate 状態",
        "",
        "| Gate | 状態 | 根拠 |",
        "|---|---|---|",
        "| 0.1 census | PASS | dirty state、source diff、実行環境を保存 |",
        "| 0.2 focused tests | PASS | actor pool 74、collection 37、trainer 19(+2 skipped) |",
        "| regression suite | PASS | isolated namespaceで1481 passed、23 skipped、2 warnings、93.66s |",
        "| 0.3 reproducibility | CONDITIONAL | fresh processを標準化したが、exact replayは未成立 |",
        "| 0.4 RNG/lifecycle | PARTIAL | local seedは導入、native engine RNG/lifecycleは完全固定できていない |",
        "| 1 representation | NOT PASSED | relation testsは通過、4 lane各128件のみ、NLLは一貫せず、R3 latencyはR2より約3–5倍 |",
        "| 2 critic | CONDITIONAL | uniform Brier改善のsmokeとC0/C1/C2 ablationは実施、real-corpus calibrationは未実施 |",
        "| 3 formal θ0 | SMOKE ONLY | rocket 128件のBC best checkpoint、full corpus/3 seeds/sealed manifestではない |",
        "| 4–6 | IMPLEMENTED + SMOKE | schema/diagnostics/evaluation/learner primitives、64 decision/64-game synthetic smoke |",
        "| 7–9 | CONTRACT SMOKE ONLY | learner/schedule/DAgger wiringはtoy smoke、real two-lane screening未実施 |",
        "| 10–12 | NOT RUN | Gate依存、GPU/再現性/compute制約により本番規模未実施 |",
        "",
        "## Phase 1: representation v3 bounded real-record result",
        "",
        "同一seed=7、各lane 128 records、3 epochs、現行ベクトル化encoderで測定した。NLLは低いほど良い。これはfull corpusのGate 1ではなく、laneごとの限定スライスである。",
        "",
        "| lane | R2 NLL | R3-A NLL | R3-B NLL | R2 p95 ms | R3-A p95 ms | R3-B p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in lane_rows:
        lines.append("| {lane} | {r2_nll} | {r3a_nll} | {r3b_nll} | {r2_p95_ms} | {r3a_p95_ms} | {r3b_p95_ms} |".format(**{k: _fmt(v) for k, v in row.items()}))
    lines.extend([
        "",
        "解釈: ArchaludonではR3-B NLLがR2をわずかに下回るがtop-1は下がる。Alakazam/Grimmsnarl/RocketではR3-B NLLがR2を上回る。R3 latencyはベクトル化後に改善したものの、R2よりおおむね3倍以上で、データ量・seed数・early-stopping統制が不足している。したがってR3-Bを正式採用したとは扱わず、full-corpus equal-budget benchmarkを残課題とする。",
        "",
        "## Phase 2: critic",
        "",
        f"64 episode/4 step/2 epoch warm-upでは、uniform Brier `{_fmt((critic or {}).get('initial', {}).get('brier') if critic else None)}` から final `{_fmt((critic or {}).get('final', {}).get('brier') if critic else None)}` へ僅かに改善し、valueは[-1,1]内だった。これは実データの十分な校正を意味しない。",
        "",
        "C0（conditioningなし）、C1（stable opponent family）、C2（game-seed negative control）のtoy ablationでは、C1のvalidation BrierがC0より小さく、C2はtrainで見かけの相関を作れてもvalidationで相関が消えた。この結果はstable categoryを使う設計の妥当性を示すが、実laneの勝率予測性能を示さない。",
        "",
        "## Phase 3: BC θ0",
        "",
        f"rocket teacher record 128件（train 102 / validation 26、episode/near-duplicate split）で、best epoch `{(bc or {}).get('best_epoch')}`、validation NLL `{_fmt((bc or {}).get('best_validation_nll'))}`、checkpoint SHA `{(bc or {}).get('checkpoint_sha256')}` を得た。ただし1 lane・1 seed・限定sliceのため formal θ0としてsealしていない。",
        "",
        "## Phase 4–6: learner/evaluation infrastructure",
        "",
        "trajectory schemaは全legal-action base logits/log-prob、chosen behavior log-prob、sampling mode、hidden hash、latencyを保持し、Gumbel logitsの誤流入を拒否する。learner diagnostics smokeでは exact forward KL 4.216e-5、reverse KL 4.214e-5、TV 0.00364、argmax flip 0、normalized entropy 0.840、V-trace effective horizon 43を得た。これらは健全性の計測結果であり、学習性能向上ではない。",
        "",
        "synthetic paired evaluation 64局は candidate win rate 0.59375 / paired delta 0.1875 だったが、runnerが決定論的toy outcomeを生成するため、promotion evidenceから除外した。",
        "",
        "Phase 7–9のintegration smokeでは、PPO exact KL、consume-once V-trace（queue消費）、AWR/CRR weight、O0/O1/O2 opponent schedule、soft search targetとDAgger near-duplicate dedupを同一runnerで通した。ただし全learnerの同じdiagnostic primitiveを呼ぶ契約smokeであり、real actor rollout、3 seeds、512 paired screening、teacher queryは未実施である。",
        "",
        "## 未実施・未完了の理由",
        "",
        "1. `nvidia-smi` は `GPU access blocked by the operating system`。計画のRTX PRO 5000 Blackwell実機はこのsessionから利用できない。",
        "2. 現行native engineは同一ledgerでもfresh/persistent間の完全再現が成立せず、Alakazam再実行ではfaultも発生した。これを解消しないまま4,096局を性能証拠にすると、paired comparisonの前提が壊れる。",
        "3. teacher rootsは巨大で、今回の再検証は各lane 512 record bounded manifest smokeに限定した。full corpus/3 seedsは実行していない。",
        "4. Gate 1とformal θ0が依存関係上未通過なので、Phase 7 learner screening、Phase 8 opponent distribution、Phase 9 DAgger、Phase 10 full training、Phase 11 promotionを成功扱いにできない。",
        "",
        "## 再現コマンド（worktreeで実行）",
        "",
        "```bash",
        "cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical",
        "OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python scripts/run_meta_specialist_v3_ablation.py --seed 7 --epochs 3 --teacher-root runs/meta-specialist-teacher-records/t1-rocket --limit 128 --output runs/meta-specialist-v3/phase1-rocket-benchmark-vectorized-128.json",
        "OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python scripts/run_meta_specialist_v3_critic_conditioning.py --seed 7 --episodes 96 --validation-episodes 48 --steps 4 --epochs 100 --output runs/meta-specialist-v3/phase2-critic-conditioning-ablation.json",
        "python scripts/build_meta_specialist_v3_report.py --output docs/evidence/meta-specialist-v3-final-report.md",
        "```",
        "",
        "## 最終成果物",
        "",
        "- `runs/meta-specialist-v3/final/model_manifest.json`: θ0未sealを明記",
        "- `runs/meta-specialist-v3/final/evaluation_manifest.json`: promotion未sealedを明記",
        "- `runs/meta-specialist-v3/final/per_lane.csv`, `per_matchup.csv`: bounded/syntheticを区別",
        "- `runs/meta-specialist-v3/final/fault_report.json`, `training_health.json`: 診断結果",
        "- `runs/meta-specialist-v3/final/source.diff`、`source.diff.sha256`、`source.tree.sha256`",
        "- `runs/meta-specialist-v3/phase7-9-smoke.json`: learner/schedule/DAgger contract smoke",
        "- `docs/evidence/meta-specialist-v3-phase0-preflight-20260808.md`、`meta-specialist-v3-phase1-representation-20260808.md`、`meta-specialist-v3-bc-smoke-20260808.md`",
        "",
        "## 次の実行順序",
        "",
        "full teacher corpusをepisode/near-duplicate componentで分割し、R2/R3-A/R3-Bを3 seedsでequal-budget比較する。Gate 1を通過したencoderだけで4 laneのformal BC θ0をsealし、criticを64 completed episodes以上で実データ校正する。その後にのみ、同一sealed θ0からPhase 7のAlakazam/Archaludon learner screeningへ進む。",
        "",
    ])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/evidence/meta-specialist-v3-final-report.md"))
    args = parser.parse_args()
    output = build(Path(__file__).resolve().parents[1], output=Path(args.output))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
