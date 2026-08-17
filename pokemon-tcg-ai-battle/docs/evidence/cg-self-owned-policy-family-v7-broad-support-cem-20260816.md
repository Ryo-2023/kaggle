# self-owned policy family v7 broad-support と CEM（2026-08-16）

## 結論

公式カード CSV と新規 role specification だけから、v1〜v6 と別の `broad-support-v7` source epoch を生成した。8 件の self-owned deck＋P1-derived policy overlay は静的合法性と両 seat smoke を通過し、v7 固定 scratch deck に対する policy-only CEM を 1 世代実行した。screen 216 局、独立再評価 144 局の合計 360 局は全て `DONE`・fault 0 だった。

screen 最大候補 `cg-p1-cem-g00-c04-51fa620f2e8b` は P1 control 比 `+12.5pt`、独立再評価は `+10.4167pt` と `+16.6667pt`（平均 `+13.5417pt`、最悪 `+10.4167pt`）だった。ただし `seat_safe=false`、`opponent_seat_safe=false` であり、研究 parent への昇格条件を満たさない。P1 center、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py` 接続は変更していない。META_DEV／META_FINAL は未読出しのまま保全した。

## source generation

- plan: [`self_owned_cg_policy_family_v7_broad_support.json`](../../configs/meta_specialist/self_owned_cg_policy_family_v7_broad_support.json)（SHA-256 `9426e937cc089afc5e575c7c7d9ed8df390f8129e763dcc9f619d3b29171b298`）
- role spec: [`self_owned_cg_deck_spec_v5_broad_support.json`](../../configs/meta_specialist/self_owned_cg_deck_spec_v5_broad_support.json)（SHA-256 `53bc9704195f405b00aad7fffcf9d49c4aa450a694947276264f2953265579bd`）
- source epoch: `self_owned_official_card_data_broad_support_v7_20260816`
- input boundary: `data/raw/EN_Card_Data.csv` と上記 role spec。既存 artifact は canonical hash collision 監査だけに使用した。
- staged root: [`runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816/`](../../runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816/)
- factorial manifest SHA-256: `46d37e9e990eeab22a56e4247ad98beaa575e211b6f5a3bd8718f61c44e2055d`
- 8 件すべて deck／policy identity が相互に一意で、`parent_deck=null`、`public_parent_read=false`、authority 全 false。source の意味は「公式カードデータから生成した self-owned deck に、immutable P1 の parameter overlay を束ねた local-eval-only source」であり、P1 から独立した policy lineage ではない。

## smoke と seal

各候補を同じ `aristophanivan_multiply`、両 seat、candidate/control matched、各 arm 1 局／opponent／seat で実行した。最初に stdin から multiprocessing を起動した試行は `<stdin>` spawn error で中断したため性能結果には算入せず、実ファイル CLI から再実行した結果だけを採用した。

- smoke summary: [`smoke-v2/smoke_summary.json`](../../runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816/smoke-v2/smoke_summary.json)
- smoke summary SHA-256: `98c0245d0006fbe037a69085c5312b79b6f5a21fa00cc46d0f1f7a22aaf8351f`
- 32/32 `DONE`、fault 0
- promoted root: [`runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816-promoted/`](../../runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816-promoted/)
- pool SHA-256: `c70cb2906b7e9e7f3084d11a1ced052b946fa5c4c9baccb5e47eb92fc19810e9`
- fresh meta SHA-256: `17326c7267b7163e09544ce46c941acddeaea05649ddd7b5d961bfbdd336ffd0`
- meta manifest SHA-256: `62a6ae44fda0c9c4aad14ed03f54eb745c708eccdfe8234a043df83c2107d28a`
- 通常 split は [`cg_self_owned_weekend_split.json`](../../runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816-promoted/cg_self_owned_weekend_split.json)（SHA-256 `40630d7525d313b7e70a1172ad69e880833080c3432e0ed2f4bea772c5b10e9b`）で、`META_TRAIN=6 / DEV=1 / FINAL=1`。

## policy-only CEM

deck 差を混ぜないため、balanced-v7-00 の scratch deck（deck file SHA `c771040e8d77921402de738f1c20dcebab088e4468202795fb0d84090cb902b0`、canonical SHA `6b77af608efd56647c4bf12248a3511dc2734df1f8d5cceee6e26027f98eb01a`）へ immutable P1 main を bind した source/control を別 artifact として作った。CEM core が source と control の両方で split の deck SHA を要求するため、通常 splitを変更せず、deck-bound split [`cg_self_owned_cem_split_v1.json`](../../runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816-promoted/cg_self_owned_cem_split_v1.json) を追加した。

- P1 source main SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- control policy SHA（default parameter point）: `2e0a552fabbe4a544905b91f1ee6effffd58d039881fd8932c7e9ac0701acc75`
- deck-bound CEM split SHA-256: `bc38707e45c34234e33da3d1060ec3ac9c42951796ff0ecf7b2b5582d12dc847`
- campaign root: [`runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816-cem/`](../../runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816-cem/)
- campaign manifest SHA-256: `6662609a524572cc5cb3322ba505ab8434daccb89911e0d78480ee2ccc0d125c`
- config: generation 1、population 8、elite 2、`META_TRAIN_ALL`、独立再評価 2 block、各 2 局／opponent／seat、positive-delta gate、risk-aware update、campaign seed `20261301`
- screen summary: [`evaluation/summary.json`](../../runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816-cem/generation-0000/evaluation/summary.json)（216/216 `DONE`、fault 0、SHA-256 `7effa9631a00da3b0437c1125abb089bb0e63db16688abf10580c1a7d0a15481`）
- independent summary: [`reevaluation/summary.json`](../../runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816-cem/generation-0000/reevaluation/summary.json)（144/144 `DONE`、fault 0、SHA-256 `a484fcee52036e1819357103ceb2f593bc1258ea8bd760a3a6ed78fc9c1bcc48`）
- results SHA-256: `7e55f6b4f13a6a12dac6787fa31ee52b95d54aa8db4de0aa6bd011bbc2d550d`

### top candidate

- candidate: `cg-p1-cem-g00-c04-51fa620f2e8b`
- parameter config SHA: `51fa620f2e8be505621607b44d96cfa2ec710bccbb17a37c4bbb4948f6f04b8e`
- candidate policy SHA: `9e76101d650c27e290e2f5f55f006e4fb0d221f55d2cb940ca8d3c39bc8207ad`
- screen: candidate `17W-0D-7L` 対 control `14W-0D-10L`、差 `+12.5pt`
- independent repeats: `+10.4167pt`、`+16.6667pt`、mean `+13.5417pt`、min `+10.4167pt`
- independent gate: fault 0 だが `seat_safe=false`、`opponent_seat_safe=false`
- elite selection: `risk_aware_independent_train96_x2_valid_candidates_below_elite_count_preserve_center`
- new center は P1 default parameter と同一（P1 center 保持）

## 検証

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
TMPDIR=/tmp/codex-test-v7 PYTHONPATH=.:src .venv/bin/python -m pytest -q --capture=no \
tests/test_generate_self_owned_cg_policy_meta_v1.py \
tests/meta_specialist/test_self_owned_cg_deck_v1.py \
tests/meta_specialist/test_self_owned_cg_deck_screen_v1.py \
tests/test_build_self_owned_cg_policy_factorial_split_v1.py
13 passed in 0.17s
```

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。v7 source は CEM 性能使用済みであり、同じ v7 pool の blind retry は行わない。次は、seat／opponent-safe を改善する相関の低い policy lineage または新しい deck recipe を別 epoch として生成し、今回の DEV／FINAL を流用せずに再び freshness gate から開始する。
