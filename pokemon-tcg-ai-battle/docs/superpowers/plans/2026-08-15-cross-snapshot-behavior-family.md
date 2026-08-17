# Cross-snapshot behavior-family meta 実装計画

## Task 1: fail-closed contract（TDD）

- [x] `tests/test_cross_snapshot_behavior_meta_v1.py`へ、4 base／3 source commit gate、family transform dispatch、重複base拒否、recipe/provenanceを先に追加する。
- [x] REDを確認する。

## Task 2: generatorとCLI

- [x] `src/mage_ptcg/opponent_ingest/cross_snapshot_behavior_meta_v1.py`を追加する。
- [x] `scripts/generate_cross_snapshot_behavior_meta_v1.py`を追加し、spec JSON、P1 package、current pool、scan roots、source epoch、seed namespaceを受ける。
- [x] 既存のpool／fresh／split helperとauthority境界を再利用する。

## Task 3: source poolとsmoke

- [x] k4内の異なるsource commitから、Alakazam単一transformとComfey factorial transformを組み合わせた4件specを作る。
- [x] fresh pool／split SHAとstatic findingsを確認する。
- [x] 新seedで両seat smokeを実行し、fault0以外は停止する。

## Task 4: CEMとfresh validation

- [x] P1 control固定、population8、elite2、2世代、独立再評価2回のCEMを実行する。
- [x] source family別lower-tailとseat gapを確認する。
- [x] robust gateを通過したcandidateだけMETA_DEV／META_FINALで確認する。失敗時はP1を保持する（今回は未通過のためDEV/FINAL確認なし）。

## Task 5: evidenceと検証

- [x] `docs/evidence/cg-cross-snapshot-behavior-meta-20260815.md`へlineage、SHA、seed、結果、判定を記録する。
- [x] current status、handoff、ChatGPT context packへ追記する。
- [x] focused pytest、py_compile、docs validator、`git diff --check`を実行する。
