# self-owned policy family v9–v11 / CEM・deck診断（2026-08-16）

## 結論

v9、v10、v11で、公式カードCSVから生成した self-owned deck source と P1 parameter surface を使った研究専用 CEM を継続した。全ての採用した CEM／診断行は `DONE`・fault0 だが、候補は `opponent×seat-safe`（候補と control の各 opponent×seat 差の絶対値 ≤ 0.05）を満たさなかった。したがって P1、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py` の接続は変更していない。v10／v11の DEV／FINAL は未使用のまま保全している。

今回の v11 deck診断では、promoted source-only directoryを直接渡した初回試行が `cg/` runtime欠落で `buffer full`／`BrokenProcessPool` になった。これは性能結果ではない。完全な `packages/core-v11-00` を使用した再試行は 320/320 row・fault0 で完走し、deck差の TRAIN-only 診断は candidate objective `0.5500` 対 control `0.490625`（差 `+5.9375pt`）だったが、candidate seat rate `0.4625/0.6375`（gap `0.1750`）で gate外である。

## v9 — c06近傍の狭い再探索

- plan: `configs/meta_specialist/self_owned_cg_policy_family_v9_c06_neighborhood.json`
- generated root: `runs/cg-self-owned-cg-policy-family-v9-c06-neighborhood-20260816/`
- promoted root: `runs/cg-self-owned-cg-policy-family-v9-c06-neighborhood-20260816-promoted/`
- batch manifest SHA: `931dfba7e8ce0c964261e7cce5df7acfbfe743f5ff28fdf694ab9bbad3a85d96`
- pool／fresh／meta／split SHA: `6acedfa92068b59dd886b270f3e45b4e7ac8f15df1b46b647c7077d8ebecd848`／`89f68e24de0af93f2e51ff7e5b35d03e92036e407a31f5d87c5e8f18de1f7ec9`／`57ec0dbbc182436eb0c2673ae87a399616544e8cd8761106c31278de4e86c775`／`962f5127f1b9bf77f4da4f6a1714d112f9a17f978ababb9ebf7bc1acff24a728`
- CEM root: `runs/cg-self-owned-cg-policy-family-v9-c06-neighborhood-20260816-cem-v8deck/`
- c01 (`cg-p1-cem-g00-c01-5744ee38ab80`) は screen `0pt`、独立 repeat は `+8.3333pt / +25.0000pt`（mean `+16.6667pt`）だが opponent×seat-safe 外。
- c02 (`cg-p1-cem-g00-c02-aeb28256c50d`) は screen `+4.1667pt`、独立 mean `+10.4167pt`（minimum `−8.3333pt`）で、opponent×seat-safe 外。
- v9 c01を同じ v8 deck で 96局/opponent/seat（2304 row）へ拡大すると `−2.691pt`、c02は `+3.299pt`となったが、いずれも opponent×seat-safe 外。c01は独立拡大で反転したため、robust候補とは扱わない。
- v9の TRAIN は性能使用済みであり、同じ split の blind retry や DEV／FINAL read は行わない。

## v10 — broad-support source epoch

- plan: `configs/meta_specialist/self_owned_cg_policy_family_v10_broad_support.json`（source epoch `self_owned_official_card_data_broad_support_v10_20260816`）
- initial config: `configs/meta_specialist/cg_p1_c02_initial_v10.json`
- generated root: `runs/cg-self-owned-cg-policy-family-v10-broad-support-20260816/`
- promoted root: `runs/cg-self-owned-cg-policy-family-v10-broad-support-20260816-promoted/`
- batch manifest SHA: `b55b2e50ed94ab5f47399cf571285b5b1becbf7cfddc2494dfa7bbb3e430ef7e`
- pool／fresh／split／meta SHA: `76d657ef0a271a3ac3a8a977eb98120fc3aab2841a2b5d3a5f1fb806796b3aa1`／`65a4febdf6fa4af4c89fc25c45c428163080a9f63462e45877a7910f8dc41fe7`／`10b694d9b38a3d5359ab848efa38919e07718b07150c1f917c6392ddce373876`／`8d05b725c1baa6db185c7a1743f62bb42e72cc6c3063dbce5ec9e9cb62ad3eed`
- source package smoke は `16/16 DONE`・fault0（runtime gateのみ）。
- c02 policyを v10 TRAINへ直接 transfer（32局/opponent/seat、768 row）した結果は candidate `52.344%` 対 control `52.995%`、差 `−0.651pt`。新 source が c02をそのまま改善しないことを確認した。
- v10 CEMは screen／独立とも全row fault0。c06 (`cg-p1-cem-g00-c06-c6588757a82f`) は screen `+25.0000pt`、独立 risk-aware mean `+14.5833pt`だが candidate seat gap `0.1667`、opponent×seat-safe 外。c07も seat gap `0.0833`で外。
- c06を 96局/opponent/seat（2304 row）へ拡大すると candidate `54.3837%` 対 control `51.1285%`、差 `+3.2552pt`、global seat gap `0.0269`。ただし opponent×seat gaps は ability `0.0208`、attach `0.1771`、attack `0.1354`、balanced `0.0052`、core `0.1042`、lethal `0.0833`で strict gate外。
- v10の DEV／FINALは未使用であり、candidate holdoutへ進めていない。

## v11 — heterogeneous source epoch と wide CEM

- plan: `configs/meta_specialist/self_owned_cg_policy_family_v11_heterogeneous.json`
- initial config: `configs/meta_specialist/cg_p1_c06_initial_v11.json`
- generated root: `runs/cg-self-owned-cg-policy-family-v11-heterogeneous-20260816/`
- promoted root: `runs/cg-self-owned-cg-policy-family-v11-heterogeneous-20260816-promoted/`
- batch manifest SHA: `0f2765dd588843291f493d22639d8292191463f52c74b2cd7350d7013bd4d73b`
- pool／fresh／split／meta SHA: `e50d826f3c3013111efc606a4c3c757123bd0f27139a029a3f9727fd0c4dbcea`／`fc16ac1af05e2a4d52d89f4d52cace4cdbf54c15bc6b3b1c00a9fb9c0a916957`／`dc1068efbee3fc6a5f5fa05cb4786b25925ed7365daf3ccf45aa604071cc8e84`／`14b2d1bd386556ba5ea7cd4863392b019ef6f86ef335ee39379594e944def699`
- role-separated／broad-support／stability の 12 source package smoke は `24/24 DONE`・fault0。splitは TRAIN10／DEV1／FINAL1。
- small CEM（screen360＋独立240、全row fault0）は c00 screen `+7.5pt`、独立 `+10.0pt / −7.5pt`、c03 screen `+10pt`、独立 `−2.5pt / +7.5pt`。positive／seat-safe／opponent×seat-safeを満たす候補はなく、centerを保持した。
- wide CEM root: `runs/cg-self-owned-cg-policy-family-v11-heterogeneous-20260816-cem-wide/`。population／elite `24/4`、screen1000＋独立800、全row fault0。c07 (`cg-p1-cem-g00-c07-f005c08e4d41`) は独立 `+12.5pt / +6.25pt`、seat gap `0.025`だが opponent×seat-safe 外。c02は独立 `+8.75pt / −12.5pt`、c16／c20も安全 gate外。selectionは全て `incumbent-center`。

## v11 deck差分診断

目的は policyを同一にして deckだけを替えることである。candidate は完全package `runs/cg-self-owned-cg-policy-family-v11-heterogeneous-20260816/packages/core-v11-00`（deck SHA `6cacde350762715a6bc60e8947be21ece917b7bf55fa6b2d6b3598e48b42baeb`、policy SHA `74b66f1f1fe6677faa094cc1f122cd0e27628aadce320d56c8bba91ed5a7eeba`）、control は v8 balanced deckを束ねた `runs/cg-self-owned-cg-policy-family-v11-heterogeneous-20260816-cem/generation-0000/candidates/candidate-00`（deck SHA `e4050c33a6e336d632bb4b837fc609a81d7f2e3ceba736b878831de82e1f3c2a`）である。control／candidateはいずれも c06 initial policy surfaceを使う。

- 初回の source-only promoted directory直渡しは `cg/` runtimeが無く、先頭局で `buffer full. capacity:7`／`BrokenProcessPool`。この出力は不完全artifactであり、性能根拠から除外した。
- 完全packageでの1局/opp/seat確認は `runs/cg-self-owned-cg-policy-family-v11-heterogeneous-20260816-deck-core-vs-v8-train-v4-1percell/`、40/40 row・fault0。
- worker1での8局/opp/seatは `runs/cg-self-owned-cg-policy-family-v11-heterogeneous-20260816-deck-core-vs-v8-train-v5-8percell/`、320/320 row・fault0。summary SHAは `88fbda0e006e58e79e40462c4b5391aa7c67032ad2d50f4b5a7f8b4ab6d99d30`。
- candidate objective `0.5500`（88/160 wins、draw0）対 control `0.490625`（78/160 wins、draw1）、paired objective差 `+5.9375pt`。candidate seat rateは seat0 `0.4625`／seat1 `0.6375`、gap `0.1750`。したがって有望なTRAIN signalではあるが、seat-safe／opponent×seat-safe gate外であり、DEV／FINALへは進めない。

## 判定と次の生成条件

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。v9〜v11の性能使用済みTRAIN artifactは再利用せず、v10／v11のDEV／FINALは未使用のまま保全する。次のepochでは、(1)完全packageのruntime gateをsource promotion前に明示する、(2) opponent×seatの相関を下げる source／deck recipe を生成する、(3) CEMの初期候補を同一policyだけでなく独立lineageへ分散する、を必須条件とする。全ゲート `legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` 通過前に `cg_bestknown_loop_v1.py`、BestKnown、Champion、production、submissionを変更しない。

### 再現コマンド

```bash
PYTHONPATH=.:src .venv/bin/python scripts/run_direct_cg_policy_pair_diagnostic_v1.py \
  --candidate-package runs/cg-self-owned-cg-policy-family-v11-heterogeneous-20260816/packages/core-v11-00 \
  --control-package runs/cg-self-owned-cg-policy-family-v11-heterogeneous-20260816-cem/generation-0000/candidates/candidate-00 \
  --split runs/cg-self-owned-cg-policy-family-v11-heterogeneous-20260816-promoted/cg_self_owned_weekend_split.json \
  --pool-root runs/cg-self-owned-cg-policy-family-v11-heterogeneous-20260816-promoted \
  --output <fresh-output-root> --split-name META_TRAIN \
  --games-per-opponent-seat 8 --base-seed 20261799 --workers 1
```

全artifactは research-only／authority全false であり、commit、push、Kaggle提出は行っていない。
