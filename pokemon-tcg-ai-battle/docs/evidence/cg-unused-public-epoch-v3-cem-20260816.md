# 公開未使用 snapshot epoch v3 / P1 CEM（2026-08-16）

## 判定

`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。
新しい公開 snapshot source を、既存の性能使用済み source と分離して hash-bound pool／split へ封印し、P1 固定 CEM を fault 0 で完走した。しかし screen 上位2候補は独立 TRAIN re-evaluation でともに負差・seat-collapseとなり、risk-aware positive gate が P1 center を保持した。META_DEV／META_FINAL、deck phase、`cg_bestknown_loop_v1.py`、BestKnown／Champion／production／submission は不変である。

## source intake と holdout 修正

先に作った v2 root（`runs/cg-kaggle-unused-public-epoch-v2-20260816/`）は、Yaminh staged を DEV に置いていた。Yaminh は別の `public-new4` CEM の DEV baseline 診断へ投入済みだったため、未使用 DEV としては不適格である。v2 root とその CEM artifact は削除せず、holdout 汚染候補として保全し、今回の判定証拠には採用しない。

v3 は Yaminh を除外し、性能探索へ未投入だった Jazi rank1 snapshot を DEV に差し替えた。TRAIN は `jazivxt/garchomp` と `prvsiyan/visible-grim v21`、FINAL は Marnie base static v2 とした。

| split | candidate id | policy SHA | canonical deck SHA | source commit |
|---|---|---|---|---|
| META_TRAIN | `kaggle_jazivxt_garchomp_20260815` | `568da4eefc836cb8b316f1075c180842ec7a908065ae328857f5b36e5e69d645` | `39fb18fd9ff204e86299a92ac22092fdd41fb6111a48febb82aabd2039d01ef` | `1197f40380ad59a2a80963f0d2da8ddca2c02a3040a71194ab4cd9c310d5e79a` |
| META_TRAIN | `kaggle_prvsiyan_grimmsnarl_v21_20260815` | `084de53f1c5b4a229b37af1e1def253c7a77f29b2b21622daa96db4058004769` | `606a775392ffe25e058b19c17801d58a4bf30f7cd8c62782388d3de7e7eb5283` | `6bec348a7ed0191f45d2f49a5ff7c4b9cdbd7aa6172f11dc96eaea84e27b22e4` |
| META_DEV | `kaggle_jazivxt_rank1_lucario_20260816` | `b56331905b06215108b89aac2387d8059a94462e74dac18951990ae05674a706` | `ed840b99364baa5b5cc03a3120e9d3c982d7c905e2ed8bea2b9e9d2017fa19b7` | `a2cf97b6b3053681020f4ceedefc311e919df6000efcd87932dc2f2bb920403e` |
| META_FINAL | `kaggle_llccqq624_marnie_base_static_v2_20260816` | `ba9af9aacbb68fcf7e3bfde3f88de50e3a259cf233e8d0be0e571e6dddade380` | `606a775392ffe25e058b19c17801d58a4bf30f7cd8c62782388d3de7e7eb5283` | `513d6858f78c26bc3c6aec2920f638eaa44b9790459d40bc8bbfe0f346616f15` |

TRAIN source の bounded smoke は両seat 4局、`DONE=4/4`、fault 0、2W-0D-2L だった。promotion subset／merge／split loader は source SHA と pool SHA を検証して PASS した。authority は全て false、`local_eval_only`／`research_only` である。

sealed artifact:

- pool root: `runs/cg-kaggle-unused-public-epoch-v3-20260816/`
- pool SHA: `5b13783671d77c66397287a8c1ff57a50177fce07fab17d7064816bdb5b9b1a6`
- fresh meta SHA: `20979a75471a2372f2554d6b248c684b12d070679737b5f48c735233c8c63ebe`
- meta manifest SHA: `9cbe500826e54606bdf260d932d22311cff9cc95fc7d85d8c6168e09a11bdd1a`
- split SHA: `25b4a48138925bd6aba909240f249ada1c97b8d03b20fc6a3cb6a51a7ba1d21c`

## P1 fixed CEM

P1 policy SHA は `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA は `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` で固定した。CEM は seed `2026084634`、population／elite `8／2`、1 generation、`META_TRAIN_ALL`、screen 2 games/opponent×seat、independent re-evaluation 2 blocks×2 games/opponent×seat、positive／risk-aware gate とした。

- screen: 72局、全 `DONE`、fault 0
- screen valid candidates: 2件
  - `cg-p1-cem-g00-c02-a3bd606e8e1c`: `3/8` 対 control `0/8`（粗い差 `+37.5pt`）、seat rates `0.50/0.25`
  - `cg-p1-cem-g00-c03-58f1efd7470a`: `2/8` 対 control `0/8`（粗い差 `+25.0pt`）、seat rates `0.25/0.25`
- independent re-evaluation: 48局、全 `DONE`、fault 0
  - c02: `1/16` 対 control `3/16`、差 `−12.5pt`、candidate seat rates `0.125/0.0`、seat-collapse
  - c03: `1/16` 対 control `3/16`、差 `−12.5pt`、candidate seat rates `0.125/0.0`、seat-collapse
- selection: `risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`
- elite: `incumbent-center`×2、P1 center unchanged
- META_DEV／META_FINAL: 未使用

CEM artifact SHA:

- campaign manifest: `a9929d8b6faff7ad8a47ea9cc02eebf6bf29246c73500859bea71e784558789e`
- generation manifest: `458bd07d1b2774a0d5164645b2efda3ebe3e459c29a700972ad181c6f5c244d6`
- generation results: `cc59bba9578b7adf71febbe8c6c00a77d2a156bd660800ba617896eddc54d588`
- evaluation summary: `2d7199d0795ec191c53d6bf62931b985da24ba3ef8e89ad1e20a7e43a0dfa484`

## 再開条件

同じ v3 source pool、同じ CEM seed、同じ c02／c03 candidate の blind retry は行わない。次は、(1) holdout exposure ledger を source ID／policy SHA 単位で自動検査する、(2) 相関の低い新規 permission済み lineage または self-owned policy family を追加する、(3) TRAIN-only screen の段階で seat-safe candidate が複数得られた場合だけ independent TRAIN→未使用 DEV→未使用 FINALへ進む、の順とする。全 gate 通過前に BestKnown、Champion、production、submission、commit、push は変更しない。

## 検証

- v1b unused TRAIN smoke: 4/4 `DONE`、fault 0
- merge／split `load_weekend_split(..., verify_sources=True)`: PASS
- CEM screen／independent: 120/120 `DONE`、fault 0
- `python scripts/docs/validate_docs.py`: PASS（`Validated 13 canonical documents.`）
- focused pytest: PASS（38 passed in 2.82s）
- split source verification: PASS（split SHA `25b4a48138925bd6aba909240f249ada1c97b8d03b20fc6a3cb6a51a7ba1d21c`）
- `git diff --check`: PASS
