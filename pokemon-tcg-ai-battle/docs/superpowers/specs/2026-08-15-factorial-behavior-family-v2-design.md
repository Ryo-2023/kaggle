# Factorial behavior-family v2 設計

## 目的

既存の単一可視優先度変換では、screen差分がfresh sourceへ安定転移しなかった。そこで、既にseal済みのlocal-eval-only snapshotへ、互いに独立な可視行動軸を組み合わせるfactorial source recipeを追加し、source-family相関を明示したまま `cg_bestknown_loop_v1.py` 前段のCEMへ接続する。

この変更はnative/public metaを生成したと主張しない。P1 `cg-lethal-target-v1`、root deck、Champion、提出物は変更せず、候補は `fault0`、正delta、seat gap≤5%を満たすまで研究artifactに留める。

## 採用方式

Alakazam snapshotの既存3軸のうち、同じコード領域を競合して書き換えない2軸を組み合わせる。Pokemon priority軸（`ABRA_FIRST` または `DUNSPARCE_FIRST`）と、setup/item軸（`FEZANDIPITI_DRAW_FIRST` または `POFFIN_FIRST`）の直積4 variantを生成する。

各variantのrecipeは、例えば `ALAKAZAM_FACTORIAL_BEHAVIOR_FAMILY_V1:ABRA_FIRST+POFFIN_FIRST` のように軸の組み合わせを含む。policy SHA、base source commit、base policy SHA、canonical deck SHA、recipe、相関family、observation boundary、usage boundaryをfresh evidenceへ固定する。

META_TRAINは最初の2 variant、META_DEVは3番目、META_FINALは4番目とする。CEMのscreenと独立再評価はMETA_TRAINだけで行い、DEV/FINALはcandidate選抜後にのみ消費する。FINALは同じbase family内の確認であり、native/public性能の証拠へ昇格させない。

## 安全性と失敗条件

- 変換は既存のexact replacement関数だけを順に適用し、対象が1回でない場合はfail-closedする。
- transformed policyのstatic findings、policy hash重複、current pool/artifact identity再利用、60枚deck不整合はsealを拒否する。
- smokeでfault、illegal、timeoutが1件でもあればCEMを起動しない。
- CEMはP1 control固定、独立re-evaluation、positive-delta gate、risk-aware seat penaltyを使う。
- fresh FINALでdelta≤0、seat gap>5%、またはfault>0なら `NOT_PROMOTABLE` とし、P1を保持する。

## 実装境界

- `src/mage_ptcg/opponent_ingest/behavior_factorial_meta_v1.py`: Alakazam／Comfey factorial variant定義、exact composition、既存seal helperへの接続。
- `scripts/generate_factorial_behavior_family_meta_v1.py`: `--family alakazam|comfey`、base root、output、source epoch、seed namespace、P1 package、variant、scan rootを受けるCLI。
- `tests/test_behavior_factorial_meta_v1.py`: composition、recipe、unknown axis、duplicate matchのfail-closed契約。
- 実験artifact: `runs/cg-alakazam-factorial-meta-20260815-t/` と `runs/cg-comfey-factorial-meta-20260815-u/`、および各後続smoke/CEM/FINAL root。

## 検証

実装後、次の順で検証する。

1. factorial unit testをRED→GREENで確認する。
2. source seal、pool/fresh/split hash検証、policy/deck/import smokeを行う。
3. META_TRAIN 2 variantの両seat smokeをfault0で確認する。
4. P1 CEMを2世代、population8、elite2、独立re-evaluation 2回で実行する。
5. 未使用META_DEVとMETA_FINALを別seedで評価し、delta・seat gap・faultを集計する。
6. 通過しない場合はP1を維持し、source recipeの転移失敗として記録する。

## 実施結果

Alakazam epoch `t` と Comfey epoch `u` の2つを実行した。両方とも4件を新規policy SHAとしてsealし、visible-state-only／local-eval-only、static findings 0、fault-free smokeを満たした。しかし Alakazam は独立CEMでrobust positiveを得られず、Comfeyの一時的な独立`+25.00pt`候補はopponent別seat gapが25–50%でgate外となった。未使用META_FINALでの診断はComfey候補がP1比`−12.50pt`で、BestKnown更新は成立していない。この結果から、factorial compositionはsource生成→CEM接続の実装手段として有効だが、性能改善の根拠ではない。
