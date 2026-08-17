# cg cross-snapshot behavior meta / CEM — 2026-08-15

## 結論

異なる sealed source snapshot を1件ずつ使い、既存の visible-state transform を1回だけ適用する新しい meta source 生成 lane を実装・検証した。4件すべてを新規 policy SHA、static findings 0、60枚 deck、`local_eval_only`、fresh split として封印でき、P1 を実際の CEM runner へ接続できた。

ただし、今回の CEM では独立再評価の risk-aware gate（複数blockの正delta、lower-tail positive、source/opponent×seat gap ≤5%、fault 0）を満たす候補は得られなかった。P1 `cg-lethal-target-v1`、root deck、BestKnown、Champion、production、submission は変更していない。META_FINAL は未使用のまま保持した。

この結果は「新しい meta source を安全に生成して `cg_bestknown_loop_v1.py` 前段へ渡す方法」の実装成功であり、public/native opponent の性能向上や native 上位72%到達の証拠ではない。

## 実装と入力 lineage

- generator: `src/mage_ptcg/opponent_ingest/cross_snapshot_behavior_meta_v1.py`
- CLI: `scripts/generate_cross_snapshot_behavior_meta_v1.py`
- spec: `configs/meta_specialist/cg_cross_snapshot_behavior_v1.json`
- contract tests: `tests/test_cross_snapshot_behavior_meta_v1.py`
- 要件: 4 base 以上、同一 base の重複禁止、source commit 3種類以上、family は Alakazam/Comfey のみ、既存 pool/artifact の policy identity 重複は fail-closed
- split: 先頭2件を `META_TRAIN`、3件目を `META_DEV`、4件目を `META_FINAL`。CEM は train refs だけを使用

入力は k4 source audit の次の4 snapshotである。source commit は4種類、base candidateも4種類である。

| base candidate | family / variant | derived policy SHA（prefix） |
|---|---|---|
| `internal_nihei-cynthias-garchomp_c74df0e47a48` | Alakazam / `ABRA_FIRST` | `e37c5f0e7de1` |
| `internal_nihei-cynthias-garchomp_dcdd60d7e792` | Alakazam / `DUNSPARCE_FIRST` | `822db51a4e56` |
| `internal_nihei-cynthias-garchomp_e6780d317bb5` | Alakazam / `FEZANDIPITI_DRAW_FIRST` | `e12a62adbd02` |
| `internal_nihei-hydreigon-deckout_24ce278aa99f` | Comfey factorial / `DECKOUT_AGGRESSIVE_COMFEY` | `fbe744723f68` |

生成 root は `runs/cg-cross-snapshot-behavior-meta-20260815-w/`。主要 SHA は次のとおり。

- pool: `7e61cd8df139d3bb3da4dbedc54b68d14d8ec06608a7b5a991c6cc8b87638bcb`
- fresh meta: `d4a6600270a1c5fe69313f95ddc6a9052854732e511b5cffc4f8c9a4c424a788`
- historical split: `2dd76b22ce06b5ad747f1b1070c3a240e86246203c7a953cad71d5f284cad030`
- intake report: `4eb9dabda4658eb2eb85124e254e493dcdb8d11c29ba6518be825ffd097a2711`

`intake_report.json` は accepted 4、distinct source commits 4、authority flags 全 false、`research_only=true` を記録している。

## smoke

P1 package `runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1` を固定し、META_TRAIN 2 refs、両seat、各2局、base seed `20260946` で実行した。

- requested/completed: 8/8
- status: `COMPLETE`
- fault: 0
- 結果: 2勝6敗（score rate 25.00%、source intake の合法性・実行可能性確認のみ）
- summary SHA: `0143eb2df4ed1de3b0c06c7a1af07cc9cf1e55a2c2009dd795829416778dd302`

## CEM

`runs/cg-cross-snapshot-behavior-cem-20260815-w/` で P1 control 固定、campaign seed `20260947`、population 8、elite 2、2世代、独立再評価2回、screen各2局/seatを実行した。

- generation 0: screen 72、独立再評価 48、全 block fault 0
- generation 1: screen 72、独立再評価 48、fresh DEV 32、全 block fault 0
- 合計: 272局、全て `DONE` / fault 0
- CEM manifest SHA: `d979ccc4eb3b85ad2a672146c623bee6c004a6e81868ee259aaf40794145252`
- gen0 results SHA: `84f3a4b112bf1423981b21bbe46e30b3dbb084ff9a6de41f1ac0638a43df3bf7`
- gen1 results SHA: `6f2559268e1aee925a989136355a0d797438cc5d066b50a53068db7298b1d303`

gen0のscreen上位は `cg-p1-cem-g00-c02-2a2a3f63dfe6` と `cg-p1-cem-g00-c03-0c5964ac1018`。独立再評価の risk-aware 集計はそれぞれ mean delta `+12.50pt`でも、lower-tail は `0pt`／`−12.50pt`、source/opponent×seat gap は最大50%／100%で、safeではなかった。

gen1のscreen上位は `cg-p1-cem-g01-c00-39c7de5282bc` と `cg-p1-cem-g01-c06-228e31db0292`。前者は独立 mean `−18.75pt`、lower-tail `−25.00pt`、seat collapse。後者は mean `+6.25pt`、lower-tail `0pt`、opponent×seat gap 50%で、いずれも更新 gate外だった。両世代の elite は `incumbent-center` のままで、new center は P1 と同一である。

gen1 fresh DEV は、center保持後の同一P1 policyを評価した `7W-0D-9L` 対 control `4W-0D-12L`、見かけの差 `+18.75pt` だった。candidate policyの改善ではなく、同一centerのRNG差なので昇格根拠にはしない。`META_FINAL` はCEM選抜・診断に使用していない。

## 判定と次の gate

今回の判定は `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。source generator の有効性は確認できたが、P1→P2 の昇格条件は未達である。

次に再開する場合は、今回4件の transform／source identityをblind retryせず、次のいずれかを新しい source epoch として固定する。

1. 既存 artifact、source commit、policy SHA と重複しない許可済み新 snapshot。
2. source runtime を構造的に bounded にした別 deck／別 behavior family。
3. 複数 source family を十分な件数で混ぜ、family別 lower-tail・seat gap を推定できる pool。

新 pool は `fault0 smoke → 独立複数block positive → seat-safe (≤5%) → fresh DEV/FINAL` の順で gateする。通過するまで P1、BestKnown、Champion、production、submission は変更しない。commit、push、Kaggle submit も行っていない。
