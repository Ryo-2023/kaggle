---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-16
---

# cg P1 public-state mix epoch1

## 結論

公式カード CSV から生成した self-owned deck と、既存 P1 15 knob とは別の public-state-only renderer を組み合わせた6 sourceを生成し、legality、package verifier、static fallback、bounded CABT smokeを通過させた。これは新しい meta source の供給・生成経路が動くことの確認であり、性能候補の採用ではない。BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py` は不変である。

## 生成物

| 項目 | 値 |
|---|---|
| source epoch | `cg-p1-public-state-mix-epoch1-20260816` |
| plan | `configs/meta_specialist/cg_p1_public_state_mix_epoch1_20260816.json`、SHA `7e8e90d3e5039299d790e05bb78fff7fb77c768aeb33daa20e97e198173449c7` |
| source generation root | `runs/cg-p1-public-state-mix-epoch1-20260816/` |
| source manifest | SHA `94a9305e4af16aab61cdd3b82ac5d95ef930bec8c5ca5957039b8c0f9b3dfc51` |
| staged batch | SHA `24efc9b867e3df03ec3f37489ea945486fbe6582db32479ed6007cfc489f0ac4` |
| promoted pool | `runs/cg-p1-public-state-mix-epoch1-20260816-promoted/`、SHA `d55d2d2d92e3514352c7dc7a7cb1b01ecba0735fa17b889f92dfcf702cdfeda3` |
| fresh meta | SHA `78c7a64fd2675b6fe16e324c5f71a4df4b97af735477b374a088c69c19f6b568` |
| smoke summary | SHA `7c749748f92d00ac7d8c1af8ac1a01a727d19ed0cabe86aaf8d285578b9b268b` |

6 sourceは全て `parent_deck=null`、`public_parent_read=false`、authority全falseで、各 policy SHA と canonical deck SHA が distinct である。deckは `EN_Card_Data.csv` と versioned role spec v5/v6/v7だけから生成した。公開kernelや `ono-` 系譜の policy/deckは入力していない。

## TRAIN-only performance screen

生成物を performance source として再利用しないため、まず `exposure_ledger.json`（SHA `8320d94d1b58c0818f3741777791155c04dbe1211ebfe515cc2d263a0b46f7c5`）へ予約し、`cg_self_owned_weekend_split.json`（SHA `e71673ab6743d342e58e11551dfbb3b82ab871819f432d16fc0e3e73698498d4`）で `META_TRAIN=4 / META_DEV=1 / META_FINAL=1` を分離した。`META_DEV`／`META_FINAL` は下記の探索中に一度も読み込んでいない。

### P1固定 CEM

`runs/cg-p1-public-state-mix-epoch1-20260816-cem/` は P1 policy＋root deck を control／parent に固定し、campaign seed `20260816801`、population／elite `4／2`、`META_TRAIN_ALL`、独立 re-evaluation 2回、各 opponent×seat 1局で実行した。screen 80局＋独立再評価48局、計128局は全て `DONE`・fault0。screen上位は `+6.25pt` だったが、独立 block delta は上位候補で `+12.5pt / +25.0pt`、別候補で `−37.5pt / +25.0pt` となり、risk-aware minimum と seat-safe／opponent×seat-safe を満たさなかった。selection は `incumbent-center`×2、P1 center／deck／BestKnownを保持した。manifest SHAは `3fca34620a2a10128586f194dc0024834b45089c33979e198bd2937a4f8fee1`、results SHAは `080232b4fb2616f163f2803bce5523b1d958a6f1f308a03396542b9f31d9f313` である。

### public-state candidate screen

6つの self-owned public-state packageを P1 control と同じ `META_TRAIN`、seat、seed panelで評価した。file-backed runner `scripts/run_cg_public_state_mix_candidate_screen_v1.py` を使い、初回の `<stdin>` spawn失敗はCABT証拠へ算入せず、96局の本実行は全て `DONE`・fault0となった。candidate/control は block内でpaired集計した。

| candidate | screen（各8局） | seat rates | 判定 |
|---|---:|---|---|
| `ahead-lethal-conserve` | +25.0pt | 0.75 / 0.50 | 独立再評価へ |
| `conservative-visible-board` | +12.5pt | 0.50 / 1.00 | 独立再評価へ |
| `race-and-stability` | +12.5pt | 0.50 / 0.50 | 独立再評価へ |
| `target-pressure` | +12.5pt | 0.75 / 0.50 | 独立再評価へ |
| `damaged-board-retreat` | −12.5pt | 0.75 / 0.75 | 除外 |
| `prize-behind-pressure` | −37.5pt | 0.25 / 0.50 | 除外 |

上位4件を別 seed `20260816803`、各 opponent×seat 4局（候補・control計256局）で独立再評価した結果は、それぞれ `−3.125pt`、`−25.0pt`、`−12.5pt`、`−18.75pt` で、全て fault0だが positive gate不成立だった。screen ledger SHAは `6e932eb7a8cd9fd42ad43afbe6062965eabf75a809c3d4a0ea6add9c87f7fec8`、paired summary SHAは `2f997be600ad545e417cc155a616abab5665eb1509c755de0e5fcf70541d558c`、独立再評価 summary SHAは `9d954e808059c9bb887572e382c7d571f0b06ae381f2cb54c8ab9c6e7ec5383e` である。

`ono-` は公開kernel作者名ではない。この repo の local Git identity `bfe-lab-ono`、branch `agents/ono-cg-lethal-v1`、commit `1965b42b028f10960d08ccb4980be5b76946f98b` に由来するローカル識別子であり、今回の6 sourceの出典ではない。

## 新 renderer と生成器

- `src/mage_ptcg/meta_specialist/cg_p1_public_state_mix_v1.py`（SHA `15cd9037e89aa70b98d4e6d5492165ce8bea08c27d86906515d42fe496729b6c`）
  - prize gap、visible attack target、visible damage、damaged active、ready benchだけを利用する8 bounded integer knobs。
  - opponentの hand、deck、discardの内容・identityは参照しない。search APIは使わない。
  - P1 parent SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` に固定。
- `scripts/generate_self_owned_cg_public_state_mix_meta_v1.py`（SHA `f7bcc043b93629b473dbdaa3be1e9dcd0f151acea20ab17b8eb08aefba862f25`）
  - planを検証し、deck generation、policy materialization、generation manifest、staged batchを一貫して作る。
  - `--execute`なしでは artifactを作らず、training／promotion／submission authorityを付与しない。

## 検証

1. focused test 7件が pass（renderer 5件、plan loader 2件）。
2. 6 packageの verifier、AST compile、60枚 deck、`ROOT_DECK` binding、public-only文字列検査、static fallback smokeが pass。
3. smokeは同一条件（reference `aman_crustleaware_fighting` と `official_random`、両seat、各1 game、base seed `2026081601`、worker1、recycle2）で24局を実行し、24/24 `DONE`、fault0だった。候補別の参考 scoreは 1/4 または 2/4 の範囲で、少数局のため性能根拠にしない。
4. 最初に batchの `staged/`（`cg/`なし）を smokeへ渡した試行は `buffer full. capacity:7` → `BrokenProcessPool` になった。既存 evaluator契約どおり `packages/`または `staged-with-runtime/`（shared `cg/`を含む）で再実行し、同じ候補が fault0になった。これは候補失敗ではなく、runtime bundle欠落を原因とする harness入力ミスであり、source artifactの静的契約は変更していない。
5. fault-free smokeを根拠に `runs/cg-p1-public-state-mix-epoch1-20260816-promoted/`へ promotionした。promotionは fresh meta封印のみで、CEM接続・BestKnown更新はしていない。

## 次の判断

この epochは `SOURCE_GENERATION_PASS / BOUNDED_SMOKE_PASS / TRAIN_SCREEN_NO_UPDATE / BESTKNOWN_UNCHANGED` とする。P1 CEMとpublic-state candidate screenのいずれも独立 positive・seat-safe・opponent×seat-safeを満たさなかったため、`META_DEV`／`META_FINAL` は未使用のまま保全し、P2／BestKnown／`cg_bestknown_loop_v1.py`へ接続しない。今回の6 source、policy SHA、deck SHA、seedは性能使用済みとして blind retryせず、次は生成時点で holdout を分離した別 source epoch または構造的に異なる self-owned rendererを作る。
