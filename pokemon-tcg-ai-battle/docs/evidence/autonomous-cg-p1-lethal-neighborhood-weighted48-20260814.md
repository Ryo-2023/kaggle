# cg-lethal P1 observed-failure neighborhood weighted48

確認日: 2026-08-14 JST

## 結論

固定した `cg-lethal-target-v1` P1 policy の公開 telemetry から抽出した失敗形だけを対象に、bounded な3候補を weighted48 で比較した。3候補とも 12 opponent × 2 seat × 2 repetition = 48 局、candidate/control 合計96局、workers=12、recycle=16、fault=0 で完了したが、P1 control を上回らなかった。`common24`、`384`、`768`、学習、teacher、promotion、submission、longrun は起動していない。

## 観測失敗と候補

P1 public telemetry（96局、4,077 decision rows、private-token scan 0）では、合法な lethal ATTACK が存在する192 stateのうち29 stateで非ATTACKを選択し、該当terminal gameの18件がlossだった。複数 lethal attack が存在する42 stateのうち30件はloss gameに含まれた。この観測以外の仮説や外部teacherは候補生成に使っていない。

| candidate | 観測失敗に対応する bounded change | policy SHA |
|---|---|---|
| `cg-lethal-lock-v1` | 公開 hp 以下の全 legal ATTACK に +30000 | `7d5704acf0e83eeb37f6f06202f25984b560dd4347720ffcd83d89b3fafd7c10` |
| `cg-lethal-setup-lock-v1` | ATTACH/EVOLVE が同時に存在する stateだけ lethal ATTACK に +30000 | `689e1eb88eba04f02c5f187f0d814a8818d5e30a871013337b5163ced40b6f90` |
| `cg-lethal-resource-first-v1` | 複数 lethal のうち最小 damage の ATTACK に +16000 | `7c5c48277e886c3b13631df0184a0697555a9cd65228543b382e7564cbfc4c48` |

共通の P1 base/control policy SHA は `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`。候補は public opponent hp、attack type、attack damage、ATTACH/EVOLVE の合法 option だけを参照し、未知・不正・例外は P1 exact fallback とした。

## weighted48 結果

| candidate | candidate W-D-L | control W-D-L | candidate score | control score | delta |
|---|---:|---:|---:|---:|---:|
| `cg-lethal-lock-v1` | 7-0-41 | 7-0-41 | 14.5833% | 14.5833% | +0.0000pt |
| `cg-lethal-setup-lock-v1` | 8-0-40 | 12-0-36 | 16.6667% | 25.0000% | −8.3333pt |
| `cg-lethal-resource-first-v1` | 11-0-37 | 11-0-37 | 22.9167% | 22.9167% | +0.0000pt |

全 arm で seat 0/1 は各24局、candidate/control の paired strata は48、fault=0、authority は全て false。lock は candidate seat 0/1 が2/5勝、control が4/3勝、resource-first は candidate 5/6勝、control 4/7勝だったが、総合差は0であり昇格根拠にならない。setup は両 seat 4/4勝対 control 6/6勝で明確な負差だった。

## 再現情報

- lock root: `runs/final-sprint-autonomous/cg-p1-lethal-neighborhood-lock-weighted48-20260814-v1/`
  - summary SHA: `68f7f5e564f680b73c4482c2ee045625d12142a1a42bfb587184c22924c6a174`
  - manifest-complete SHA: `9c8f0c4fb23062bc490fde197f34cf72548d94b00dcf814566df79f85cbc368c`
  - base seed: `40610000`
- setup root: `runs/final-sprint-autonomous/cg-p1-lethal-neighborhood-setup-weighted48-20260814-v1/`
  - summary SHA: `507ccab819fbba3445ce8e3b5ccada27e6d20b0145f4cd9009b0308e036937e6`
  - manifest-complete SHA: `457bee3a6b48ef1ed686f097553813f528743b7bb9ad46f664c511e14352df2d`
  - base seed: `40610100`
- resource-first root: `runs/final-sprint-autonomous/cg-p1-lethal-neighborhood-resource-weighted48-20260814-v1/`
  - summary SHA: `aaadbbd38214511c8414092345a77b4853532781eb3d7a78006f41dc2ef00692`
  - manifest-complete SHA: `65af494c313d69ff8179e30aa7f4d4b2615f632c66b4c33dfb140d53f226bae6`
  - base seed: `40610200`

実装・テスト SHA:

- module `src/mage_ptcg/meta_specialist/cg_p1_lethal_neighborhood_v1.py`: `1da85ac835697e73bf8d45fc2b70097e7a1e8dcccae70e726eebeb53f1c287c6`
- runner `scripts/run_cg_p1_lethal_neighborhood_v1.py`: `82119c74a0fd97244a1834100102c0782a2cbc115bb861a1aa9c193b092c6eb6`
- test `tests/meta_specialist/test_cg_p1_lethal_neighborhood_v1.py`: `3aca226804aa8f83cf4b311a61dcfac974d73c6fb8473b41ea7cf12246a06d00`

## 判定

これは P1 の observed-failure neighborhood を閉じるための research-only screen である。全候補が `candidate_delta_points <= 0` なので、同じ3候補の再実行、common24拡張、384/768、学習・teacher・promotion・submission は行わない。Rule v0 の過去の deck/policy探索は履歴として保存するが、現在の主経路ではない。次の実験は新しい公開観測失敗または新しい cg parent identity が得られるまで保留する。
