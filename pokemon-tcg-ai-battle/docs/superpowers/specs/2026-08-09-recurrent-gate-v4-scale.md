# Recurrent Gate v4 fused-lane execution

## 結論

actual Gate は cell ごとにfull corpusを再decodeしない。laneごとに `current-R2`, `R4-A`, `R4-B` の3 candidates × 3 seedsを同時に保持し、一回decodeしたphysical sequenceを全active cellへ渡す。split、teacher overlay、semantic target、update unitは共有するが、optimizer、hidden state、early stop、checkpoint、metricsはcellごとに独立させる。

旧実測では selection/split authorityの再構成だけで Alakazam 2,475.9秒、Archaludon 849.9秒、peak RSSは各約596,000 KiBだった。18 cellsを逐次実行し、各epoch/validation/coverageで同じcorpusを再構成する方式は本格学習前のGateとして運用不能である。

## Job lifecycle

1. lane authority、teacher-quality overlay、v4 projectionをjob-startで一度preflightする。
2. source shardのregular-file identityとraw SHA、selection index、overlay sidecar、projection aggregateを外部anchorで固定する。
3. 9 modelsと9 optimizersをseed分離して初期化する。active cellだけを保持し、early-stop後のcellは更新しない。
4. train partitionを一回streamし、各physical sequenceを全active cellへ順番に適用する。R2はhidden無し、R4はcell固有hiddenをepisode境界でresetする。
5. validation partitionも一回streamし、cell別carry/reset/complete/STOP/top-k/rare/calibrationを集計する。
6. best independent validation stateをcell別にatomic保存し、次epochのactive setを決める。
7. 全cell終了後にruntime microbenchmarkをcell別に行う。混合training passのaggregate timing/VRAMをpromotion用p50/p95へ流用しない。

## Integrity conditions

- source decode failure、record/overlay mismatch、v4 projection mismatchは、そのpassで更新した全in-memory cellを破棄し、checkpoint/resultをpublishしない。
- candidate間でtensor、optimizer、hidden、RNGを共有しない。controlled seed identityをartifactへ固定する。
- one-pass shared decodeがcellごとのtarget orderを変えないことをtestする。
- cellを単独実行した場合とfused実行した場合で、同じseed/configのoptimizer update後state hashとvalidation metricsが一致することを小fixtureで証明する。
- coverageはlane authorityから一度だけ集計し、全9 cellsへ同じcoverage hashを参照させる。cell自己申告値を持たせない。
- peak VRAMが9-model trainingでdevice capacityを超える場合、3 seeds単位などへbatchを縮小する。ただし同一corpus passを何度も繰り返す前に、固定したbatch planと予測/実測costをartifactへ残す。

## Promotion boundary

fused executionは計算量最適化であり、Gate条件を緩めない。R2も同一train/validation authorityとoptimizer budgetで学習し、R4はtemporal ablation、R2 absolute noninferiority、lane別fallback、teacher-quality READY、ordered/STOP/rare coverage、CUDA/runtime/fault Gateをすべて満たす必要がある。

