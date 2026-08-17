# Bootstrap Kernel クイックスタート

Bootstrap Kernel は、明示的な `TaskContract` を 1 件ずつ隔離して実装し、別の clean verification worktree で検証した後、人間の承認によってのみベースラインへ patch を適用する最小の Control Plane です。万能な複数 Agent 基盤や自動提出のための仕組みではありません。正典と設計上の位置付けは [Bootstrap Kernel 実装計画](bootstrap_kernel_implementation_plan.md) を参照してください。

## 事前確認

ローカル前提条件を JSON で確認します。

```bash
python scripts/orchestrate.py doctor
```

`doctor` は Python、Git repository、builtin Fake Provider、Codex Provider の利用可否と固定 security configuration、外部モデル認可、環境ポリシー、Control Plane 用ディレクトリの書込み可否を報告します。実装 run や統合は開始しません。

## TaskContract

`TaskContract` は JSON object です。現在の schema の field は次のとおりです。

```text
task_id
role
base_snapshot_id
read_paths
allowed_paths
forbidden_paths
protected_paths
expected_outputs
verification_commands
acceptance_digest
command_policy
environment_allowlist
resource_budget
provider
external_model
```

`task_id` は空でない文字列、`allowed_paths` は空でない文字列配列です。`verification_commands` は shell 文字列ではなく、`["python", "-m", "py_compile", "path/to/file.py"]` のような非空の argv 配列の配列です。`provider.type` は `fake` または `codex` を指定します。`codex` を使う場合は `provider.prompt` が必要で、外部モデル認可も検証されます。

`allowed_paths` は worker が変更できる repository 相対 path または pattern の集合です。変更がこの集合外なら run は停止します。`protected_paths` は変更を許可しない集合であり、`allowed_paths` に含めても変更は拒否されます。`forbidden_paths` も変更拒否に使います。読み取り対象は `read_paths` に明示します。絶対 path、`..`、`.git` 配下は使えません。

安全な Fake Provider の完全例は [task_contract.example.json](../../../examples/orchestration/task_contract.example.json) です。この例は `examples/orchestration/generated_hello.py` だけを生成し、`python -m py_compile` を verification command に指定します。

## 実行と状態確認

```bash
python scripts/orchestrate.py start --contract examples/orchestration/task_contract.example.json
python scripts/orchestrate.py status RUN_ID
python scripts/orchestrate.py status
python scripts/orchestrate.py resume RUN_ID
```

`start --contract` の出力 JSON から `run_id` を控えます。`status RUN_ID` はその run、引数なしの `status` は既存 run の一覧を返します。中断や `BLOCKED` 後には `resume RUN_ID` で再開できます。すでに `WAITING_INTEGRATION_APPROVAL` または終端状態の run を resume しても、その状態を返します。

正常な R1 run は `INTAKE`、`IMPLEMENTATION`、`VERIFICATION_DETERMINISTIC` を経て `WAITING_INTEGRATION_APPROVAL` で停止します。この状態は、worker の自己申告ではなく authoritative verification が通ったことを示しますが、まだベースラインには変更を適用していません。

## worktree と統合承認

`start` 時に WorkspaceSnapshot が保存されます。worker は snapshot から materialize した worker worktree で動作し、Control Plane がその patch を capture します。続いて、同じ snapshot から別に作る clean verification worktree へ patch を適用し、protected path の hash と `verification_commands` を検証します。worker の cache、virtualenv、shell state、`.env`、patch に含まれない一時ファイルは verification worktree へ持ち込みません。

人間が patch と authoritative evidence を確認し、統合してよいと判断した場合だけ、次を実行します。

```bash
python scripts/orchestrate.py approve RUN_ID integration
```

これは承認を記録し、snapshot 時点から `allowed_paths` が変わっていないことを確認して patch をベースラインへ適用します。統合しない場合は理由を付けて拒否できます。

```bash
python scripts/orchestrate.py reject RUN_ID integration --reason "review findings require revision"
```

Bootstrap Kernel は commit、push、tag を自動実行しません。これらの Git 操作は、統合後に人間が必要なレビューと判断を終えてから別途行います。
