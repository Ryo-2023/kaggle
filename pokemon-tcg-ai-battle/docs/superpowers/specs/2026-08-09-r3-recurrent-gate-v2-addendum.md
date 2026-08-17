# R3 Recurrent Gate v2 Addendum

## 結論

recurrent Gate v2 は、full-corpus の同一sealed selection・teacher overlay・budget・seedを使う `current-R2` の6 reference cellsを、R3-A/R3-B の12 physical cellsへ加える。結果は18セルのclosed artifactであり、laneごとに `current-R2`、R3-A、R3-Bを選ぶ。これはsupervised selectionだけであり、runtime rollout/fault Gateを代替しない。

`current-R2` はrandom initializationの評価値ではない。`CurrentR2GateAdapterV3` の実modelを、R3と同じtrain/validation source、seed、`max_epochs`、`patience`、`min_delta`、事前固定Adam learning rate `1e-4`、global gradient clip norm `1.0`で学習し、独立validationのbest checkpointだけを比較に使う。全candidateのoptimizer update unitはphysical sequence 1件であり、各sequence内のpost-burn-in soft semantic+STOP lossを平均して1 updateとする。R3はsequence内でhidden carry/BPTTを行い、current-R2は各stepをnonrecurrentに評価する点だけが異なる。epochは両armともsealed train streamの全physical sequencesを1回消費する。

## Lane selection

- 各lane/candidateは3 seedを完備する。R3はcarry complete/STOP NLL平均がreset+0.02以内で、少なくとも一方が平均0.01以上改善し、同じmetricで2/3 seed以上が改善する必要がある。
- R3は同laneのcurrent-R2に対してcarry complete NLLが+0.02以内、top1が-0.02以内でなければならない。top3、rare-action recall、calibrationは全cellでfiniteかつmeasuredとして保存するが、追加のhard thresholdはこの版では置かない。
- R3-BはそのlaneのR3-Aよりcomplete NLLが0.01以上、またはSTOP NLLが0.02以上良いときだけ選ぶ。その他はR3-Aを選ぶ。両R3不適格時は当該laneだけcurrent-R2へfallbackする。
- R3を選んだlaneは `MODEL_SELECTED_PENDING_RUNTIME`、fallback laneは `CURRENT_R2_FALLBACK`。いずれも `promotion_authority=false` である。
- 各physical cellは、prepared streamを再走査してtrain/validation別のsequence、step、STOP available、positive STOP target、nonempty prefix、ordered nonempty prefix、burn-in step countをclosed coverageとして保存する。同laneの18 cellsでcoverageが一致しない場合、またはlane合計のordered nonempty prefixが0の場合は `UNMEASURED_ORDERED_PREFIX` とし、top-levelは `BLOCKED_COVERAGE_UNMEASURED`。R3の `MODEL_SELECTED_PENDING_RUNTIME` を出さない。

## Artifact contract and migration

- result schemaは `meta-specialist-recurrent-gate-result-v2`、selection schemaは `meta-specialist-recurrent-gate-selection-v2`。v1はread-only legacyで、新規writerはv1を出力しない。
- current-R2を含む全cellはatomic best checkpointのbasename、output rootからの相対path、raw file SHA-256、tensor-state SHA-256、candidate/lane/seed、gradient clip norm `1.0`を結合する。cell budgetとcheckpoint descriptorのclip値は一致必須であり、command identityにも同値を含める。strict readerはpath containment、file rehash、state rehash、descriptor identityまたはclip値の不一致を拒否する。`optimizer_updates > 0`、`parameter_delta_l1 > 0`、independent validation、physical-sequence update unitを持たない自己申告metric cellはselection authorityにならない。
- CUDA artifactは18 physical cellsすべてのCUDA peak/device evidenceを要する。CPUは `RESEARCH_ONLY` であり、promotion authorityを持たない。

## Scope boundary

このGateはsynthetic/32-record static resultを参照しない。runtime parallel rolloutとfault evidenceは別Gateの入力であり、このartifactはPASSを作らない。

READY teacher-quality overlayについて、既存public readerはmanifest/rule/primary evidenceのexternal anchorを検証できる。一方、現行prepared recurrent streamは`record_id`を`BCExampleV3`または`RecurrentBCSequenceV3`へ保持せず、dataset materializerが`quality_weight=1.0`を固定している。このためGate側だけでrecord単位の完全coverage overlayを推測実装してはならない。dataset public adapterがrecord identityを保持しREADY weightをstrict適用するまで、production optimizer開始条件は未充足である。

preflight receipt自身へのcoverage bindingはdataset側schema拡張待ちである。今版のGateはreceiptで再検証されるstreamからcell coverageを計測・resultへself-hash bindingするが、receipt由来のcoverage anchorが無い間はこの依存を解消済みとは扱わない。
