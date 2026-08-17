---
title: "cg portfolio hard-negative source factory v22-v24"
date: 2026-08-16
status: evidence
---

# 結論

`portfolio-hard-negative source factory v1` の実装前診断として、v22〜v24のdeck-bound source poolを生成し、CABTのbounded smokeとsource-side validationを実施した。結果は `SOURCE_FACTORY_SCREEN_PASS / SOURCE_SIDE_STRICT_GATE_FAIL / BESTKNOWN_UNCHANGED` である。v24は5 source、960局、fault 0まで到達したが、source-side strict gate（全referenceでseat gap `<= 5%`）を満たすsourceが0件だったため、P1 CEM、`cg_bestknown_loop_v1.py`、DEV／FINAL、deck phaseへ接続していない。

## 固定された現行状態

- 現行BestKnownは self-authored P1 policy＋common/public root deck。
- P1 policy SHA-256: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- root deck raw SHA-256: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py`は不変。
- 全artifactは research-only / local-eval-only、authorityは全てfalse。

## v22: cross-archetype hard-negative

Fire／Dark／Lightning／Fightingの4 deck recipeを公式カードCSVから生成した。staged pool SHAは `5c05ee8b8e6aecd8ff89751532563c5593c83e160032d26093424e824fa90634`、source smokeは32/32 `DONE`・fault 0（5W-27L、15.625%）だった。

新seedのfast independent validation（4 source×2 reference×両seat、64局）は64/64 `DONE`・fault 0（5W-59L、7.8125%）。P1 policyはLucario中心であり、cross-element sourceはpolicy互換性を失って大きく崩れたと判断する。v22は性能候補にせず、P1 CEMへ接続しない。

- staged manifest SHA-256: `5929d2483454a3facdadaa05a19e0db4bba63868a5d20a616e5c183d17ef8c0b`
- smoke summary: `runs/cg-portfolio-hard-negative-source-v2-20260816/source-smoke-4x4/`
- independent summary: `runs/cg-portfolio-hard-negative-source-v2-20260816/source-validation-fast-4x2/`
- 追加の `source-validation-4x4x4` は26/128で手動停止したため、性能証拠には使わない。

## v23: Lucario-core 4-source retry

P1との互換性を優先し、Lucario coreを固定してbalanced／search／tempo／switchの4 deck-bound packageを生成した。初回生成はtempo role countが59枚となりfail-closedしたため、specを修正してretry1だけを採用した。retry1のstaged pool SHAは `2b3b63f48c5820a3365be46b80f718e0508e8519a4c2ccff9b0026876a26fdf8`、manifest SHAは `60aa4f22c10a8b48ebd5024f854564b57280674186cf8a82d911bd03e5c308ac` である。

smokeは32/32 `DONE`・fault 0（9W-23L、28.125%）。fast validationは64/64 `DONE`・fault 0（14W-50L、21.875%）。8×4 validationは256/256 `DONE`・fault 0（49W-207L、19.140625%）だった。sourceごとのaggregate seat差は残り、strict source-side objectiveを満たす選択を封印できなかったため、v23もP1 CEMへ接続しない。

- retry1 root: `runs/cg-portfolio-lucario-core-source-v3-retry1-20260816/`
- smoke summary: `runs/cg-portfolio-lucario-core-source-v3-retry1-20260816/source-smoke-4x4/`
- validation summary: `runs/cg-portfolio-lucario-core-source-v3-retry1-20260816/source-validation-8x4/`

## v24: Lucario-core 5-source source-side validation

v23の4 recipeにrecoveryを追加し、balanced／search／tempo／switch／recoveryの5 sourceを生成した。staged pool SHAは `660d1dfd1197cd0f45c3bdd82cd8f11ed56beb1aa9dc1dd1002eaa064ea89a12`、plan manifest SHAは `1e074966cc7e7d2e77154f1621b9ff4df1d84c71e69f6ca0d36d5432df4c819f` である。policy/deckは5/5 uniqueで、公式カードID・60枚合法性・package verifierを通過した。

### 実行結果

| stage | 局数 | 結果 | 勝率 | 用途 |
|---|---:|---|---:|---|
| smoke 5×4 | 40 | 40/40 `DONE`, fault 0 | 52.5% | runtime gate |
| validation 8×4 | 320 | 320/320 `DONE`, fault 0 | 16.5625% | screen |
| validation 16×4 | 640 | 640/640 `DONE`, fault 0 | 17.96875% | source-side objective診断 |
| validation 32×3 | 960 | 960/960 `DONE`, fault 0 | 23.5417% | final source-side screen |

最終3-reference（`official_random`、`aman_crustleaware_fighting`、`aristophanivan_probabilistic`）のstrict objectiveでは `selected_ids=[]` となった。v24のmean／worst／max seat gap／objectiveは次のとおり。

| source | mean | worst | max seat gap | objective | strict eligible |
|---|---:|---:|---:|---:|---|
| balanced-pressure-v24-00 | 0.25521 | 0.03125 | 0.0625 | 0.14323 | no |
| search-pressure-v24-01 | 0.25000 | 0.03125 | 0.0625 | 0.14062 | no |
| tempo-reserve-v24-02 | 0.23958 | 0.01562 | 0.1250 | 0.12760 | no |
| recovery-control-v24-04 | 0.22917 | 0.01562 | 0.21875 | 0.12240 | no |
| switch-counter-v24-03 | 0.20312 | 0.03125 | 0.09375 | 0.11719 | no |

最終 objective artifactは `runs/cg-portfolio-lucario-core-source-v4-20260816/source-validation-32x3-final/source_side_objective.json`（SHA-256 `a105212969e171693675654f06c5d9ce63bc747ed3848a271f173e09db88160f`）。faultは0だが、seat gapが1ゲーム差でも5%を超える候補があり、source poolとしての選択条件を満たさない。

## 次の方式

固定configを5件作るだけではsource-side strict gateを通らないため、次は**deck-bound source-side CEM**を新しいepoch／seed namespaceで設計する。各deck recipeに複数のP1 parameter configurationをサンプルし、同じ3-reference portfolioのterminal WDL・seat・opponent identityだけでscreenする。上位候補は独立seedで全referenceを再評価し、4件以上が strict gateを通過した場合だけTRAIN splitとP1 policy CEMへ進める。

この方式は設計候補であり、実装・CABT起動はまだ開始していない。既存 `scripts/run_robust_adversarial_source_cem_v1.py` のblind retryではなく、新しいdeck-bound candidate identityと新しいsource epochを要求する。

## `ono-` の provenance

`ono-` は公開kernel作者名や外部source名ではない。根拠はローカルGitの次の一次情報である。

- local Git identity: `bfe-lab-ono <ono.ryosuke.36t@st.kyoto-u.ac.jp>`
- sealed branch: `agents/ono-cg-lethal-v1`（および closure branch）
- sealed commit: `1965b42b028f10960d08ccb4980be5b76946f98b`
- commit subject: `feat(submission): cg lethal提出候補を封印`
- remote ref: `origin/agents/ono-cg-lethal-v1`

したがって「ono-owned」は、このローカル署名とbranchで作ったself-owned package／policy lineageを指す。現行P1 policyはこのcommitで封印されたもので、root deckは同じcommitで差し替えた `deck.csv`（raw SHA `2a541d…`）である。ただしroot deck bytesと同一のlocal snapshotが複数あるため、repo証拠だけから単一の公開元kernelを逆算することはできない。`ono-`を公開sourceの出典として扱ってはならない。

## 権限・再現性

今回のv22〜v24では、BestKnown／Champion／production／submission、DEV／FINAL、`cg_bestknown_loop_v1.py`を変更・接続していない。commit、push、Kaggle提出も行っていない。再現時は各runのmanifest／summary／SHAを確認し、v22〜v24のpool・seed・候補をblind retryしない。
