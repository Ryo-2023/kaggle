# Kaggle Validation v2 独立レビュー（2026-07-18）

## 結論

二回のKaggle Validation Episode失敗と既存Safety Gateを、既存判定を前提にせずコード、実loader、同一tarball、Python 3.11.11で再監査した。CRITICAL／HIGH findingはすべて修正した。新archive `390dfcfb886bbdaacb6624a537d160444331f71bcae59b7028b4ea2716f32962` はPython 3.11.11、Kaggle Environments 1.32.0、source/cwd分離、archive-only G1〜G6をPASSしたため`LOCAL_SUBMISSION_READY`である。ただしKaggle platformへは再提出しておらず、`kaggle_validation_passed`はfalseである。full regressionは既知flaky 1件のため`FULL_REGRESSION_BLOCKED`とする。

## Review scope

- reviewed commits: `00baf71`、`a6d7bee`、`d7ea5d8053d405fbb1a576c866ec221498117005`、`4ff889a`
- reviewed code: packager、candidate verifier、submission wrapper、actual viability runner、root `main.py`、関連tests
- reviewed process: postmortem、runbook、status、handoff、旧verification evidence
- failed submissions: ref `54796979`（archive `d4e2cdcb…`）、ref `54798845`（archive `dd33517f…`）

## Independent findings

### CRITICAL（修正済み）

1. generated `main.py`がraw exec時にcwdをpackage root候補として採用し、sourceとcwdが分離するKaggle本番で失敗した。cwd候補を廃止し、source filenameと検証済みKaggle locationだけを必須memberで判定するfail-closed設計へ変更した。
2. verifierが呼出元のPythonを無条件に使い、`cwd_decoupled_verification: true`を実測せず固定値で出力していた。指定runtimeをprobeし、Python 3.11.x／Kaggle Environments 1.32.0以外を拒否し、実際に異なるdirectoryである場合だけtrueとするよう修正した。
3. smoke game数が0でもG6と`local_submission_ready`が成立し得た。正式Gateの最小試合数を20に固定した。

### HIGH（修正済み）

1. G6のmodule origin監査が`.venv`を許可rootとして扱い、archived `mage_ptcg`がvenv/workspaceから解決しても見逃す余地があった。また`mage_submission_agents`を監査していなかった。対象runtime moduleは必ずarchive root配下であることを強制した。
2. wrapperがPython version、Kaggle Environments version、`runtime_main.py` SHAを検証していなかった。全項目を必須化し、`kaggle_validation_passed`もpre-submitではfalseを強制した。
3. tar監査に総展開サイズ、member数、特殊file拒否がなかった。compressed/individual/total/member count制限を追加し、regular fileだけを手動展開するよう変更した。
4. repository root `main.py`もraw exec時に`__file__`を参照していた。source `co_filename`からrootを解決し、別cwd raw exec回帰を追加した。

### MEDIUM

1. Python-level `open` hookはnative extensionによる全I/Oを完全捕捉するものではない。`-I`、隔離temp layout、archive module origin強制、poison fixtureを併用して境界を検証した。
2. upstream `get_last_callable`は実行pathを`sys.path`へappendする。各Gateを隔離subprocessで実行し、workspace pathを渡さないことで影響を限定した。
3. full regressionは既知のprocess-start race 1件を再現し、全件PASSではない。

### LOW（修正済み）

1. viability privacy testが裸の文字列`700`を禁止し、commit SHAとの偶然一致で誤検知した。秘密値のJSON field contextを検査するoracleへ精密化した。

## Root cause

- 一回目: `__file__`未定義の`exec(code_object, env)`を通常importで代用し、generated entrypointが`NameError`となった。
- 二回目: `__file__`不在時の`Path.cwd()` fallbackを採用したが、本番sourceは`/kaggle_simulations/agent/main.py`、cwdは`/kaggle/working`だった。
- Gate failure: archive展開rootをcwdとして実行し、誤ったcwd fallbackが必ず成功するfixtureになっていた。
- systemic cause: loader、Python minor、directory topology、artifact boundaryを独立した機械的postconditionへ落とさず、ローカル成功をplatform互換性へ過剰一般化した。

## Entrypoint実験

Kaggle Environments 1.32.0の実装を確認し、`get_last_callable(raw, path=...)`が`compile(raw, path, "exec")`後に空のenvでexecすることを確認した。module frameの`sys._getframe().f_code.co_filename`はcompile時のabsolute source pathを保持し、`__file__`なし、別cwdでもarchive rootを解決した。

採用候補は`__file__`、module frame `co_filename`、`/kaggle_simulations/agent`である。cwd、任意の`sys.path`、candidate sidecarはroot候補にしない。`main.py`、`runtime_main.py`、`deck.csv`、model、submission helper、neural runtimeの存在を確認し、解決不能時はchecked candidatesを含めてfail closedにする。

REDはcwd sidecarの誤採用とroot `main.py`の`__file__` NameErrorを再現した。GREENでは指定8 root-resolution tests、実`get_last_callable`、Python 3.11.11 subprocessがPASSした。

## Candidate

- path: `runs/offline-training-v1/offline-long-run-actual-20260718-r1/submission_candidate/neural-student-v1-entryfix-v2/`
- archive SHA-256: `390dfcfb886bbdaacb6624a537d160444331f71bcae59b7028b4ea2716f32962`
- archive size: 631,369 bytes
- entrypoint SHA-256: `c01a188034658735b38d1c51a1d375046b5d9f3e5f38f442269d9ab606b9c8bb`
- runtime_main SHA-256: `777da2527e1c513c54b5abb799c71458af36d7898db6016b8e7baa6ca072ad36`
- model SHA-256: `2318b7ff7f1d981ec4181ae01cc681b15c057f5b4daba3d5f900e71e2144eb8f`（不変）
- semantic model hash: `94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4`（不変）
- build commit: `d7ea5d8053d405fbb1a576c866ec221498117005`
- members: 18

直前のlocal candidate `e90546ab…`から変わったmemberは`main.py`と`runtime_main.py`だけである。Kaggleでrejectされた`dd33517f…`との比較ではsubmission helper namespace、`main.py`、`runtime_main.py`、既存dataset source revisionが異なる。モデルbytesと学習結果は変更していない。

## Gate／wrapper

Python 3.11.11、Kaggle Environments 1.32.0、seed 35000で同一archiveを検証した。

- G1〜G4: PASS
- G5: PASS、60 steps、`DONE/DONE`
- G6: PASS、20 games、12–8–0、crash 0、invalid 0、timeout 0
- fallback telemetry: AVAILABLE、selected 855、fallback reasons空
- archive-only: true
- cwd-decoupled: true
- external files read: `[]`

wrapper dry-runはcanonical branch、clean worktree、archive/entrypoint/runtime/model/verifier SHA、verification commit ancestor、environment fieldsを検証してPASSした。`--execute`とKaggle submissionは使用していない。

## Tests

- focused exact command: 93 passed、9 warnings、104.53秒
- root Rule Agent suite: 20 passed、3 warnings、7.06秒
- full regression final: 1 failed、1068 passed、5 warnings、219.57秒
- failed test: `tests/test_collect_offline_training_v1_evidence.py::test_run_command_safe_timeout_and_child_cleanup`
- JUnit: `/tmp/kaggle-validation-v2-full-pytest.xml`
- full regression verdict: `FULL_REGRESSION_BLOCKED`

## Final verdict

`PASS_WITH_NOTES`。新archiveは`LOCAL_SUBMISSION_READY`だが、Kaggle platform validationは未実施である。Champion/defaultはRule Agent v0、Promotionは`NO_DECISION`、model bytes不変、追加学習なし、Kaggle再提出なし。
