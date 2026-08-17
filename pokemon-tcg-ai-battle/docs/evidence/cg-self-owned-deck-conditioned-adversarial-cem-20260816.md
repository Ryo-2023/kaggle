---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-16
---

# self-owned deck-conditioned adversarial source と P1 CEM

## 結論

公式カード CSV と repo 内の self-owned deck recipe から新しい deck を生成し、P1 parameterized policy をその deck へ再結合する source generation 方法を実装した。6 source の package smoke は 192/192 `DONE`・fault 0 で、fresh batch・promotion・TRAIN/DEV/FINAL split sealing まで完了した。これは source generation の成功であり、提出候補や BestKnown 更新ではない。

P1 固定 CEM は2世代・population 8・elite 2で全 row fault 0を完走した。gen0 screen の最良候補は control 比 `+18.75pt` だったが、独立 re-evaluation 2 block は `−18.75pt / −6.25pt` に反転し、risk-aware delta は `−12.5pt` となった。gen1 は screen 全候補が負差分で、DEV の incumbent center も candidate `6/16` 対 control `9/16`（`−18.75pt`）だった。positive、seat-safe、opponent×seat-safe gateを満たす候補はなく、selection は両世代とも `incumbent-center` である。BestKnown、P1、root deck、Champion、production、submission、`cg_bestknown_loop_v1.py` の昇格状態は不変である。

## 生成方法と provenance

- generator: `scripts/generate_self_owned_cg_deck_conditioned_adversarial_meta_v1.py`
- plan: `configs/meta_specialist/self_owned_cg_deck_conditioned_adversarial_family_v1.json`
- plan SHA: `591cfee66de6f1964e0d54a6c8b390d47202980ec5f7d69e61318db07e53007d`
- generator schema: `self-owned-cg-deck-conditioned-adversarial-source-v1`
- source kind: `self_owned_official_card_data_deck_with_p1_adversarial_policy`
- source epoch: `self_owned_cg_deck_conditioned_adversarial_family_v1_20260816`
- seed namespace: `self-owned-cg-deck-conditioned-adversarial-fresh-v1-20260816`
- immutable P1 source policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- immutable P1 root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`

各 source の deck は `data/raw/EN_Card_Data.csv` と self-owned role spec から生成し、既存 public canonical deck hash との衝突を fail-closed で除外した。policy は P1 の parameter surface を renderer で再生成し、deck hash と package identity へ bind した。source の authority は全て false、`research_only=true` であり、hidden opponent zone、training label、submission authority は使用していない。

生成 artifact は `runs/cg-self-owned-deck-conditioned-adversarial-v1-20260816/` に保存した。

- staged source manifest SHA: `8deb3f46b3deabc730c7aed3b27ae483da947798a6ba16bdf4666a311f116f08`
- promoted pool SHA: `f8c2e4fe3735730665bd8234ef48c628809373e22cce895c74043add7b7233aa`
- fresh meta SHA: `786d1d8b186e060b3c664ab2e3375c0a58b04c63ff20dbc21b86eaa57c67f9d9`
- meta manifest SHA: `f3de7856cb0b4bbe72552ba1f3795c5c622bf4171a99f7b5bb505c29f2d6d6f5`
- weekend split SHA: `2e16dffed86c89982e380d9d0d76a25653a716ebae15af8c265372553a1c983c`
- split: `META_TRAIN=4 / META_DEV=1 / META_FINAL=1`
- split contract: `final_results_read_during_search=false`, `training_exposure=0`

## source runtime smoke

P1を subject とした bounded smoke は、4 local opponent、両 seat、各 source 32局の計192局で実行した。全件 `DONE`、fault 0、draw 0、source 側の勝敗は `41W-0D-151L` だった。勝率は source の合法性・runtime gateを示すもので、P1のBestKnown性能ではない。

- smoke root: `runs/cg-self-owned-deck-conditioned-adversarial-v1-20260816/smoke-bounded-w1-4x/`
- smoke summary SHA: `5c30cd9198aa5dbc4fbd3f9042ea815f6aa7828a3dd1f629830c34cf7492df3c`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- authority: `longrun_allowed=false`, `promotion_allowed=false`, `submission_allowed=false`, `training_allowed=false`

## P1 fixed CEM

`runs/cg-p1-cem-deck-conditioned-adversarial-v1-20260816/` を campaign seed `2026089801` で実行した。META_TRAIN 4 sourceだけを使い、population/elite `8/2`、2世代、screen、独立 re-evaluation 2回、positive-delta gate、risk-aware update を有効にした。CEM manifest は `status=COMPLETE`、`champion_changed=false`、全評価 row は fault 0である。

| 世代 | screen の要点 | 独立 re-evaluation | 選択 |
|---|---|---|---|
| gen0 | c05 が control 比 `+18.75pt`（`14/16` 対 `11/16`） | c05 は `−18.75pt / −6.25pt`、mean `−12.5pt`、seat-safe false | `incumbent-center` |
| gen1 | 8候補すべて control 比 `−12.5pt`〜`−43.75pt` | gate を通る候補なし | `incumbent-center` |

gen1 の未使用 META_DEV は incumbent center のみを確認し、candidate `6/16`、control `9/16`、差 `−18.75pt`、fault 0だった。META_FINAL は CEM中に読んでいない。candidateが strict gate を通過しなかったため、FINAL評価、deck phase、policy→deck→policy loop接続は実施していない。

- CEM manifest SHA: `7ffc5ee4d417db548ba24c0309c8d516f4f8399f4097e62464f48302ca3a74dd`
- gen0 results SHA: `559167af1799eb437591621bc96bd571f394e453073c9e5395d24b717dac3e1a`
- gen1 results SHA: `14b7ed6753b7de9a39464371e1d228224ce497d1caefd53b964ce7837ac49b3a`
- CEM evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`

screenの小標本改善は独立 block で反転しており、P2/P3やBestKnown更新の根拠にしない。今回のpoolと候補は性能使用済みとして、同じ source、seed、候補の blind retry は行わない。

## 判定と次の方針

判定は `SOURCE_GENERATION_PASS / RUNTIME_SMOKE_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED` である。今回の結果は「self-owned deck＋policyを作れる生成経路が増えた」ことを示すが、最終目標の提出可能な self-owned deck＋policyや72%級への到達を示すものではない。

次の研究は、同じ deck recipe／P1 surface／seed の再試行ではなく、相関の低い source lineage または未使用 policy lineageを新しい exposure ledgerで予約してから実施する。strict gateは `legality → static safety → bounded fault0 → TRAIN-only CEM → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` の順を維持する。

commit、push、Champion変更、production変更、Kaggle submissionは行っていない。
