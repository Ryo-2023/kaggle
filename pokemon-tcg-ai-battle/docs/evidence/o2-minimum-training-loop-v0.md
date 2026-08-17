---
project: MAGE-PTCG
evidence_status: actual-lineage-contract-implemented
as_of: 2026-07-19
---

# O2 Minimum Viable Training Loop v0

## 結論

Deck／Opponent Pool、決定的な seat-swap match plan、atomic resume、paired 集計、非自動 Promotion Report と、既存 Offline Training v1 の fixture training を一つの CLI から実行できる。Champion と submission default は変更しない。

Real CABT Bridge により、allowlistされた Rule Agent v0／Random Legal Agent と実 `deck.csv` を実cabtへ渡す adapter を追加した。実cabt smokeは4/4 `DONE`、既存C4 collectorは4 episode／87 supervised decisionを生成した。ただし既存gateは4 game collectionを`COLLECTION_SMOKE`、生成modelを`NEURAL_FIXTURE_SMOKE`として扱うため、複数試合actual paired評価はfail-closedで拒否される。

## 実測した fixture smoke

`python -m mage_ptcg.o2_training_loop.cli --config configs/competition/training_loop_o2_v1.yaml --output-dir /tmp/o2-loop-train --run-id o2-fixture-smoke --run-offline-training` を実行した。

| 項目 | 結果 |
| --- | --- |
| Pool match | 2 planned / 2 completed（`fixture_backend`） |
| Offline collection | 4 episodes / 24 decisions、privacy violation 0 |
| Dataset split | train 2 / validation 1 / test 1 episodes |
| Training | 2 epochs、`NEURAL_FIXTURE_SMOKE` |
| Package verify | 8/8 legal、illegal 0、verified=true |
| Paired evaluation | 1 fixture pair、Promotion `INSUFFICIENT_EVIDENCE` |

この結果は cabt の性能または Challenger の優位性の根拠ではない。fixture backend と fixture dataset は metadata 上も `ACTUAL_CABT_NOT_RUN`／`fixture_backend` と区別され、Promotion Report は `INSUFFICIENT_EVIDENCE` を返す。

## Real CABT Bridge smoke

| 項目 | 結果 |
| --- | --- |
| Rule v0 vs Random Legal | 2 seed × seat swap = 4/4 `DONE` |
| backend | `cabt`。fixture fallbackなし |
| deck | `repository-default-v1`、canonical deck hash `aa5d50ee…2bd93dd7` |
| C4 actual collection | 4 episode、87 supervised decision、354 candidates、private binding 87 |
| C4 split | train 3 / validation 1、overlap 0、duplicate decision 0 |
| privacy | scan実行済み、violation 0 |
| Student Gate A | 1 actual game、21 model selection、fallback/illegal/crash/timeout 0 |

cabt engineは`engine_seed_supported=false`を報告した。agent seedとmatch planは決定的だが、engine outcomeの同一seed再現は主張しない。既存artifact-purpose gateにより4 game actual paired evaluationは`ViabilityError`で拒否され、complete pairは0、Promotion判定は`INSUFFICIENT_EVIDENCE`である。

## Actual-trained viability smoke

既存C4 gateの閾値（24 episode、800 decision、3,000 candidate、split/privacy/binding整合）を変更せず、source=`actual` の40-game collectionを実行した。40 episode、1,722 supervised decision、actual-only training dataset 1,680 records（train/validation/test = 20/10/10）となり、`NEURAL_ACTUAL_TRAINED` export（model hash `7cea1e67…c37854a4`）を生成した。package clean-room verifyは8/8 legal、illegal 0だった。

actual viability runnerの16-game seat-balanced runは、`engine_seed_supported=false`、`engine_outcomes_deterministic=false`である。従ってこれは`pairing_mode=seat_matched_unseeded`、`exact_paired_inference=false`、`promotion_eligible=false`のsmokeである。16/16 DONE、invalid/crash/timeout/fallback/privacy violation はすべて0、Student model selection 395、Student decision latency p50 3.40 ms／p95 13.32 ms、match latency p50 0.175 s／p95 0.570 sだった。

## 再利用した正典実装

- 60枚 deck validation: `main.validate_deck`
- canonical hashing / atomic output: `mage_ptcg.competition_intelligence.canonical` / `atomic_io`
- Replay normalization / decision eligibility / permission: Competition Intelligence sidecar
- collection / split / train / export / package / clean-room verify: Offline Training v1

## 解消済みだった実 cabt gate（→ O2→C4 Actual Lineage Contract で解消）

旧記述: 「C4 collectorは`collect_actual_dataset()`がRule v0 self-play専用であり、O2のRule-vs-Random match IDをそのprivate binding episode IDへ直接渡す入力をまだ公開していない」。この入力契約は下記「O2 → C4 Actual Lineage Contract v0」で解消した。少なくとも50 complete pairを含むPromotion候補評価は、`ACTUAL_TRAINED` purposeを得る十分なactual collection後に既存actual viability runnerへ渡す方針は変更していない（今回のsmokeも16-game、pairs<100のためPromotion対象外）。

## O2 → C4 Actual Lineage Contract v0（2026-07-19）

### 結論

O2のmatch plan（`match_id`／`plan_hash`／seat／own・opponent agent・deck）を、既存C4 collectorのprivate bindingへ後方互換に渡す入力契約を実装した。O2のRule-vs-Random match planをそのままC4 collectorへ渡し、実cabt 40 matchから`NEURAL_ACTUAL_TRAINED` artifactを再生成し、16-game seat-balanced smokeで評価した。C4のprivate binding schema・episode schemaは複製していない。既存Rule self-play呼び出し（legacy mode）は完全後方互換である。commit範囲は `94ac2a9..4bea803`（branch `feature/o2-minimum-training-loop`）。

### 実装

- `mage_ptcg.dataops.collector.ActualEpisodeLineageInput`: 一時的な入力DTO（永続schemaではない）。`match_id`／`plan_hash`／`match_spec_hash`／`backend_kind`／`requested_seed`／`engine_seed_supported`／`seat_index`／`player_side`／own・opponent の `agent_id`／`implementation_hash`／`deck_hash`／`pair_id`（seat-swap pair grouping用、最小限のoptional追加）を持つ。
- `collect_actual_dataset(..., episode_lineage_inputs=None, opponent_deck_path=None, opponent_agent_factory=None)`: 3つのoptional引数を追加。`episode_lineage_inputs=None`（既定）では従来通り自己対戦のみを行い、`episode_group_id`（`f"{run_id}-g{i}"`）・binding schema・config_hashは変更なし。指定時（O2 lineage mode）は次を検証してfail closedで拒否する: 件数不一致、match_id欠損・重複、seat/player_sideの不整合、own・opponent agent/implementation_hash/deck_hashのバッチ内不整合、`backend_kind != "cabt"`。own seatは既存のRule captureで、opponent seatは呼び出し側が渡す非captureのfactoryで実行するため、実際にRule-vs-opponentの対戦になる。episode identityはO2の`match_id`（`run_id`／timestamp／pathを含まない content hash）をそのまま使う。
- private bindingとdataset row metadataにのみ完全lineageを保存し（`binding["o2_lineage"]`、`metadata["o2_match_id"]`／`metadata["o2_pair_id"]`）、public側（`dataset_manifest.json`／`public_summary.json`）には`match_id`・`plan_hash`のみを追加する（`o2_lineage_present`／`o2_plan_hashes`／`o2_match_ids`）。両方とも既にO2自身の`match_plan.json`／`batch_manifest.json`で公開済みの識別子であり、seat・agent実装hash・deck hashは公開側に出さない。
- `mage_ptcg.o2_training_loop.c4_bridge`（新規）: O2の`build_match_matrix`／`resolve_real_agent`／`resolve_real_deck`をそのまま再利用し、`MatchSpec`から`ActualEpisodeLineageInput`を導出する`build_episode_lineage_inputs`と、単一challenger・単一opponentのMVP範囲でcollectorのO2 lineage modeを呼ぶ`run_o2_actual_collection`を追加。
- Seat-swap pairのleakage対策として、`split_by_episode_group`（collector）と`deterministic_episode_split`（offline_training）の両方に、`o2_pair_id`が存在する場合にのみ効くoptionalなgroup key引数を追加した（未指定時は既存split結果とbyte-for-byte同一であることをテストで確認済み）。これにより同一pairのepisodeは常に同じsplitへ入る。
- `scripts/run_o2_actual_lineage_pipeline.py`（新規）: O2 match planを実行してC4 O2 lineage modeで収集し、`Pipeline`の`build-dataset`／`train`／`export`／`evaluate`／`screen`／`package`／`verify`フェーズをそのまま再利用する。collect phaseだけを置き換え、既存gate・既存フェーズ実装は一切変更していない。
- `offline_training.dataset.build_dataset`に`source_plan_hash`（既定`"NONE"`）を追加。O2 modeでは各matchのplan_hashが個別値のため単一値に潰せず、`_collection_plan_fingerprint`でsorted listを1つのreference hashへ畳み込む。個々のplan_hashは`source_collection_hash`経由でcollectorの`dataset_manifest.json`（`o2_plan_hashes`、public）まで1 hopで到達できる。

### 双方向追跡

| 方向 | 経路 |
| --- | --- |
| O2 match_id → C4 episode ID | O2 modeでは`episode_group_id == match_id`（恒等） |
| C4 episode ID → O2 match_id | 同上（恒等） |
| C4 decision → O2 match_id | private dataset rowの`metadata["o2_match_id"]`（private） |
| dataset → plan hash | `dataset_manifest.json`(offline-training)`.source_plan_hash` → `source_collection_hash` → collectorの`dataset_manifest.json`(`o2_plan_hashes`、public) |
| artifact → source collection | export/checkpoint`.dataset_hash` = collectorの`dataset_hash`と一致（実測で確認） |

### 実測: actual collection 再実行（40 match、seed 9300–9319、20 seed×seat-swap）

| 項目 | 値 |
| --- | --- |
| backend_kind | `cabt`（fixture 0） |
| O2 match ID数 | 40（重複 0） |
| C4 episode ID数 | 40 |
| private binding数 | 931（row数と一致、duplicate decision 0） |
| captured decision | 1,062（教師targetなしの1件を除いた後 931 supervised decision） |
| candidate | 5,387 |
| privacy violation | 0（`privacy_scan_executed=true`） |
| collector split | train 30 / validation 10 episode、overlap 0 |
| dataset_status | `ACTUAL_TRAINING`（`performance_eligible=true`） |
| collector `dataset_hash` | `f4529f74b3cb6b2a5d7166d0d367b0cc6afd42a8fd04ec9b50a545c63e96114e` |

offline-training側（`dataset/canonical`）は record 911、episode 40、split train 20 / validation 10 / test 10。collector側の931件との差20件は、既存の decision-content-hash based cross-split quarantine（`_decision_hash`、O2導入前から存在）が捕捉した「別matchだが内容が同一な初手付近の決定」であり、今回追加したpair-aware groupingの副作用ではない（監査で確認、下記）。`dataset_manifest.json`(offline-training)の`dataset_hash`は`bf9960f11031762da6b98b9dd470319197bf3c8cffda67c312773b9a018bc91b`。

### Dataset leakage audit

| 検査 | 結果 |
| --- | --- |
| 同一episode overlap | 0（構造的に保証、既存whole-episode split） |
| 同一binding overlap | 0（`(episode_group_id, decision_index)`重複 0） |
| dataset record duplicate | 0（quarantineされた20件は非重複、内容衝突として除外） |
| seat-swapped counterpart（O2 pair）overlap | 0/20 pair（collector split、offline-training 3-way splitの両方で確認）。pair-aware group keyなしでの理論値は概ね高確率でoverlapが起きうることをsplit_seed 0–199（40 episode/20 pair相当）でシミュレーションし、group key追加により200 seed中overlap 0を確認済み |
| fixture contamination | 0（`o2_lineage["backend_kind"]`が全件`cabt`） |

### NEURAL_ACTUAL_TRAINED artifact

| 項目 | 値 |
| --- | --- |
| model_purpose | `NEURAL_ACTUAL_TRAINED` |
| model_hash（export/checkpoint） | `d503db29cdcd2833d02b771e9ecd9555573b240aea44c7a4d2ebc06dbbf407f1` |
| package archive_sha256 | `d358fc035a4778fe74ab98ef725b7903649b71bdcaa3e44f8fd51229169a6d54` |
| package_identity | `neural-student-v1-rule-v0-fallback` |
| clean-room verify | executed 8、legal 8、illegal 0、exception 0、verified=true |
| gate変更 | なし。gate bypassなし |

### Evaluation smoke（seat-balanced, unseeded）

`scripts/run_actual_agent_viability.py --challenger neural_student_package`で16 game。cabt engineは`engine_seed_supported=false`を継続して報告するため、これは`engine_seed_supported=false`・`engine_outcomes_deterministic=false`のseat交互（`seat_schedule_deterministic=true`、champion側 player_index が試合ごとに交互）unseeded smokeである。paired winrate評価やPromotion判定はこのsmoke単体では行っていない（100 pair未満）。

| 項目 | 値 |
| --- | --- |
| games | 16/16 DONE |
| gate_status | `CLEAN_PASS` |
| wins/losses/draws | 8/8/0 |
| timeouts／crashes／invalid_actions／privacy_violations | 0/0/0/0 |
| challenger legal_action_rate | 1.0（fallback 0） |
| challenger model artifact_hash | `d503db29cdcd2833d02b771e9ecd9555573b240aea44c7a4d2ebc06dbbf407f1`(= export/package model_hashと一致) |
| challenger latency | p50 4.12 ms／p95 12.53 ms（decision）、match p50 0.213 s／p95 0.703 s |

**Promotion判定**: `INSUFFICIENT_EVIDENCE`。この評価は O2 の `promotion_report()`（`minimum_pairs=100`）を経由しておらず、16 game は同基準に遠く満たないため、既存ルールに従い Promotion 対象外のままとした。Promotion Gate自体は変更・拡大していない。

### 既知の制約

- engine_seed_supportedは引き続き`false`であり、同一seedでの決定的再現は主張しない（O2既存の制約を継続）。
- 本smokeのbridgeはMVP範囲として単一own agent・単一opponentのみを対象とする（`c4_bridge.build_episode_lineage_inputs`はown/opponent agent idがバッチ内で複数になるとfail closedで拒否）。Opponent Poolの複数opponent同時対応は未実装（範囲外）。
- `dist/kaggle/neural-student-v1/`はビルドの度に上書きされる git-ignored staging先であり、正典はrun-scopedな`runs/o2-real-cabt/o2-actual-lineage-v0/package/`である。
