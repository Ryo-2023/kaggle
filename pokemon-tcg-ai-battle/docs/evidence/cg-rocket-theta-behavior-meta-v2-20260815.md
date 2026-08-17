# Rocket Theta Behavior Meta v2 / TRAIN smoke / CEM — 2026-08-15

## 判定

source生成・hash/freshness・TRAIN-only runtime smokeは成功したが、P1をcontrolにした独立CEM gateを通過するcandidateは得られなかった。判定は `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL` とする。P1、root deck、BestKnown、Champion、production、submissionは変更していない。META_DEVとMETA_FINALは実行していない。

## source生成

受理済み `internal_ozawa-rocket-rule_de797c3646e9` をbaseに、5つのtheta table（GENERAL、LUCMIX、A09_MERGED、A07_MERGED、ABOMASNOW_R2）全体へbounded numeric transformを適用する新familyを実装した。bool、dispatch、可視情報抽出、RUSH mode、deck、環境変数、importは変換していない。既存の `ROCKET_THETA_SELECTION_V1` のblind retryではない。

- implementation: `src/mage_ptcg/opponent_ingest/rocket_theta_behavior_meta_v2.py`
- CLI: `scripts/generate_rocket_theta_behavior_meta_v2.py`
- config: `configs/meta_specialist/cg_rocket_theta_behavior_v2.json`
- design: `docs/superpowers/specs/2026-08-15-rocket-theta-behavior-meta-v2-design.md`
- plan: `docs/superpowers/plans/2026-08-15-rocket-theta-behavior-meta-v2.md`
- base source commit: `de797c3646e935157618be3edea17615430ccfec`
- base staged policy SHA: `159a5d61ce7d1d12cf955a5d2bf99845b25d3d32eedc3904ee46e21143be053e`
- canonical deck SHA: `d61230a21f488d4e78b28b37187c6a468168c0a2fff7842025e6c0409da3614a`
- output root: `runs/cg-rocket-theta-behavior-meta-20260815-a/`
- variants: 12（TRAIN 8、DEV 2、FINAL 2）
- usage boundary: `local_eval_only`
- authority: training／promotion／submission／longrun 全て false

各policy SHAは新規かつ相互に一意で、current pool identityとの重複はなかった。`runs/`全体は約127GBのため、identity scanは`docs/evidence/`とcurrent pool manifestへ限定した。巨大なopaque payloadを読むscanは行っていない。

| artifact | SHA-256 |
|---|---|
| pool manifest | `cbb89bc59cfc500a5484c7007c876a8e53672ebd2397f1c128a4400077e44741` |
| fresh meta | `f89f830803c658387b94571029f109b2f2a6a272422a43b0c7953cd7adbc6d7b` |
| `cg_historical_split.json` | `029196b14f3d6338b2cf81d9c9aa3311478d571809edc3f409ce91ba79a37830` |
| `meta_manifest.json` | `74e4a329bbd52610bcc7a1f85cace5061ae7e9498c301ae4e5133a42cced9072` |
| `intake_report.json` | `1c760d9aa8218c461e66faf283758172c9d6aead9bdc95f8e7281ad538f57eb0` |

## preflightとTRAIN smoke

全12 policyのcompile、exact 60-card、`load_opponent_pool_v1`、split verification、focused test 7件、docs validationはPASSした。

TRAIN 8件だけを `--reference-id` で明示し、P1 packageをcandidateとして両seat各1局、合計16局を実行した。

- artifact: `runs/cg-rocket-theta-behavior-smoke-20260815-a/`
- requested/completed: `16 / 16`
- status: `DONE=16`
- fault: `0`
- draw: `0`
- score rate: `2 / 16 = 12.5%`
- smoke summary SHA: `6d6b5a42862b7b16102e8be8fe3879eac606c931e521b0a98a51762093733c94`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`

最初にstdin wrapperから起動した試行は、multiprocessing spawnが`<stdin>`を再実行できずcollection前に停止した。これはCABT faultではなく起動方法の問題で、rootは `runs/cg-rocket-theta-behavior-smoke-20260815-a-failed-stdin/` へ保全した。正式CLIでの上記16局には影響していない。

## P1 CEM（generation 0のみ）

DEVをCEMへ自動投入する既存runner仕様を避けるため、まずgeneration 0だけを実行し、独立TRAIN gateを確認した。gate未通過のためgeneration 1へresumeせず、DEV/FINALは未使用である。

- artifact: `runs/cg-rocket-theta-behavior-cem-20260815-a/`
- source/control: immutable P1 package
- P1 policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- campaign seed: `20260882`
- population／elite: `16 / 2`
- independent re-evaluation: 2 repeats、両seat各2局
- screen: `544 / 544` DONE、fault 0、score rate `73 / 544 = 13.4191%`
- independent: `192 / 192` DONE、fault 0、score rate `20 / 192 = 10.4167%`
- generation screen summary SHA: `d17abfeb4834d1982f0be345ea59a19803631afb2fe04e0be335ed54de16c079`
- independent summary SHA: `700cdf38f7ebd6e1c5f7f6ea9b400283802d7e91ce2353e4b932676aef28077e`
- generation manifest SHA: `a6f3b78b32938ea6623f1cef69acee4ec9298fa60813e74f68add4117c7fabfb`
- result SHA: `a85d9744f7c12187c752a2aca1c4fa696eab5736790498173dfa56b5b6899c88`
- campaign manifest SHA: `100ce282698f916bcafd967396c4e8f83834dd9a0bca5224cad73bf312df3f0d`

screen上位2件は次のとおりだった。

| candidate | screen delta | independent mean delta | independent worst delta | seat-safe | opponent×seat-safe |
|---|---:|---:|---:|---|---|
| `cg-p1-cem-g00-c10-6af42faf4962` | `+12.50pt` | `−9.375pt` | `−15.625pt` | false | false |
| `cg-p1-cem-g00-c11-70605b06953c` | `+9.375pt` | `−1.5625pt` | `−3.125pt` | false | false |

両candidateとも独立lower-tail positive条件を満たさず、elite selectionは `independent_reeval_x2_positive_delta_gate_preserve_center`、centerはP1のままとなった。従ってDEV/FINAL、policy promotion、deck phase、`cg_bestknown_loop_v1.py`接続は起動していない。

## 研究判断

今回のgeneratorは、既存Rocket theta選択proxyより広い5-table変換面を安全にsealed poolへ接続できることを示した。しかし同一source commit由来のlocal proxyであり、独立meta/native性能の根拠にはならない。screen正差は独立再評価で反転し、P1を更新する再現性付きcandidateは得られなかった。

次はこのpoolのblind retryを行わない。候補は、(1)許可済み新snapshotの追加、または(2)family別lower-tailを推定できる複数runtime-safe source compositionである。次epochもTRAIN-only smoke→独立positive→opponent×seat seat gap≤5%→未使用DEV→未使用FINALの順を厳守する。

P1、BestKnown、Champion、production、submissionは不変。commit、push、Kaggle提出は行っていない。
