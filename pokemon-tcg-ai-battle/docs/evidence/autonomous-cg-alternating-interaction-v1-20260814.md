# Autonomous cg policy×deck alternating interaction — 2026-08-14

## 結論

cg package の policy-fixed / deck-fixed 契約を実 CABT で初めて通した。新規 deck interaction を2件、P0 policy 固定の `POLICY_FIXED_SHORT` 96局で評価したが、Solrock 増量は負差、Colress support は差なしだった。したがって両方とも `NOT_PROMOTABLE` で停止し、契約どおり `DECK_FIXED_LONG` policy phase は起動していない。fault 0 であり、停止理由は性能ゲートであって実行障害ではない。

## 実行条件

- broad common24: 24 opponent IDs × 両 seat × 2 repetition = 96 games/arm
- `POLICY_FIXED_SHORT`: candidate/control は同一 P0 policy SHA、deck SHA のみ差分
- workers=12、recycle=16、authority は training/promotion/submission/longrun 全 false
- candidate/control は同一 opponent×seat×repetition×seed strata
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`

## 結果

| interaction | candidate | control | delta | decision |
|---|---:|---:|---:|---|
| `675 Lunatone → 676 Solrock` | 14/96 (14.5833%) | 22/96 (22.9167%) | −8.3333pt | `NOT_PROMOTABLE` |
| `1192 Carmine → 1194 Colress's Tenacity` | 14/96 (14.5833%) | 14/96 (14.5833%) | 0.0000pt | `NOT_PROMOTABLE` |

両 interaction とも candidate/control 全192局 `DONE`、fault 0、draw 0、両 seat support あり。Solrock の stage summary SHA は `07c68430a001e53a95d3093008359e98e5008592e36accdc3285fb95c141ad82`、Colress は `cfe90ca430fbdcb2347ba3c4acaa23d320e683a1ab26c7a7764c090e0d5952be`。positive gate 未達のため 384/768/longrun は実行していない。

## package identity

Solrock candidate deck SHA は `adc821b9a735ecebfb785515c58f34c1385104da7a052855e15f5e8e00bbf4a6`、Colress candidate deck SHA は `af201a2106b501533beb1b17d5b944b6cab83d0d6cd8288bfc5d4105774516f6`。両方とも P0 policy source SHA `617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef` と cg runtime closure を固定した deck package、および cg lethal policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` を別 package として materialize した。

package builder は source policy と source deck を同時に hash-bind できるよう research-only optional `source_agent` 引数を追加した。builder SHA `e14dfd4da0d3181226d9942bb1812427c0fcebe08677d31c89d5a001842569bd`、focused test SHA `d1cca1e486630e4ef4537b90798f1a1395ca6c35592e4fd662a1b0026c396404`。既存 production `main.py`/`deck.csv` は変更していない。

## alternating runtime

`src/mage_ptcg/meta_specialist/cg_alternating_runtime_v1.py` は package SHA、deck SHA、policy SHA を phase ごとに再検証し、positive/fault0/seat gate を満たす場合だけ次 stage を許可する。今回の実行 root は次の通り。

- Solrock: `runs/final-sprint-autonomous/cg-alternating-solrock-v1-20260814/execute/`
- Colress: `runs/final-sprint-autonomous/cg-alternating-colress-v1-20260814/execute/`

両 root の `iteration.json` strict reload と `policy-fixed-short/manifest-complete.json` は PASS。`DECK_FIXED_LONG` が未起動なのは、実測 `candidate_delta <= 0` による正しい fail-closed である。

## status

cg local package contract は引き続き PASS、公式 remote Submit verifier は `UNKNOWN_NOT_BUNDLED`。従って `submission_ready=false`、Champion変更・training・promotion・Kaggle提出は行っていない。現行 ResearchSubmissionCandidateBestKnown は cg lethal + root deck のままだが、今回の新規 deck interaction はその improvement evidence にならなかった。次は同じ Solrock/Colress の retryではなく、hypothesis が明示された新しい interaction または remote contract confirmation が必要である。
