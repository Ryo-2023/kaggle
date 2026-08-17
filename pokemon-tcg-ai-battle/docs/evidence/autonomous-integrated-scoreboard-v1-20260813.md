# Autonomous integrated scoreboard v1 (2026-08-13)

これは、Strong Asset の共通24 arena、Student v3、guarded score-bias、deck-policy interaction、dynamic curriculum/Full6/adapter の現時点証跡を一つの機械分類へ束ねた research-only scoreboard である。既存の一次artifactは変更せず、BestKnown昇格、Champion変更、longrun、package build、Kaggle submissionは行っていない。機械可読版は同名の `.json` である。

## BestKnown の現時点分類

| 区分 | 判定 | 根拠 |
|---|---|---|
| EvaluationBestKnown | `tomatomato_archaludon` native (provisional) | 1,107/1,536 = 72.0703%、fault 0。Lucifer 1,103、Plamen 1,102。 |
| BestKnownArchaludon | `tomatomato_archaludon` native (provisional) | native top-3 の首位。ただし差は近く、GlobalBestKnownの確定ではない。 |
| TrainingEligibleBestKnown | Tomato primary / Lucifer・Plamen control | sealed snapshot と permission-filtered META_TRAIN の範囲に限定。native pairへの提出・behavior権限とは分離。 |
| SubmissionEligibleBestKnown | strong asset populationには無し | Rule v0 + root deck の既存 archive anchor のみが fallback。 |
| GlobalBestKnown | 未確定 | 残存 smoke/R7、permission closure、共通protocol coverageが未完了。 |

## 性能結果と機械ゲート

| 対象 | 結果 | native超越 | fault / protocol | permission / package / longrun | 分類 |
|---|---:|---|---|---|---|
| Tomato native | 1107/1536 | anchor | 0 / common24 PASS | bounded / NO-GO / 未起動 | EvaluationBestKnown・BestKnownArchaludon |
| Lucifer native | 1103/1536 | Tomato未達 | 0 / common24 PASS | bounded / NO-GO / 未起動 | control |
| Plamen native | 1102/1536 | Tomato未達 | 0 / common24 PASS | bounded / NO-GO / 未起動 | control |
| Student θ0 | 7/96 vs native 66/96 (−61.458pt) | No | 0 / 96 screen PASS | research-only / NO-GO / NO-GO | screen NO-GO |
| Student AWR | 3/96 vs native 66/96 (−65.625pt) | No | 0 / 96 screen PASS | research-only / NO-GO / NO-GO | screen NO-GO |
| guarded Tomato score-bias | 96 screen +9.375pt、384 confirm −2.995pt | No (confirm) | 0 / 96→384 NO-GO | research-only / NO-GO / NO-GO | confirm NO-GO |
| `3f6451` + Plamen native policy | 1121/1536 vs parent 1095/1536 (+1.6927pt) | Parentのみ | 0 / common24 PASS | NO-GO / NO-GO / 未起動 | candidate-only |
| `3f6451` vs Tomato direct | 274/384 vs Tomato 275 + draw (−0.3906pt score-rate) | No | 0 / direct384 PASS | NO-GO / NO-GO / NO-GO | stopped |
| `3f6451` + Tomato native policy | 264/384 vs Tomato control 260/384 (+1.0417pt) | bounded signal only | 0 / interaction384 PASS | NO-GO / NO-GO / NO-GO | +3pt gate未達 |

`3f6451` は Plamen parent に対しては再現した正差を持つが、Tomato direct control では負差であり、Tomato native policy interaction も事前の約+3pt gateに達しない。このため `EvaluationBestKnown`、`BestKnownArchaludon`、`GlobalBestKnown`、`TrainingEligibleBestKnown`、`SubmissionEligibleBestKnown` のいずれにも昇格しない。

## 学習・データ経路

- Dynamic META_TRAIN iteration 0 は common24 の META_TRAIN 20 opponents / 80 rows の outcome adapterであり、META_DEV 0、META_FINAL exposure 0、全 opponentが `local_eval_only`、teacher/behavior eligible 0。学習authorityは付与しない。
- Full6 repair は ordered `5:34` 4件と cross-identity near-duplicate 1件の一次再現が未完了で、published rows 0、`performance_training_ready=false`。学習入力へ接続しない。
- Common24 outcome adapter は現行 runner SHAに対して atomic re-seal済み（manifest `0679bc79…`、semantic `6ff323c8…`、ledger SHA不変 `18f1bec6…`）。これは provenance/output adapterであり、policy training labelやteacher behavior authorityではない。

## 証跡と検証

一次evidenceのパス・SHA、全rowの機械分類、fault/seat/protocol/permission/package/longrun gateは [autonomous-integrated-scoreboard-v1-20260813.json](autonomous-integrated-scoreboard-v1-20260813.json) に固定した。重点一次evidenceは native top-3、guarded score-bias、Student v3 reconciliation、`3f6451` pooled/direct/interaction、Full6/dynamic curriculum、adapter re-seal である。

検証は focused suite `35 passed in 0.75s`（`TMPDIR=/tmp`）、docs validator `Validated 13 canonical documents.`、`git diff --check` PASS。新規scoreboard JSON/Markdown以外の一次性能artifactは不変である。

## 次のゲート

このscoreboardだけでは昇格やlongrunを開始しない。再開時は Full6/permission closure を修復し、Tomato/Lucifer/Plamen の native controlを含む上位2 archetypeで、public-state value + AWR/filtered BC、hard-negative更新、deck searchを96→384→768→1536で逐次検証する。
