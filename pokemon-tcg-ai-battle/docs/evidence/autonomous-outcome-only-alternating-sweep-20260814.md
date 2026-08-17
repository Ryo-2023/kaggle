---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-14
---

# Outcome-only alternating sweep — 95cc deckを中心とした deck/policy 交互評価

## 結論

実接続した alternating runtime を、同一 broad24 pool・同一 strata・workers=12/recycle16で複数の deck/policy候補へ適用した。全て research-only、全局 fault0で、candidate/controlの比較は実行できた。しかし、stableに昇格できる改善はまだ得られていない。

最も有望なのは、95cc deck（`1213 -> 1185`）と native Tomato policy の組み合わせで、別seedの 1536局確認で **1089-1-446 (70.93099%) vs control 1059-1-476 (68.97786%)、+1.9531pt** だった。これは長めの固定条件で持続した候補だが、Tomato native policyは `local_eval_only` の研究資産であり、submission-compatible pairではない。また native BestKnown超越ではなく、promotion/submission/longrun権限もないため candidate-only とする。

policy variant の局所positiveは再現しなかった。Relicanth-firstは96局で+6.25ptだったが384局で−2.3438pt、threshold-lowerは96局で+3.125ptだったが384局で−2.6042ptへ反転した。したがって小局point estimateをpolicy更新や長時間学習へ流用しない。

## 共通条件

- policy/deckをcandidateとcontrolに分け、`outcome_only_alternating_runtime_v1` の一つの evaluator blockへ投入。
- broad24 opponent IDs、両seat、repetition、seed strataをcandidate/controlで共有。
- stageは96→384→768→1536、fault-inclusive denominator、drawを0.5勝相当として集計。
- ResourceGovernor正常時の既定は `workers=12`, `worker_recycle_games=16`。
- authorityは execute/training/promotion/submission/longrun 全false、native action/teacher/private情報は使用しない。
- 各runは fresh root、既存production/既存artifactを上書きしない。

## 実測一覧

`Δ` は candidate score − control score。W-D-L-F は各armの勝敗・fault数。

| run | candidate | control | Δ | 判定 |
|---|---:|---:|---:|---|
| Tomato policy × a73 deck / 96 | 61-0-35-0 | 65-0-31-0 | −4.1667pt | 停止 |
| Tomato policy × 95cc deck / 96 | 70-0-26-0 | 69-0-27-0 | +1.0417pt | 微小、確認へ |
| Tomato policy × 432ff deck / 96 | 65-0-31-0 | 65-0-31-0 | 0pt | 停止 |
| Tomato policy × 95cc deck / 384 | 282-0-102-0 | 269-0-115-0 | +3.3854pt | 確認値、次へ |
| Tomato policy × 95cc deck / 768 | 544-1-223-0 | 524-0-244-0 | +2.6693pt | `POSITIVE_CONTINUE` |
| Tomato policy × 95cc deck / 1536 | 1089-1-446-0 | 1059-1-476-0 | +1.9531pt | 長期candidate-only |
| Relicanth-first policy × 95cc / 96 | 66-0-30-0 | 60-0-36-0 | +6.25pt | 384へ |
| Relicanth-first policy × 95cc / 384 | 265-0-119-0 | 274-0-110-0 | −2.3438pt | 反転、停止 |
| Duraludon-first policy × 95cc / 96 | 55-0-41-0 | 68-0-28-0 | −13.5417pt | 停止 |
| threshold-lower policy × 95cc / 96 | 65-0-31-0 | 62-0-34-0 | +3.125pt | 384へ |
| threshold-lower policy × 95cc / 384 | 264-0-120-0 | 274-0-110-0 | −2.6042pt | 反転、停止 |
| threshold-higher policy × 95cc / 96 | 67-0-29-0 | 68-0-28-0 | −1.0417pt | 停止 |
| Rule v0 × 95cc vs Rule v0 × root / 96 | 12-0-84-0 | 15-0-81-0 | −3.125pt | submission route停止 |

## 解釈

95cc deckは native Tomato controlへ一定の正の差を示したが、同一 deck上の policy variantは384で負へ反転した。これは deck improvementの信号と policy perturbationの信号を混ぜると誤判定することを示す。現時点で採用可能な分類は、`Tomato native policy × 95cc deck = research candidate-only / local evaluation`、`Rule v0 × 95cc deck = root deck controlを下回るため submission candidateではない` である。

この結果から、native policyの出力をteacher labelやbehavior labelとして学習へ流さない。95cc deckを固定したままの次の policy searchは、同じ threshold/setup面を再実行せず、別の未評価 surfaceを1件ずつ選ぶ必要がある。longrunは、candidate policy/deckが permission・submission closureを満たし、BestKnown比較とrollback gateを通過するまで起動しない。

## 主成果物SHA

- alternating wrapper（一般化後）: `scripts/run_outcome_only_alternating_tomato_a73_v1.py` — `879292381b92c733b28cbaf6a8f5dfb6fd3a7be86a7b12676434a7144aa85a21`
- runtime: `src/mage_ptcg/meta_specialist/outcome_only_alternating_runtime_v1.py` — `9a06ba77a9e2b16ecced051b32463afd9c233139d41e4e49c887f712ffb99bda`
- 95cc/1536 manifest — `7559a535bad446ffec795fd7068c57c4a7633da55dd2de660a8a2781f37cc80a`
- 95cc/1536 summary — `574c67771b6363c1944d96ce7156577da406380853229c0edf388f27bcacea04`
- 95cc/768 manifest — `5305c2a368cca3be657ff3ce1640e199276f77ff7d5063b02aaea477af1b1228`
- 95cc/768 summary — `f54f920087531b3198100a62a42615b1f01bf63b0ff9f62eb79c7081a9211380`
- 95cc/384 manifest — `4bf12200903cada1a392b73d14cb2fb2f13bf89ec6def9e17d9dc8e9f3302f3a`
- 95cc/384 summary — `81caf098668ca0bedd7f46e997b53b81ef3053217e0ea0fd02a561125ac097bb`
- Relicanth/384 manifest — `28a6d017402f6edde7a7df7e91e0810cb65f87b41dd9dd13084e49e0c9b1cf81`
- Relicanth/384 summary — `5fb5a7b4136fd8ae6a86206b82e493bd5465d08f02986d9b7b26f11fc2df70e4`
- threshold-lower/384 manifest — `7f0a3f189611e6438f16cdc40172629401cfee29a1bbf47f9d352a0e8b5084af`
- threshold-lower/384 summary — `2622528a394bd09077768ec4f1fae586b9d68743e3f77c5b0d1ecf3d698e6ee2`
- Rule v0/95cc manifest — `26f8ded850526811a1f4fddd55898e15dd234aa2d539cb6891c7a2122d182f91`
- Rule v0/95cc summary — `0da2b5796243816b0da425dbbf2804c0c566e416788942a5f9ba0813c98042bc`

