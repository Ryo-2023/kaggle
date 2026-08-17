# Frozen Residual v1 runtime coverage audit — 2026-08-12

## 判定

ChatGPT Pro レビューの runtime coverage 要求に合わせ、研究専用 frozen residual sidecar へ実行時 counter と CABT runner の cell 別 ledger を追加した。production V4、Wave6 checkpoint、decoder、GRU、legal-action contract は変更していない。

今回の変更だけでは既存 48 局の結果を再解釈しない。既存の seed0/seed1 JSON は runner 更新前に生成され、`coverage.observed=false`、`reason=runtime sidecar counters not yet connected`、count 0 である。したがって、既存の勝率は residual が発火したかどうかを示さない。更新後 runner で再実行した JSON のみを coverage 実測として扱う。

## 実装

- `src/mage_ptcg/meta_specialist/frozen_residual_v1.py`
  - `ResidualCoverageSnapshotV1` を追加した。
  - `adjust_logits()` の各 decision で exact context SHA gate、semantic action SHA gate、STOP gate、malformed/arity pass-through を計測する。
  - total decision、valid/exact-known context、eligible/known/applied/nonzero action slot、top-1 change、STOP、OOD pass-through reason、action type、residual magnitude（mean/p50/p95/max）を保存する。
  - `delta()` は単調な snapshot 間差分を返し、`reset_coverage()` は新しい評価 ledger の開始に使える。
  - zero-init では known slot が `residual_applied` に計上されても `nonzero_residual=0`、top-1 change=0 になる。
- `src/mage_ptcg/meta_specialist/frozen_residual_factory_v1.py`
  - factory 経由で sidecar の `coverage_snapshot()` / `reset_coverage()` を取得できる。
- `scripts/run_frozen_residual_cabt_eval_v1.py`
  - seed 開始時に counter を reset。
  - 各 opponent × seat × game の前後 snapshot を取り、`by_opponent_seat_game` へ delta を保存する。
  - total coverage は `observed=true` として、exact gate の measured counters を出力する。
  - coarse public bucket は v1 runtime gate に未接続なので、`coarse_public_bucket_observed=false` を明示する。

## 監査結果

Focused test:

```text
32 passed
```

対象は zero-init base parity、exact known context/action coverage、held-out unknown context の OOD pass-through、snapshot delta/reset、runner coverage serializer、factory/loader/preflight contract である。`py_compile` と `git diff --check` も pass した。

synthetic held-out audit では、未知 context は base logits と完全一致し、`exact_known_context=0`、`residual_applied_slots=0`、`ood_pass_through=1`、reason=`unknown_context` となることを確認した。これは CABT の実ゲーム coverage ではない。

## 解釈と次の条件

現在の sidecar は exact canonical context SHA と semantic action SHA を gate に使う。新しい CABT state が train manifest の exact hash と一致しない場合、residual は適用されず Wave6 baseへ pass-throughする。従って更新後 24 局 smoke でも、まず次を確認する。

1. `coverage.observed=true` かつ `total_decisions>0`。
2. `exact_known_context_rate`、`known_action_rate`、`residual_applied_rate`、`nonzero_residual_rate`、`top1_change_rate` を確認する。
3. `by_opponent_seat_game` に全 6 opponent × 2 seat × 2 game の cell がある。
4. `coarse_public_bucket_observed=false` を維持し、exact coverage がほぼ 0 の場合は勝率を residual 性能証拠と解釈しない。
5. residual applied が 0 でない場合でも、24 局は coverage/fault/runtime smoke に限定し、promotion や longrun を許可しない。

## 更新後 runner の seed0 観測

seed0 のみ、更新後 runner で 6 opponent × 2 seat × 2 game（24 局）を再実行した。artifact は [fixed-six-24-coverage.json](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/meta-specialist-signed-residual-tiny-20260812/seed-0/fixed-six-24-coverage.json) で、sidecar SHA は `e512024175133257ad2a4280d0b99ca6b8f0857a96c6f821368e7066695550fc`、fault は 0 だった。

| counter | 観測値 |
|---|---:|
| total decisions | 1,346 |
| exact-known context | 12 / 1,346 = 0.8915% |
| eligible action slots | 5,509 |
| known / residual-applied action slots | 24 / 5,509 = 0.4357% |
| nonzero residual slots | 24 |
| top-1 change | 0 / 1,346 = 0% |
| OOD pass-through | 1,334 / 1,346 = 99.1085% |
| STOP decisions / known STOP | 169 / 0 |
| score | 10W–14L = 41.67% |

全 24 cell の `by_opponent_seat_game` ledger と action-type 別 counters が保存されている。exact context coverage が 1% 未満、top-1 change が 0% なので、この 24 局スコアを residual の性能証拠とは扱わない。これは「ほぼ Wave6 pass-through だった」ことを実測で示す coverage 診断であり、residual target の良否を比較する実験ではない。既存の coverage 未接続 JSON（10W–14L ではない以前の実行結果）と局数が同じでも、engine seed setter がないため game-level pairing ではない。

## 更新後 runner の seed1 観測

seed1 も同じ更新後 runner で 24 局を再実行した。artifact は [fixed-six-24-coverage.json](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/meta-specialist-signed-residual-tiny-20260812/seed-1/fixed-six-24-coverage.json) で、fault は 0 だった。

| counter | 観測値 |
|---|---:|
| total decisions | 1,358 |
| exact-known context | 12 / 1,358 = 0.8837% |
| eligible action slots | 5,289 |
| known / residual-applied action slots | 24 / 5,289 = 0.4538% |
| nonzero residual slots | 24 |
| top-1 change | 0 / 1,358 = 0% |
| OOD pass-through | 1,346 / 1,358 = 99.1163% |
| STOP decisions / known STOP | 176 / 0 |
| score | 9W–15L = 37.50% |

seed0/seed1 を合わせると 48 局で 19W–29L（39.58%）だが、engine seed setter がない独立評価であり、Wave6との game-level pairing ではない。両seedで exact context coverage は 1% 未満、action residual application は 0.45% 未満、top-1 change は 0% だった。従って今回の測定は、レビューで懸念された「exact gate が実戦中ほぼ発火しない」を確認した診断であり、signed residual の性能比較ではない。coarse public bucket gate への変更、または exact gate の source/domain 再設計なしに、追加の長時間学習や promotion へ進まない。

## artifact evidence class の固定

coverage runner は fault 0 を性能証拠へ自動昇格させないよう更新した。`_research_evidence_flags()` は常に `performance_evidence=false`、`coverage_evidence=true`、`performance_evidence_reason=coverage_diagnostic_only_not_promotion_evidence` を返す。2 games/cell・CABT engine seed setterなし・coarse bucket未接続という条件では、完走はruntime健全性とcoverage計測の証拠に限られる。既存の再評価JSONは更新前のfieldを持つため、勝率やfieldだけで再解釈せず、本evidenceのcoverage値と実行identityを正本とする。
