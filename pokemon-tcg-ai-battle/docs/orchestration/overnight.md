# Overnight MVP v0.1

Overnight runner は、root worktreeを変更せず、task専用worktreeで実装・修正し、clean verificationとread-only reviewを通過したLOW risk patchだけをsession branchへローカルcommitします。push、通常branchへのmerge、Kaggle submissionは行いません。

```bash
/usr/bin/python3 scripts/orchestrate.py overnight --plan examples/overnight-plan.example.json
/usr/bin/python3 scripts/orchestrate.py overnight --resume SESSION_ID
/usr/bin/python3 scripts/orchestrate.py overnight-status SESSION_ID --follow
/usr/bin/python3 scripts/orchestrate.py overnight-report SESSION_ID
```

## Planとrouting

version 1 planは未知フィールドを拒否し、session作成前にTaskContract、command policy、external-model authorization、依存関係、上限、model availabilityを検証します。rootの`routing`にeconomy/standard/deepのmodelとlow/medium/high effortを明示します。taskはtierを上書きできません。

- simple + low risk: economy
- normal: standard
- complex、algorithm、大差分、Control Plane: deep
- repair: 1 tier昇格
- review: implementationとは別decision・別context

modelが利用不能ならfallbackせず停止します。実Providerには選択tierのmodel/effortを渡します。

## Isolationとresume

session integration worktreeは`.orchestrator/overnight/worktrees/<session-id>/integration`、task workerは`tasks/<task-id>/implementation`以下です。Providerはintegration worktreeを直接編集しません。task開始時のsession HEADとtree digestを保存し、clean verificationも同じHEADから作ります。前taskが統合された場合、次taskは更新後HEADを使います。

`plan.snapshot.json`、TaskContract、patch、verification、review、commitのdigestと段階checkpointを検証してresumeします。結果不明のProvider/reviewerは再実行せず`WAITING_HUMAN`にします。commit後crashはparent/tree identityから復元し、二重commitしません。

## Budget、risk、report

Provider、repair、review、verification、integration、commit直前にelapsedとbudgetを再確認します。token usageが得られない場合は推測せず`unknown`とprovider call、prompt byte、elapsedのproxyを保存します。

allowed/forbidden/protected glob、binary、symlink、submodule、大量削除、dependency/lock、Git/CI、authorization/secret、Kaggle、Kernel/Provider/process/integration/policy/eventsは決定論的に自動統合禁止です。さらにplanとTaskContractの明示許可、verification PASS、review PASS/LOW、diff行数制限、clean worktree、HEAD一致が必要です。

state、event、morning reportにはprompt、argv、stdout/stderr、environment value、patch本文、authorization本文を保存しません。`overnight-status --follow`はcurrent stateを1件表示した後、新規sanitized eventだけを表示します。
