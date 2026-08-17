# Outcome-only policy-fixed 384 confirmation v1（2026-08-13）

## 結論

96 局 screen で fault 0、seat collapse なし、`ATTACK=+120` candidate が control より +2 wins / +2.083pt だったため、同一 sealed bridge を 4 block に分けた 384 局 confirmation plan を materialize した。これは評価器を起動する前の計画 artifact であり、学習・昇格・提出の権限は持たない。

## 親 bridge とブロック

- parent bridge: `runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-96-retry-v1/bridge.json`
- parent semantic SHA: `6d13acde40f003a409e0a2019beb397a8f1dcd2a3a1a899f2f4c596fd75facd9`
- parent は A schedule に対する Rule v0 control / `ATTACK=+120` candidate の 96 slots、META_TRAIN のみ、heldout exposure 0。
- confirmation は parent の candidate/control identity、deck/pool/config/evaluator/schedule SHA、candidate config を再検証し、不変の 96 slot pattern を 4 回複製した。
- block 0 は seed `14910096..14910191`、block 1 は `14910192..14910287`、block 2 は `14910288..14910383`、block 3 は `14910384..14910479`。parent の `14910000..14910095` と重複しない。
- 各 block は 96 games、seat 0/1 各 48、candidate/control は opponent・seat・repetition・seed を完全共有。heldout は 0。

## Artifact

新規 root: `runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-confirmation-384-v1-20260813/`

| artifact | SHA-256 |
|---|---|
| `confirmation.json` | `a7500a75e6bb2848f43818d1766e9d6782bcff7dbd9521ecb938609d0a0eefb2` |
| `confirmation.games.json` | `9e2cbe3a7eff8e59ab9f55e7854ea8b57caffa2a537d1de94503a637591e1d74` |
| manifest `confirmation_sha256` | `9ccb1180b21142a74a731c2d55ce79072a82843f5a45ef0ec85554f2d10d30de` |

`ready_for_evaluation=true` は manifest/source/identity の閉包だけを意味する。`execution_allowed=false`、authority（training/promotion/submission/external-execution/longrun）は全て false であり、confirmation materializer 自体は 384 局を実行していない。

## 再現と検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/build_outcome_only_policy_fixed_confirmation_v1.py \
  --repo-root . \
  --parent-bridge runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-96-retry-v1/bridge.json \
  --output runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-confirmation-384-v1-20260813/confirmation.json
```

Focused suite は `tests/meta_specialist/test_outcome_only_policy_fixed_confirmation_v1.py: 5 passed`。strict reload は parent bridge file/semantic SHA、inherited identity、seed disjointness、4×96 slot/seat strata、authority を再導出して成功した。`py_compile`、docs validator、`git diff --check` を完了してから runner 起動判断へ進む。

## 次の gate

この plan を使う実評価では、fresh output root、`workers=1` 優先、各 block の fault 0、seat/opponent support、candidate/control paired WDL を保存する。384 結果が parent control より再現的に約 +3pt 以上なら次の 768 gate を検討し、未達または fault があれば candidate arm を停止する。実行前に manifest と sidecar の SHA を strict reload する。

## 実測 384 confirmation（2026-08-13）

fresh root `runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-confirmation-384-20260813/` で、同一 confirmation manifest/sidecar を strict reload 後に `workers=1`, `worker_recycle_games=16` で 768 game cells（control 384 + candidate 384）を完了した。

- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- completion: 768/768, `DONE=768`, faults=0, draw=1
- candidate (`ATTACK=+120`): 49W/0D/335L = **12.7604%** W+0.5D rate
- control (native Rule v0): 30W/1D/353L = **7.9427%** W+0.5D rate
- paired: loss→win 42, win→loss 23, net +19 candidate wins
- seats: candidate seat0 27W/165L, seat1 22W/170L; control seat0 13W/1D/178L, seat1 17W/175L
- blocks: each 96 games, seeds `14910096..14910479`, all four blocks fault-free; paired candidate net by block was `+4, -2, +5, +12` (aggregate `+19`)

Artifact SHA:

| artifact | SHA-256 |
|---|---|
| `evaluation/summary.json` | `4401639360388c4edc45966aadc28476bcac920ee0d9d4c7afbcc87d8bddbd` |
| `evaluation/ledger.jsonl` | `eda449d3fa071c4f0303c1421ec9fd369d51bb48e110e258a234ce839f5392df` |
| `run-result.json` | `686a74cc196b9870e420141a82d7d7000f312521aaea1c470325fb3be1642c1e` |

この結果は parent control より +4.8177pt であり、同型 96→384 gate の longrun-ready 条件を満たす候補だが、現行で比較すべき native BestKnown の上位に到達したことを意味しない。768/longrun は自動起動せず、次は別候補との同一 arena 比較または明示承認を待つ。
