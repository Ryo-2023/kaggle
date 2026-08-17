# self-owned deck + P1 policy CEM v1（2026-08-16）

## 結論

公式カードCSVとversioned role specだけから生成した新規self-owned deck batchへ、固定P1 policy surfaceを結び付けてCEM pilotを実行した。source生成、package検証、両seat smoke、CEM実行はすべてfault 0だったが、独立再評価でseat／opponent×seat安全条件を満たす候補は0件だった。positive-delta gateはincumbent centerを保持し、P3昇格、DEV／FINAL読出し、`cg_bestknown_loop_v1.py`接続、BestKnown／Champion／production／submission変更は行っていない。

## source生成とfreshness

- 生成器: `scripts/generate_self_owned_cg_deck_v1.py`
- 入力: `data/raw/EN_Card_Data.csv`（SHA `a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373`）、`configs/meta_specialist/self_owned_cg_deck_spec_v2.json`（SHA `e7797e96e4480592487268caa26edae451846267647728d0c322c6d2088af3c5`）
- seed／ordinal: `20260840..20260845`／`0..5`
- 6件すべて exact 60、公開canonical hash衝突0、互いに異なるcanonical deck SHA。各candidate packageは`parent_deck=null`、`public_parent_read=false`、authority全false。
- batchは `runs/cg-self-owned-cg-meta-batch-v3-20260816-promoted/`。pool SHA `99a28828d0adaa215f048ce35ecc5b59445be670efe1a9973a4b6fd0d769f5ec`、fresh_meta SHA `de31609f0b9d9f51c0a7a3c39f35d9e6c9a88e8ae2beb6d0c12b5d31becfdc28`、meta manifest SHA `eb9ad88443c956a9c3df39a7c9b80048bc0ecb2a44d236511d8627fcb92e9305`。
- 6 sourceを各4局（両seat、candidate/controlを含む）smokeし、24/24 `DONE`、fault 0。smokeの詳細は`runs/cg-self-owned-cg-meta-batch-v3-20260816-smoke-00`〜`05`と`runs/cg-self-owned-cg-meta-batch-v3-20260816-smoke-summary.json`。
- 性能splitは`runs/cg-self-owned-cg-meta-batch-v3-20260816-promoted/cg_self_owned_weekend_split.json`（SHA `3eab6dc1b3ef61e84e28f680a44fc6abfb49f58ebc98201d77f0aaf7dd43372d`）。META_TRAIN 4件、META_DEV 1件、META_FINAL 1件で、CEMはTRAINだけを使用した。DEV／FINALは未使用のまま保全している。

## package境界

P1のimmutable source SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`。scratch deck controlのdeck file SHAは `5610c2e0b9210c22885edcf59160212270fdfc53c90b6f61df588dcdbf8ffde2`、canonical SHAは `b6dc5a6a3f3e00545df881fa3c6981e1cf8ee418c39794504bed90d052ddfcbf`、default policy overlay SHAは `6f84df2ebcdd63c1d88b98fa348ed6316bb739c1deda5ea65466e83c5940889f`。materializerはP1の15 parameter surfaceだけをoverlayし、同じdeckの`ROOT_DECK`へ再bindする。root `deck.csv`、Champion、submission packageは変更していない。

実装は[`self_owned_cg_parameterized_package_v1.py`](../../src/mage_ptcg/meta_specialist/self_owned_cg_parameterized_package_v1.py)、runner bridgeは[`run_self_owned_cg_policy_cem_v1.py`](../../scripts/run_self_owned_cg_policy_cem_v1.py)。materializer contract testは3件PASS。

## CEM結果

実行rootは `runs/cg-self-owned-cg-policy-cem-v1-20260816-pilot/`。設定はcampaign seed `2026084601`、generation 1、population／elite `8／2`、META_TRAIN_ALL、独立re-evaluation 2 block、各2局／opponent／seat、positive-delta gate、risk-aware updateである。

- screen: 144/144 `DONE`、fault 0
- independent re-evaluation: 96/96 `DONE`、fault 0
- screen deltaの最大は0pt（候補c03/c05）。候補c05は独立2 blockで`+18.75pt`／`+25.00pt`（平均`+21.875pt`、最悪`+18.75pt`）だったが、opponent／seat gapとseat-safe条件を満たさなかった。
- 候補c03は独立`0pt`／`+12.5pt`で最悪delta `0pt`、同じくseat／opponent安全条件外。その他候補は独立前から負またはscreenでcontrol同率以下。
- 選定ラベルは `risk_aware_independent_train96_x2_valid_candidates_below_elite_count_preserve_center`。new centerはP1 default configと同一。
- campaign manifest SHA `3c556759b8cc360700ee23b4288945ddef3ff1077fe38ef2aefe0594f71fac25`、generation results SHA `2b05f2a7018ab20fe62eaf6041fde11cfc4d1d2c3183e4a6cd02a65886593795`。

## 判定

`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。今回のsource epochはCEM性能使用済みであり、同一poolのblind retryはしない。次は、別の公式データ由来deck recipeまたは新しいpermission済みpolicy lineageをfresh epochとして作り、同じ順序（fault 0 → independent positive → seat／opponent×seat safe → 未使用DEV → 未使用FINAL）を満たしたcandidateだけをBestKnown loopへ渡す。

`ono-`は公開source作者名ではなく、local Git identity `bfe-lab-ono`、branch `agents/ono-cg-lethal-v1`、commit `1965b42b028f10960d08ccb4980be5b76946f98b`に由来するローカル識別子である。現行BestKnownのroot deckはcommon/public deckと一致するため、pair全体をself-ownedとは表記しない。
