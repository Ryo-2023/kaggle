# Meta Specialist v3 Phase 0 Preflight Evidence

実行日: 2026-08-08 (Asia/Tokyo)  
実行 worktree: `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical`  
branch: `feature/meta-specialist-canonical`  
HEAD: `a4e6475255ff7ac56469f87cfd0ca6214de749af`

この記録は、`docs/META_SPECIALIST_V3_LUNA_MAX_IMPLEMENTATION_EXPERIMENT_PLAN.md` のPhase 0に従って、既存変更を保持したまま実施した事前確認の証拠である。実験用の追加成果物は `runs/meta-specialist-v3/preflight/` と `runs/meta-specialist-actor-pool/v3-preflight-*` に保存した。

## 1. Worktree census

開始時点でworktreeはdirtyだった。今回の作業開始前から存在する変更を戻していない。

- 変更済み: `deck.csv`、opponent manifest、parallel runner、既存meta-specialist実装・テストなど
- 未追跡: opponent assets、既存evidence、既存run補助スクリプトなど
- Phase 0で追加された主な成果物: `runs/meta-specialist-v3/preflight/*`、`docs/evidence/meta-specialist-v3-phase0-preflight-20260808.md`
- `git diff --binary` のSHA-256: `3a4f6337a7e78840137a35e0376b1924615286718988938548d204172d60ddf9`

環境:

- Python 3.12.3
- NVIDIA GPUは存在しているが、この実行環境ではNVMLがOSにより拒否された。
  `nvidia-smi` は `GPU access blocked by the operating system` で失敗した。
- したがってPhase 0の実験はGPU性能を根拠にせず、CPU実行として扱う。

## 2. Existing focused tests

計画書の指定コマンドをそのまま実行した。

```text
PYTHONPATH=. pytest tests/meta_specialist/test_actor_pool_v1.py -q
74 passed in 13.74s

PYTHONPATH=. pytest tests/meta_specialist/test_collect_trajectories_cli.py -q
37 passed in 7.98s

PYTHONPATH=. pytest tests/meta_specialist/test_train_from_trajectories.py -q
19 passed, 2 skipped in 2.54s
```

この時点で、Phase 0開始時点の変更に対するfocused regressionは通過した。neural系を含む全suite greenとは主張していない。

## 3. Evaluation reproducibility probe

### 3.1 共通条件

v2 BC checkpoint、同一source commit、同一lane、同一`env_seed`、同一seat、同一opponent (`cabt_rule_agent_v0`)、同一`max_steps=2000`、greedy decodingで比較した。各runは8局、2 worker、同じjob identityを使用した。

checkpoint:

- Alakazam: `runs/meta-specialist-bc-distill/v2smoke-alakazam/checkpoints/checkpoint-7849171dc6e70336a0222e991831a7b1df978ba9ebd87324a7dd61e172d05e79.pt`
- Archaludon: `runs/meta-specialist-bc-distill/v2smoke-archaludon/checkpoints/checkpoint-fdbf75cbb2fcd11b111c2e86795c33bba89a1499c4a29898c0ca7055aa5341b0.pt`

実行した代表コマンド:

```bash
PYTHONPATH=.:src python -m mage_ptcg.meta_specialist.cli collect-trajectories \
  --lanes alakazam --num-games 8 --base-seed 5710000 --workers 2 \
  --run-name v3-preflight-fresh-alakazam \
  --behavior-kind neural_specialist \
  --neural-checkpoint-path runs/meta-specialist-bc-distill/v2smoke-alakazam/checkpoints/checkpoint-7849171dc6e70336a0222e991831a7b1df978ba9ebd87324a7dd61e172d05e79.pt \
  --decoding-mode greedy --timeout-seconds 30 --max-steps 2000 \
  --opponent-kind cabt_rule_agent_v0 --json
```

同じ条件で`v3-preflight-persistent-*`（`--persistent-worker`あり）と`v3-preflight-fresh2-alakazam`（freshの再実行）も収集した。

### 3.2 Run summary

| run | lane | worker mode | attempted | completed | faulted | transitions | wall time |
|---|---|---:|---:|---:|---:|---:|---:|
| `v3-preflight-fresh-alakazam` | alakazam | fresh process/game | 8 | 8 | 0 | 548 | 18.203s |
| `v3-preflight-persistent-alakazam` | alakazam | persistent worker | 8 | 8 | 0 | 373 | 8.820s |
| `v3-preflight-fresh2-alakazam` | alakazam | fresh process/game | 8 | 7 | 1 | 434 | 20.2s |
| `v3-preflight-fresh-archaludon` | archaludon | fresh process/game | 8 | 8 | 0 | 397 | 14.427s |
| `v3-preflight-persistent-archaludon` | archaludon | persistent worker | 8 | 8 | 0 | 358 | 6.787s |

`run_summary.json` はそれぞれ以下にある。

```text
runs/meta-specialist-actor-pool/v3-preflight-fresh-alakazam/run_summary.json
runs/meta-specialist-actor-pool/v3-preflight-persistent-alakazam/run_summary.json
runs/meta-specialist-actor-pool/v3-preflight-fresh2-alakazam/run_summary.json
runs/meta-specialist-actor-pool/v3-preflight-fresh-archaludon/run_summary.json
runs/meta-specialist-actor-pool/v3-preflight-persistent-archaludon/run_summary.json
```

### 3.3 Record-level comparison

比較キーは同じjob idであり、recordから`seed`、`seat`、`winner`、`steps`、transition数、chosen action `content_hash`列を比較した。

- Alakazam fresh vs persistent: 8/8局が不一致
- Archaludon fresh vs persistent: 8/8局が不一致
- Alakazam fresh vs fresh再実行: 共通7局の7/7局が不一致。再実行側は1局faulted
- persistent経路では、例えば同じseed `5710000`でもfreshは`steps=56`、`transitions=42`、`winner=0`、persistentは`steps=21`、`transitions=9`、`winner=1`だった。
- fresh同士でも同じseed `5710000`は`winner=0`と`winner=1`に分かれた。

したがって、現在のnative CABT engineでは、seedを固定しても同一gameの完全なaction/state replayは成立しない。さらにpersistent workerはfresh workerと比べて遷移数・勝敗・action列が大きく変わるため、単なる速度最適化ではなく、ゲームライフサイクルの意味を変えている。

### 3.4 Gate 0.3 decision

**判定: exact replay gateは不通過。ただし標準評価経路は決定した。**

以後の標準は次のとおり。

1. 学習・評価のworkerは`persistent_worker=False`（game-local fresh process）を使用する。
2. 同一seedの完全再現を性能評価の前提にしない。
3. candidateとbaselineを同じsealed ledger（opponent、deck fingerprint、seat、env seed、agent seed derivation）でpaired evaluationする。
4. 各gameのrecord hash、fault、state/action traceを保存し、再実行差分を隠さない。
5. persistent workerは速度比較またはnegative controlに限定し、promotion evidenceには使わない。

この決定は計画書の「persistent actor/opponent object vs game-local fresh object」の比較要求を満たす。native engineの非決定性は残存リスクとしてPhase 5のevaluation protocolへ引き継ぐ。

## 4. RNG/lifecycle audit findings

- gameごとの`sampling_seed`はjob identityから導出され、同一run内で共有されない。
- neural policy wrapperはgameごとに新規policy wrapperを生成する。
- default actor poolは1 game/processで終了し、OS-level timeout/killを持つ。
- persistent workerは同一process内で複数gameを実行するため、CABT engine・依存モジュール・内部キャッシュの再初期化契約がない。
- 実測でpersistentとfreshのrecordが完全に分岐したため、既存の「persistentは速いだけ」という仮定は採用しない。
- fresh同士の差分はnative engine側の非決定性を示す。`scripts/test_sim.py`の環境生成・`env.run`・engine内部乱数を、完全なgame seed ledgerとして制御できていない。

追加の修正候補は、Phase 5のfault/evaluation instrumentationで、engine state hashとaction sequenceを明示的に保存し、persistentを再び標準へ戻す前に同一engine identityテストを通すことである。

## 5. Phase 0 conclusion

- Gate 0.1: **pass**。dirty worktreeと既存変更を認識し、破壊操作なし。
- Gate 0.2: **pass**。指定focused testsは全てpass（2 skippedは既存条件）。
- Gate 0.3: **conditional pass**。exact replayは不可能だが、fresh process + sealed paired ledgerを標準評価方式として固定した。
- Gate 0.4: **partial**。job-local sampling seedとfresh lifecycleは確認できた。native engine内部の完全なRNG再現性は未解決で、Phase 5へ引き継ぐ。

この結果を踏まえ、計画のPhase 1（relation/invariance testsを先行）へ進む。exact replay failureを理由に少数局の勝率を性能改善として解釈してはならない。
