# Kaggle public kernel meta intake v7–v9 / source-generation checkpoint（2026-08-15）

## 結論

公開 Kaggle kernel を `local_eval_only` の CABT opponent source として取得・隔離する経路は、v7–v9で継続利用できる状態になった。ただし、今回の batch から BestKnown を更新する candidate は得られていない。v8 CEMは fault 0 で完走したが、独立 positive／seat-safe gateを満たさず P1 center を保持した。次の最優先は同じsourceのblind retryではなく、**新しい meta source を獲得・生成する recipe そのもの**の設計である。

判定は `SOURCE_INTAKE_PASS / PERFORMANCE_PROMOTION_FAIL / NEXT_SOURCE_RECIPE_REQUIRED`。P1、root deck、BestKnown、Champion、production、submission、commit、pushは不変である。

## 0. intake の静的合法性修正

v7のruntime smokeで、Sushanth Mega Emboar sourceが `Energy Search Pro`（card ID `1100`）を4枚含み、engine `errorType=4` の `AGENT_INVALID` になった。従来の `exact 60 + official IDs` だけではACE SPEC枚数を検出できなかった。

`src/mage_ptcg/opponent_ingest/kaggle_kernel_meta_v1.py` にローカル `data/raw/EN_Card_Data.csv` の `Rule=ACE SPEC` を読む静的ゲートを追加した。catalogが利用できる場合はデッキ中のACE SPEC枚数を正確に1枚に固定し、`invalid_ace_spec_count` でfail-closedする。evidenceへ `ace_spec_card_ids`、`ace_spec_count`、`ace_spec_validation` を記録する。catalogがない環境で推測拒否を避けるため、検証不能時は `UNAVAILABLE` として従来のexact-60ゲートを維持する。

回帰テスト `tests/test_kaggle_kernel_meta_v1.py` は11 passed。これにより同じv7のEmboar型sourceは、CABT起動前に拒否される。

## 1. v7 — 新規1 sourceは再利用可能、もう1 sourceはruntime quarantine

configは `configs/meta_specialist/cg_kaggle_kernel_meta_v7.json`、intake rootは
`runs/cg-kaggle-kernel-meta-intake-v7-20260815/`。

- static accepted: `kaggle_raunak_advanced_heuristic_20260815`、`kaggle_sushanth_mega_emboar_20260815`
- static rejected: Pllinas stable baseline（`source_identity_reused`）、Sushanth Zacian（`invalid_deck`／`'Card'`）
- smoke: 4局中 Raunak 2/2 `DONE`、Mega Emboar 2/2 `AGENT_INVALID`、fault 2
- Raunakのみを部分昇格し、root `runs/cg-kaggle-kernel-meta-promoted-v7-raunak-20260815/` を作成
- partial promoted pool SHA: `20d52d4833edd4325aaf25f9a4ba1da256777c37128c3eefee143b6323e62d02`
- partial promoted fresh SHA: `52052e437dddb16fe6f1a2dd62cf739c0bd1eb751771f8173c08f36bb135ab3a`
- partial smoke summary SHA: `69a9a58d10ef917503c6b641a767393c056fff41bf28d128e2327059d35b7f04`

部分昇格 helperは、全体summaryが `FAULT` でも、ledger上で指定した referenceが全行 `DONE` なら、そのsubsetだけをsealed poolへ出力する。元のfault sourceは削除せず、入力rootは不変である。

## 2. v8 — 3 sourceをruntime-safeに接続したがCEMはno-update

configは `configs/meta_specialist/cg_kaggle_kernel_meta_v8.json`。static acceptedは次の3件。

- `kaggle_faheem_dragapult_ucb1_20260815`
- `kaggle_prvsiyan_alakazam_v10_20260815`
- `kaggle_prvsiyan_alakazam_v9_20260815`

Greninja、Sushanth Zacian／Emboar等はinvalid deck、Jazivxt variantsはsource identity reuse、llccqqはdynamic importで拒否した。v8 smokeは6/6 `DONE`・fault 0。promoted pool SHAは `f85b7eed8b0ac7a8a7dbac1ab4137e9ec63fe0fa1d872c2e8b7a73bc9f25b378`、fresh SHAは `68489a3d3b01a282d8209a6120e25727beaaff35d2008fc3c20967fcc3a010b4`。

splitは `runs/cg-kaggle-kernel-meta-promoted-v8-20260815/cg_historical_split.json`、SHA `2fd0aa63bc5525d464da4a3b49a8d1d1756c3f6c46b208696385afb2050a2906`。構成は TRAIN=Faheem Dragapult、DEV=Prvsiyan Alakazam v10、FINAL=Prvsiyan Alakazam v9。

`runs/cg-kaggle-kernel-meta-cem-v8b-20260815/` は同じP1 source packageで2世代、population 8／elite 2／独立re-evaluation 2回を実行した。screen 144局、independent 48局、DEV/FINAL診断32局、合計224局は全て `DONE`・fault 0。

- generation 0: valid screen candidate 1件だが elite 2件に不足し、center保持
- generation 1: risk-aware independent gateでpositive candidate数不足、`incumbent-center`を2件置いてcenter保持
- 新center、P1 policy、root deck、Championはいずれも不変
- `manifest.json`: `COMPLETE`、`champion_changed=false`、`submission_sent=false`

v8bの `--include-dev-refs` 実行では、Prvsiyan v9を32局の最終診断へ投入した。この結果は選抜に使っていないが、v8 campaignに対する未使用FINALではない。次のcampaignで未使用holdoutと呼ばない。

## 3. v9 — public discoveryはsource identity枯渇を確認

configは `configs/meta_specialist/cg_kaggle_kernel_meta_v9.json`。9 tarを調査し、static acceptedは `kaggle_prvsiyan_control_v11_20260815` の1件だけだった。smokeは2/2 `DONE`・fault 0、promoted pool SHAは `496be2170654b736d6213c34a39787fce7232a6bda612672c3532fe67d205392`、fresh SHAは `cc35ba1bf22fbb1fade3851349d39a1d9ff639cf3857aa7e90e8ccf4e7e3e646`。

残りは、source identity／artifact identity reuse、missing agent entrypoint、dynamic executionまたは既存sourceと同一で拒否された。v7 Raunak、v8 3件、v9 controlを別rootへmergeした5-reference poolは `runs/cg-kaggle-kernel-meta-merged-v789-20260815/`、pool SHA `4eda03128677938a1c21f226bf6edd003416a07fac8bef10be60c1e5d20bdd29` である。

ただし v8の3件はすでにv8 CEM／診断へ投入済み、v7 Raunakとv9 controlはsmokeのみである。したがってこの5件をそのまま「全て未使用」として新CEMへ渡してはならない。

## 4. source獲得・生成の次段階

これまでに次の生成面は実装・接続済みだが、P1 promotionには至っていない。

1. internal first-parent / historical snapshot
2. visible-state behavior-family / factorial / cross-snapshot / stratified transform
3. Rocket theta／route／classifier／confidence transform
4. runtime-safe Water Box transform
5. public Kaggle kernel tar intake

共通する失敗は「screenの正差が独立blockで反転する」「opponent×seatのlower-tailが0またはseat gap>5%」「source identityが実際には同一lineage」である。従って、次のrecipeは単なるpriority/tableの再配置ではなく、次を満たす必要がある。

- 新しい source provenance（別kernel snapshot、別commitまたは別deck）を最小1つ確保する。
- source transformを適用する前に、deck legality（ACE SPECを含む）、static安全性、実行時間上限を検証する。
- TRAIN-only smokeを先に行い、DEV／FINALをsmokeへ混ぜない。
- CEMのsource poolは少なくとも2 family、各seatを十分に含み、source/opponent×seat lower-tailを推定できる数にする。
- CEMで使ったreference、seed、policy SHA、deck SHAを新campaignのfreshnessから除外する。
- candidateは `fault0 → independent positive → seat-safe → opponent×seat-safe → 未使用DEV → 未使用FINAL` の順でのみ `cg_bestknown_loop_v1.py` へ接続する。

次に実装・検証すべき第一候補は、P1 CABTの公開状態／terminal outcomeから観測されたhard-negative stateを抽出し、private情報を使わない bounded adversarial policy adapterへ変換する **failure-conditioned self-owned source generator** である。これは過去policyの同一table再配置ではなく、P1の失敗状態に対するactor-visibleな対戦相手圧力を新しいrecipeとして生成する。ただし、generatorの仕様・lineage・未使用holdoutを先にsealし、CABT outcomeを直接expert labelとして学習しない。

新source recipeが通るまで、P1→policy CEM→fresh validation→deck→policyのheavy loopは再開しない。既存v789 poolを盲目的に再実行せず、source recipeの独立性を先に証明する。

## 5. 一次artifactと検証

- source intake tests: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q --capture=no tests/test_kaggle_kernel_meta_v1.py` → 11 passed
- promotion subset tests: `tests/test_promote_historical_meta_smoke_v1.py` → 3 passed
- CEM regression: `tests/meta_specialist/test_run_cg_p1_cem_v1.py tests/meta_specialist/test_cg_p1_cem_v1.py` → 30 passed
- docs／py_compile／diff-checkは、今回の文書更新後に再実行する。
- active heavy processなし。commit、push、Champion変更、Kaggle提出なし。
