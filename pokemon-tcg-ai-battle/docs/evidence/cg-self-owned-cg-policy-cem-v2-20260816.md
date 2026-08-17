# self-owned alternate deck epoch + P1 policy CEM v2（2026-08-16）

## 結論

公式カードCSVと新しい versioned deck recipe だけから作った alternate self-owned deck epoch を生成し、固定 P1 policy surface へ接続した。6 source は合法性・package hash・両seat smoke を通過し、24/24 `DONE`・fault 0 で promote できた。一方、META_TRAIN の1世代 CEM は screen 144局、独立再評価 96局を全て `DONE`・fault 0 で完了したが、独立 positive かつ seat-safe／opponent×seat-safe を満たす候補は0件だった。P1 centerを保持し、META_DEV／META_FINALの読出し、BestKnown loop接続、BestKnown／Champion／production／submission変更は行っていない。

## source epoch

- 生成器: `scripts/generate_self_owned_cg_deck_v1.py`
- カードデータ: `data/raw/EN_Card_Data.csv`（SHA `a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373`）
- recipe: `configs/meta_specialist/self_owned_cg_deck_spec_v3.json`（SHA `b09d04996e02689bf78bdd0d40c596efc028a98ca264a095112859663e863423`）。検索／setup寄りの count 構成で、公開 kernel の deck bytes は入力していない。
- 完了 source: seed／ordinal `20260850/0`, `20260851/1`, `20260853/3`, `20260854/4`, `20260855/5`, `20260856/6`。各 exact 60、公開 canonical hash 衝突0、6件の canonical deck hash は相互に異なる。
- `20260852/2` は合法 deck の retry bound 到達で失敗した。失敗 artifact は削除せず、sealed poolから除外した。代替 ordinal 6を使ったため、成功 sourceのseed identityは重複していない。
- staged root: `runs/cg-self-owned-cg-meta-batch-v4-20260816-staged/`
- promoted root: `runs/cg-self-owned-cg-meta-batch-v4-20260816-promoted/`
- promoted pool SHA `4215effb998e0fc6fa3e4b70c52f456ba29351ed4e01fd608bb488366f69607b`、fresh meta SHA `d81837eb6e605e1fc3ab72f46dcd8d1b137a08d15ff50bf0c1f2336504c7911a`、meta manifest SHA `ee9f1da95be5951378a35946942577a9cb316d7f321741a232b55a93c3247d49`。
- 各 source を P1 root control と両seat各1局で smoke した。`runs/cg-self-owned-cg-meta-batch-v4-20260816-smoke-summary.json`（SHA `01ff46535698d2d6cdfe5baf10f315e0a34b8e14e8905091f858831612b10cf0`）は 24/24 `DONE`、fault 0。smoke性能は v4-03/v4-04 が各 `−50pt`、他は 0pt であり、これは runtime gate の合否確認であって性能昇格の根拠ではない。
- split `runs/cg-self-owned-cg-meta-batch-v4-20260816-promoted/cg_self_owned_weekend_split.json`（SHA `b1380c9b29ebc5a9c24c8bc5ba567a15054312e7ad22a8b3b8087e08e2f5a63d`）は `META_TRAIN=4 / META_DEV=1 / META_FINAL=1`。source、pool、evaluator SHAを検証してロード PASS。CEMはTRAINの4件だけを読んだ。

## package境界

P1 immutable source SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`。controlは v4-00 deck（deck file SHA `86fa825dd449e45683d5a08bb2f0bb6e5028f5d6b7439ae8d5739c21f90833cd`、canonical SHA `8e678e6fd0c6d91fa3434b96fe7a2218c144a8836377ee8e1c1e3a06c80c7f7d`）へ P1 default overlay を再bindした。control policy SHAは `9e32db3e9a0d8efea9bdaa20caa17858fb41a7f6e070ab3b4be563cb76bc60fd`。materializerは P1 の15 knobだけを変更可能にし、合法 action contract、deck 60枚、runtime を変更していない。

## CEM結果

実行 root は `runs/cg-self-owned-cg-policy-cem-v2-20260816-pilot/`。campaign seed `2026084607`、generation 1、population／elite `8／2`、initial scale `0.20`、META_TRAIN_ALL、screen各2局／opponent／seat、独立再評価2 block各2局／opponent／seat、positive-delta gateと risk-aware updateを使用した。

- screen: 144/144 `DONE`、fault 0。screen上位は c03 `+12.50pt`、c01/c05 `+6.25pt`だが、いずれも独立確認前の小標本。
- independent re-evaluation: 96/96 `DONE`、fault 0。c03は独立 blockで `−6.25pt / +12.50pt`（平均 `+3.125pt`）、c01は `−25.00pt / +25.00pt`（平均 `0pt`）。安定した positive、seat-safe、opponent×seat-safe を満たす候補は0件。
- 選定ラベルは `risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`。new centerはP1 default configと同一、eliteは `incumbent-center` 2件。
- campaign manifest SHA `167f57307e00ba29277132aaecd1aea534efd99f931bb84553c72b3518196f3a`、generation results SHA `f59a2f05293d4e675b6e4eaf738faa9b79dc0aefa4351f669f4b1e92338ab80d`、generation manifest SHA `339df8828fe4ad840f9e5cfebaf2dbc27ab1ec94c034b3711d723d3e54b3ed76`。
- `META_DEV`と`META_FINAL`は性能選抜にも診断にも使用していない。`cg_bestknown_loop_v1.py`、deck phase、BestKnown／Champion／production／submission変更も未実行。

## 判定と次の扱い

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。この v4 source epoch は screen・独立再評価で性能使用済みであり、同じ pool の blind retry はしない。次は smoke専用候補と性能holdoutを分けた別 recipe／別 policy lineageを生成し、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を通過した候補だけを BestKnown loopへ渡す。

`ono-`は公開source作者名ではない。local Git identity `bfe-lab-ono`、branch `agents/ono-cg-lethal-v1`、commit `1965b42b028f10960d08ccb4980be5b76946f98b`に由来するローカル識別子である。現行BestKnownの policy は P1 の self-authored artifactだが、root deck bytes は common/public snapshots と一致するため、pair全体を self-owned deck＋policy とは表記しない。
