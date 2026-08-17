# Plamen deck mutation `3f6451…` common24 pooled confirmation (1536)

## 結論

Plamen native policy を固定した候補 `3f64513bf1c069b7e14c889b2c94150f7e0dd58697e9004c937871344668719f` は、同じ 24 opponent / 2 seat / 8 repetition の common protocol を seed-disjoint 4 block（各 arm 384局、合計各1536局）で評価した。候補は `1121/1536 = 72.9818%`、native parent は `1095/1536 = 71.2891%` で、`+26 wins / +1.6927pt`、fault 0 だった。これは native Plamen parent に対する provisional candidate-only GO である。

ただし Tomato native pair への直接 control は未測定で、submission/package permission も未確認である。従って BestKnown の最終確定、submission eligibility、longrun GO はまだ行わない。

## 一次artifactとSHA

- 統合manifest: `runs/final-sprint-autonomous/deck-mutation-plamen-v1/common24-pooled-confirm-3f6451-1536/pooled_manifest.json`
- candidate manifest SHA: `67f63c1caadf1b478cd3865dda8193f778e4c1fc4e5d0903776b23a0e982690b`
- candidate deck file SHA: `faa7d275f5c7a963d7c7c2ffc3bb3dc8c04c731204ffb07eb58696cba152aa20`
- candidate deck multiset SHA: `650c413259e60ae4fa7c4e9eb12acd2c20a03e70ffa10d0fc36d8e348eccdd3d`
- fixed native Plamen policy SHA: `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3`
- common reference SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`

各 block の `common_protocol_summary.json` SHA は統合manifestの `blocks` 配列に固定した。4 block とも candidate/native が 384局ずつ、fault 0、全 authority false である。

## Integrity caveat

各 run の raw ledger では、既存 runner の game ID が block ID/seed を含まないため、block 間で 768件の ID が再利用され、4 block 合計で raw cross-block duplicate は 2304件となる。seed は 3072件すべて一意であり、各 block 内の ID は一意である。統合manifestでは block root を前置した canonical ID を用いて 3072行の一意性を検査し、raw ledger は変更せず保存した。この runner ID 契約は Tomato control 前に別途修正対象である。

## 次の gate

同じ common24 reference と paired seed schedule で、候補（Plamen native policy + mutated deck）を Tomato native pair と直接比較する。384局で明確な正差が出た場合のみ 768局へ進み、そこで初めて Global/Tomato BestKnown candidate として扱う。submission/package permission は別の fail-closed gate とし、今回の性能結果だけでは解禁しない。
