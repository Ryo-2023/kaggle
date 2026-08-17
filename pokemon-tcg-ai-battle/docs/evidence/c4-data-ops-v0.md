---
project: MAGE-PTCG
evidence_type: c4-data-ops-actual-dataset-pipeline
as_of: 2026-07-16
---

# C4 data-ops: privacy-safe actual-cabt training-data collection

## 結論

actual cabt の decision 時点から、既存 `RuleBCExample`（`rule-bc-v1`）と完全互換な private training dataset と episode-group split を生成できることを4 game smokeで実証し、その後32 gameの実測engineering gateを満たした。Codex 調査（[c4-actual-data-feasibility](c4-actual-data-feasibility.md)）が示した「actual data 0 件」を、public trace を疑似変換せず、actor-visible境界とprivate candidate bindingを維持したまま解消した。

status は `COLLECTION_SMOKE`（`performance_eligible=false`）。teacher は Rule Agent v0 のみで、目的は RULE_IMITATION、性能上限は RULE_LEVEL。性能主張には使わない。

32-game actual runのcollection、bundle、training、Gate A/Bの正典は[actual-trained evidence](c4-actual-trained-v0.md)である。この文書の4-game値はcollector初期smokeとして保持する。

## 収集した smoke（actual cabt）

| 項目 | 値 |
|---|---|
| environment | `kaggle_environments.make('cabt')`、`actual_execution_allowed=true` |
| source / teacher agent | Rule Agent v0（`rule` / `actual-viability-v0`）self-play |
| games committed | 4 / 4（すべて `DONE`） |
| episodes | 4 |
| decisions | 171 |
| candidates | 931 |
| private candidate bindings | 171 |
| chosen targets | 171（`chosen_target_count == decision_count`） |
| split | train 3 / validation 1、`split_overlap_count=0`、`duplicate_decision_count=0` |
| privacy | `privacy_scan_executed=true`、`privacy_violations=0` |
| feature schema | `student-v0-features-v1`、dimension 96（state 32 + action 64） |

再現コマンド（private artifact は Git 管理外 `.local_artifacts/c4_runs/<run_id>/`）:

```bash
python scripts/generate_c4_actual_dataset.py \
  --run-id smoke-4g --games 4 --base-seed 1000 \
  --canonical-base 4590a85d6f78a0bd413c41ad945747f59e221a5e
python scripts/validate_c4_dataset.py --run-dir .local_artifacts/c4_runs/smoke-4g
```

engine outcome は非決定的で、勝敗は本収集の目的ではない。

## 設計と正典再利用

新しいゲームエンジンや forward API は作らない。収集は既存の actual cabt 経路と C1/C4 の contract をそのまま再利用する。

- 対戦駆動: `scripts/test_sim.run_match`（= `env.run`）。`run_actual_agent_viability.py` は変更しない。
- 各 decision の actor-visible observation を `DecisionCaptureAgent`（Rule v0 delegate の thin wrapper）で捕捉し、`build_rule_bc_example`（`src/mage_ptcg/student/dataset.py`）で 1 行を構築する。
- actor-visible 境界は `build_decision_state` の allowlist に一致する。own hand は acting player の合法入力として private dataset にのみ残し、opponent の zone は count のみ（`hand_count` / `prize_count` / `deck_count`）。opponent hidden identity と raw observation 全体は保存しない。
- candidate identity は Stable `ActionKey`。private binding だけが option-index namespace（`cabt.select.option.index.v0`）と各 candidate の `to_canonical_payload()`、chosen option index、Rule v0 の完全 ranking を保持する。
- teacher ranking は `rank_rule_indices` の実スコアで、legal set をちょうど一度覆う。取得不能なら架空スコアを作らず、既存契約どおり optional prompt のみ neutral ranking を使う。source と teacher はともに Rule v0 なので chosen == teacher。

## 成果物とスキーマ

private（Git 管理外、own-card identity を含む）:

- `private_dataset/rule-bc-v1.jsonl`: `RuleBCExample` 行。`metadata` に `episode_group_id` / `decision_index` / `seat` / `source_agent` / `feature_schema_version` / `trace_provenance_hash` / teacher を追記（すべて string、trainer 互換）。
- `private_dataset/private_bindings.jsonl`: 1 行 1 decision の candidate binding。`rule-bc-v1.jsonl` と行順一致。

public（hash / count / schema / privacy のみ）:

- `dataset_manifest.json`: smokeでは`artifact_purpose=COLLECTION_SMOKE`、`performance_eligible=false`。counts、dataset/bindings hash、feature schema、teacherを保持する。
- `split_manifest.json`: `split_method=episode_group_hash_v0`、seed、train/validation episode count、`split_overlap_count`、`duplicate_decision_count`、`split_hash`。
- `public_summary.json`: 上記の要約に compute manifest（`cpu_count` / `cuda_available` / `gpu_count` / `gpu_names` / `recommended_training_device=cpu`）と privacy 結果。hostname / username / IP / 絶対 path は保存しない。
- `collection_state.json`: resume 状態（`config_hash`、`completed_game_indices`、`dataset_hash`、`split_hash`）。

## Split

episode（= 1 game）単位で train / validation を分ける。同一 episode を跨がせず、decision 単位の random split はしない。group は redacted `source_id`（= `episode_group_id`）で、`split_by_episode_group` は少数 episode でも train・validation 双方を非空にする決定的割当を使う。最低条件 `split_overlap_count=0` を満たす。episode が少ないため性能評価用ではなく `COLLECTION_SMOKE`。

## Privacy

private dataset と public summary を分離する。public 側は key 名走査（raw observation、card identity、own-hand、opponent hidden、candidate binding/payload、secret、email、signed URL、絶対 path）、`find_forbidden_keys`、`secret_scan` を通す。scan 未実行または violation ありでは status を PASS にしない。smoke は `privacy_violations=0`。

## Resumability

collection stage は per-game で resume 可能。`config_hash` 不一致の resume は拒否する。完了済み game（per-game file + state 記録）は再実行しない。二重 collect すると 0 game 実行で finalize のみ行う。

## 既知の制約と integration 要件

- `dataset_status=COLLECTION_SMOKE` / `performance_eligible=false`。性能主張不可。実訓練は Codex 側（consumer）がより多い game で再収集する。
- 既存 trainer の `split_examples`（`source_id` hash %100）は episode 数が少ないと空 partition で失敗しうる。4 episode では失敗を観測した。consumer は十分な episode 数を収集するか、本 lane の `split_manifest`（`split_by_episode_group`）を使う。
- trace_provenance_hash は raw trace bytes ではなく config・game seed・environment から導出する（raw observation を保存しない方針のため）。
- Champion / submission default / Promotion / `main.py` / `deck.csv` は不変。`run_actual_agent_viability.py` も不変。

## 検証

- focused: `pytest tests/test_c4_data_ops.py` → 26 passed。
- 回帰: `pytest tests/test_student_v0.py tests/test_actual_agent_viability.py tests/test_cabt_trace.py` → 58 passed。
- actual smoke: 上記コマンドで `status=PASS`。independent validator で `valid=true`、`privacy_violations=0`。
