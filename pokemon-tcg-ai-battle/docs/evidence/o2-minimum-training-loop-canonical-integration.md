---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-07-19
---

# O2 Minimum Training Loop canonical integration

## 結論

O2 Minimum Training Loop は、最新正典 `feature/belief-guided-search` を基点に専用worktreeへ意味的に統合した。O2 → C4 ActualEpisodeLineageInput → 実cabt collection → private binding → actual-only dataset → training/package の接続、legacy collector互換、fixture/actual境界、seat-swap leakage guardを維持している。Champion/defaultはRule Agent v0、Student promotionは `INSUFFICIENT_EVIDENCE` のままである。Kaggle提出は行っていない。

## provenance

| 項目 | 値 |
|---|---|
| integration worktree | `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o2-integration` |
| integration branch | `integration/o2-minimum-training-loop-v1` |
| canonical branch | `feature/belief-guided-search` |
| canonical base HEAD | `fc3ba1f17cbb8e0d9eac4efe3edc018647733a03` |
| O2 source branch | `feature/o2-minimum-training-loop` |
| O2 source HEAD | `599a19e0eab50a787303893fc2c65b3ac29b2ed7` |
| merge base | `fc3ba1f17cbb8e0d9eac4efe3edc018647733a03` |
| merge commit | `a6b137a7b5776e824e96e3d18c7ed86917978d50` |
| O2 commit range | `fc3ba1f..599a19e`（11 commits） |
| conflicts | なし |

O2の統合対象は、Pool contract／deterministic match plan／fixture contract／real cabt adapter／C4 bridge／ActualEpisodeLineageInput／collector後方互換拡張／`source_plan_hash`／pair-aware split／actual pipeline／tests／evidenceである。無条件の `ours`／`theirs` 解消、履歴書換え、既存worktree変更、force pushは行っていない。

## 維持した契約

- `main.py`、`deck.csv`、submission entrypoint、Rule Agent v0のdefault選択、Promotion Gateの閾値と判定ロジックは変更していない。
- `collect_actual_dataset(..., episode_lineage_inputs=None, ...)` の既定呼び出しは従来のself-play契約を維持する。O2 lineageは明示入力時だけ有効で、fixture結果をactual evidenceへ昇格できない。
- private bindingのschemaは複製せず、O2のseat／agent・implementation hash／deck hash／pair_idはprivate bindingへ伝播する。public manifest/summaryはmatch ID／plan hash等の識別子とdescriptorだけを保持し、private payloadを含めない。
- `engine_seed_supported=false`。決定的なのはmatch plan／seat scheduleであり、engine outcomeのseed再現は主張しない。評価モードは `seat_matched_unseeded`、`exact_paired_inference=false`、`promotion_eligible=false` である。

## feature evidence（既存artifactの照合）

O2 feature branchにコミット済みの実測evidence（[O2 evidence](o2-minimum-training-loop-v0.md)）をcanonical integrationから参照した。run-scoped artifact実体はgit管理外で、このclean integration worktreeには存在しないため、以下はevidence記載値の照合であり、artifactを再生成したという主張ではない。

| 項目 | 記録値 |
|---|---:|
| actual collection | 40 matches / 40 episodes |
| supervised decisions | 931 |
| candidates | 5,387 |
| private bindings | 931 |
| duplicate / mismatch / fixture contamination / privacy violation | 0 / 0 / 0 / 0 |
| split | train 20 / validation 10 / test 10; seat-swap pair overlap 0 |
| collector dataset hash | `f4529f74b3cb6b2a5d7166d0d367b0cc6afd42a8fd04ec9b50a545c63e96114e` |
| offline dataset hash | `bf9960f11031762da6b98b9dd470319197bf3c8cffda67c312773b9a018bc91b` |
| artifact class | `NEURAL_ACTUAL_TRAINED` |
| model hash | `d503db29cdcd2833d02b771e9ecd9555573b240aea44c7a4d2ebc06dbbf407f1` |
| package archive SHA-256 | `d358fc035a4778fe74ab98ef725b7903649b71bdcaa3e44f8fd51229169a6d54` |
| package clean-room | 8/8 legal、illegal 0、exception 0、verified=true |
| evaluation | 16/16 `DONE`; invalid/crash/timeout/privacy violation 0 |
| Promotion | `INSUFFICIENT_EVIDENCE` |

このfeature evidenceは統合後の昇格根拠ではない。100 pairのexact paired evaluationが未実施であり、StudentをChampion/defaultへ変更していない。

## canonical integration smoke

一時出力は `/tmp/pokemon-tcg-o2-canonical-integration/` に保存した。

- `python scripts/run_actual_league.py --challenger deterministic --games 2 --base-seed 9300 ...`：official `kaggle_environments.make("cabt")`、2/2 `DONE`、invalid/crash/timeout/fallback/privacy violation = 0、seat 0/1を各1試合、`engine_seed_supported=false`。
- O2→C4 bridge（seeds 9300/9301）：4 episodes、73 supervised decisions、433 candidates、73 private bindings、duplicate decisions 0、privacy violations 0、全private lineageのbackend `cabt`、engine seed false。
- `python scripts/validate_c4_dataset.py --run-dir /tmp/.../o2-smoke`：`valid=true`、row/binding 73、candidate 433、episode 4、privacy scan executed、privacy violations 0。
- pair-aware split：2 pairそれぞれが単一splitに割り当てられ、pair overlap 0。public summaryにprivate payloadはなく、private bindingはdescriptorとprivate path roleで隔離される。

これは統合smokeであり、40-match artifact／16-game evaluation evidenceを再生成するものではない。

## 検証結果

### baseline

- canonical単体 `python -m pytest -q -p no:cacheprovider`：`1432 passed, 1 failed`。
- 失敗は `tests/test_collect_offline_training_v1_evidence.py::test_run_command_safe_timeout_and_child_cleanup`。タイムアウト直後のPID file raceで、O2変更とは無関係。
- 同nodeを単体で3回再実行し、3回とも同じ失敗を再現した。削除・skip・xfail化はしていない。
- docs validation：`Validated 12 canonical documents.`。protected-file baselineを保存し、merge後もdiff 0を確認した。

### post-merge

- focused O2/C4/offline/artifact/submission suite：`167 passed, 3 warnings`。
- full regression `python -m pytest -q -p no:cacheprovider`：`1470 passed, 1 failed, 5 warnings`。失敗はbaselineと同じ既知flaky 1件だけで、新規O2関連失敗は0件。
- package：Rule Agent v0 artifact build/verify pass。clean-roomはdeck 60、mandatory `[0,1]`、illegal 0。生成archiveは `/tmp` に置き、tracked artifactは変更していない。
- `scripts/validate_c4_dataset.py`：pass。`scripts/check_offline_training_import_closure.py`：internal dependency closure complete（unresolvable表示は既存third-party importの警告）。
- `git diff --check`、staged diff check、変更ファイルのconflict marker check：pass。docs validator、pytest privacy/secret contract tests、submission compatibility tests：pass。

### clean clone

clean clone検証は、統合commitをpushする前のローカル一時cloneで実施する。clone後にfocused tests、docs validation、import、package validationを実行し、未追跡worktree依存がないことを確認する。cloneは `/tmp/pokemon-tcg-o2-clean-clone` に作成し、検証後に削除した（ローカルartifactは持ち込んでいない）。

## push gateと最終状態

証跡commit作成後、push直前に `git fetch origin --prune` して `origin/feature/belief-guided-search` が統合開始時の `fc3ba1f...` から動いていないことを再確認する。動いていた場合はpushせず、最新正典から統合をやり直す。force pushは使用しない。

期待する最終不変条件は次のとおりである。

```text
Champion/default = Rule Agent v0
Student artifact = NEURAL_ACTUAL_TRAINED evidence available
Promotion = INSUFFICIENT_EVIDENCE
engine_seed_supported = false
pairing_mode = seat_matched_unseeded
exact_paired_inference = false
promotion_eligible = false
Kaggle submission = not performed
O2 feature branch = retained
```
