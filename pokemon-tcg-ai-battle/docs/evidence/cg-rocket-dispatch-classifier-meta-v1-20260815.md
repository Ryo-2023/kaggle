# Rocket Dispatch Classifier Meta v1 / TRAIN-only CEM Evidence

日時: 2026-08-15 JST  
判定: `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`

## 結論

受理済みRocket sourceの `_TIER_A_TO_GROUP` classifierについて、公開card IDのfamily所属だけをboundedに変える12 policyを生成し、TRAIN-only smokeとP1 control固定CEMへ接続した。全CABT局はfault 0・draw 0だったが、独立再評価でlower-tail positive、seat-safe、opponent×seat-safeを同時に満たす候補は0件だった。P1 centerを保持し、DEV／FINAL、generation 1、deck phase、BestKnown loop接続は起動していない。

## Source generation

- base source: `runs/cg-fresh-internal-meta-intake-20260815-f/internal_ozawa-rocket-rule_de797c3646e9/`
- source commit: `de797c3646e935157618be3edea17615430ccfec`
- implementation: `src/mage_ptcg/opponent_ingest/rocket_dispatch_classifier_meta_v1.py` (SHA-256 `ef1608c46072bfe823d2237aacb0f1e27ea637c3e89dc82a89685f8002ee6e4c`)
- CLI: `scripts/generate_rocket_dispatch_classifier_meta_v1.py` (SHA-256 `32d0b4655257c8a6d854c120a55952f8517e3fbf859d8296943ae0b9d3a125ce`)
- config: `configs/meta_specialist/cg_rocket_dispatch_classifier_v1.json` (SHA-256 `9b091248f34198bf3e24b0efe45abd0b8f031ac964c459a41f40a927d23922a4`)
- recipe: `_TIER_A_TO_GROUP`の既存13 keyのvalueだけを置換。deck、observation extraction、stateful commit、theta、import、environment、fallbackは不変。
- variants: 12（TRAIN 8／DEV 2／FINAL 2）
- root: `runs/cg-rocket-dispatch-classifier-meta-20260815-c/`
- pool SHA: `b3ccdec6e68bfebe78ba55d1b859432d022f1aa17c5dc21320d47355c549664d`
- fresh SHA: `294e2157f7407d16d18785a6ed865bbc050b4fd8adf08da96b9b9ccaa5112e51`
- split SHA: `9749aa51b6c1941ad81c53642b7e716117ced5f15b96362329d1d39ef3bdd482`
- meta SHA: `cdcf280c151895c9aceacb568a4f31f1a0aac15b4bbf75c9a189eaceed58733a`
- intake SHA: `ddb70296135d24419d26f051a2bc08ad9c7b7563e07dbfee90c0a6de1473cb2b`
- all rows: `local_eval_only`、authority全false、static findings 0、compile／pool loader／exact 60／split verification PASS。

## Evaluation

### TRAIN smoke

実行対象はMETA_TRAIN 8件だけ（両seat、各1局、base seed `20260885`）。

- requested/completed: `16/16`
- outcomes: `1W-0D-15L`
- fault: `0`
- smoke summary SHA: `40f4f2b73f66a5c6e56614946eac97018abb6d288a8b0c5106b999cef3bb4abd`

### P1 control固定 CEM generation 0

実行rootは `runs/cg-rocket-dispatch-classifier-cem-20260815-c/`。population 16、elite 2、initial scale fraction 0.05、campaign seed `20260886`、independent re-evaluation 2回（各96局）、positive-delta／risk-aware gate、META_TRAIN 8件のみを使用した。

- screen: `544/544 DONE`、`73W-0D-471L`、score `13.4191%`、fault `0`
- independent total: `192/192 DONE`、`30W-0D-162L`、score `15.6250%`、fault `0`
- screen initial elites: `cg-p1-cem-g00-c04-2299fc9097c9`（`+12.50pt`）、`cg-p1-cem-g00-c10-8f84e24b475d`（`+6.25pt`）
- c04 independent mean delta `−15.625pt`、worst block `−21.875pt`、opponent×seat gap `50.0pt`
- c10 independent mean delta `+1.5625pt`、worst block `0pt`、seat gap `3.125pt`、opponent×seat gap `75.0pt`
- elite selection: `independent_reeval_x2_positive_delta_gate_preserve_center`
- result: P1 center保持。promotion候補 `0`件。
- campaign manifest SHA: `d4b199fb28f25de9aaddfcb66cb6b585add38187f007613077d6ab0624012766`
- generation manifest SHA: `d36329a1f489988b3cb5fd7ee9d268158df163fc10c51d4007f3cf93a581191f`
- results SHA: `733b4c2fc70382a2b3ccb08b41c6e74e250e6b527c85d91e4b3049990209ea65`
- screen summary SHA: `b55a48e8d02073c51291206c72b5072f0c531509eac2f452deef67459eeb8458`
- re-evaluation summary SHA: `bd8a53df883087e00706ae1469488d3c601b86b4774bfd6dddf51b610b9a28b0`

## Promotion and next action

P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、BestKnown、Champion、production、submissionは不変である。DEV／FINALは未使用のまま保持した。

同一Rocket sourceのblind retryは行わず、次はsource commit／deck／family相関を下げた許可済みsnapshot、または複数runtime-safe familyを混合した別compositionをsealする。通過候補だけを `cg_bestknown_loop_v1.py` へ渡す。

## Verification

- `TMPDIR=/tmp PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:src pytest -q tests/test_rocket_dispatch_classifier_meta_v1.py`: `7 passed`
- 全12 policy `py_compile`: PASS
- sealed pool loader／split verification: PASS
- `python scripts/docs/validate_docs.py`: `Validated 13 canonical documents.`
- `git diff --check`: PASS
- heavy CABT process: 完了、active processなし
- commit／push／Champion変更／Kaggle提出: 未実行
