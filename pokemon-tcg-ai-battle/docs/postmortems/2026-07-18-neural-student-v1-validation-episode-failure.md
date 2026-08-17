# Postmortem: Neural Student v1 Validation Episode Failure

## 1. Incident Summary

* **発生日**: 2026-07-18
* **Kaggle 提出リファレンス (Submission Ref)**: `54796979`
* **失格ステージ (Failure Stage)**: `Validation Episode`
* **ステータス (Status)**: `ERROR`
* **スコア (Score)**: 未計測 (0.000)
* **概要**: 提出パッケージの `main.py` が初期ロード時に `NameError` を起こしたため、モデルの推論やゲーム状態、フォールバック等への到達前に失格となりました。Agent 0、Agent 1 ともに Step 0 の直後 (約 0.004 秒以内) に全く同一の箇所で失敗しました。

## 2. Impact

* Kaggleのデイリー提出枠を 1 回無駄に消費した。
* Leaderboard での評価やスコア計測が一切行われなかった。
* 提出のデバッグと再検証のためにユーザーの時間を浪費させた。
* 「提出可能である」と判断したエージェントの検証プロセスの信用を損ねた。
* **過去にも提出ミスに対する検証体制の再発防止策を指示されていたにもかかわらず、再び同様のプラットフォーム互換性バグによる提出失敗を発生させてしまった。**

## 3. Direct Cause

Kaggle Environments 1.32.0 では、提出された `main.py` を通常の import ではなく、コンパイルしたコードオブジェクトに対し `exec(code_object, env)` を用いてグローバル実行します。この実行用環境 `env` にはグローバル変数 `__file__` が定義されていません。
しかし、自動生成された `main.py` は、絶対ルートを解決するために無条件で `__file__` を参照していました。

### 実際のトレースバック (Traceback)
```text
File "/kaggle_simulations/agent/main.py", line 13, in <module>
    _ROOT = Path(__file__).resolve().parent
NameError: name '__file__' is not defined
```

## 4. Root Cause (工程上の根本原因)

1. **検証環境と本番実行環境の不一致 (Clean-room Import vs Raw Exec)**:
   ローカルの `clean-room` 検証では、パッケージされた `main.py` を `importlib` や通常の `import` 経由で読み込んでテストしていました。この通常のインポート経路では Python ランタイムが `__file__` をグローバルに設定するため、バグを検知できませんでした。
2. **検証ツールでの Kaggle 実経路の不使用**:
   Kaggle のエージェントロード実関数である `kaggle_environments.agent.get_last_callable()` をローカル検証で使っていませんでした。
3. **Validation Episode の不完全なローカル再現**:
   Kaggle Environments を使った Step 0 / Step 1 への前進テストを `verify_kaggle_submission.py` などのローカルプレフライトで実際に実行していませんでした。
4. **不適切な安全宣言と過大表現**:
   「提出相当ランタイム」「安全に提出可能」「PREFLIGHT READY」といった過剰な自信に基づく表現を、プラットフォーム互換性を機械的に検証し終える前にドキュメントに記述してしまっていました。
5. **記憶や文書に頼るチェックリストの限界**:
   「`__file__` の使用に注意する」などの反省をドキュメント上のチェックリストに書き留めるだけで終わらせ、**それが自動テストとしてコードで強制的に検証される仕組み（Gate）にまで落とし込んでいませんでした。**
6. **アーティファクト監査と提出の分離欠如**:
   検証した tarball の SHA-256 と、実際に提出された tarball の SHA-256 が一致していることを機械的に紐付ける仕組みがなく、ユーザーが手動で生の `kaggle competitions submit` コマンドを実行できる状態のまま放置されていました。

## 5. Why Existing Checks Missed It

| 旧検証 (Check Name) | 実際に確認したこと | 確認していなかったこと (検出できなかったこと) |
|---|---|---|
| deterministic build | ビルドされるパッケージのバイナリ同一性 | Kaggle エミュレーター内でのロード・実行互換性 |
| local clean-room | 展開先での import 成功と依存 closure の充足 | `__file__` がグローバル空間に存在しない場合の raw exec |
| policy identity | Rawモデルとパッケージモデルでの出力同一性 | エントリポイント `main.py` 自体の起動とルート解決 |
| viability evaluation | 専用 evaluation adapter 経由での対戦動作 | `main.py` の生の source コードからのロードと実行 |
| 20-game smoke | ローカル評価器を流用した 20 試合完走 | 本番の `kaggle_environments` Validation エミュレーション |
| candidate freeze | モデルバイトサイズおよび SHA-256 の固定 | Kaggle 本番ローダー `get_last_callable()` の通過性 |

## 6. Resolution (実施済みの対処)

* **コード修正**: `_MAIN_TEMPLATE` を修正し、`__file__` が未定義のときは `Path.cwd().resolve()` へフォールバックし、かつ `runtime_main.py` などの必須ファイル存在を確認する fail-fast チェックを追加。
* **回帰テスト追加**: `tests/test_offline_training_v1.py` に `test_raw_exec_without_file` と `test_kaggle_get_last_callable` を追加。修正前に RED（ NameError ）、修正後に GREEN になることを実証。
* **ローカル Validation再現**: `kaggle_environments.make("cabt")` を使用して Step 0 の初期登録から全 215 ステップを正常完走することを確認。
* **新たな候補の再ビルド**:
  * 出力先: `runs/offline-training-v1/offline-long-run-actual-20260718-r1/submission_candidate/neural-student-v1-entryfix/`
  * アーカイブ SHA-256: `dd33517fb7758fc671b27cfe672a0367835761061f2121747430e084895178d8`
  * モデルのバイナリデータは旧候補から100%不変であることを確認。
* **G6 のアーカイブ完全独立化と再検証 (2026-07-18 追加監査)**:
  * 旧 verifier の G6 実行は、ワークスペースや候補ソース側の `manifest.json` に依存（コピーして使用）しており、完全な archive-only になっていませんでした。このため、一度目の `LOCAL_SUBMISSION_READY` 判定を撤回しました。
  * G6 の実行モデルを、外部の `run_actual_agent_viability.py` を呼ぶのではなく、展開されたアーカイブ内の `main.py` のみを用いて `kaggle_environments` を直接実行する完全な「archive-only」構成へと修正しました。
  * 実行時に `builtins.open` をフックして外部ファイルアクセスを検知・エラー化する検証を追加しました。
* **テスト実行状況の訂正**:
  * 前回セッションでの Full regression（全 pytest テストスイート）は、ROS orchestration 関連テストで遅延が発生したため途中で強制終了（kill）されており、未完了（focused tests のみ合格）であったことが後続の監査で判明しました。これに伴い、「all tests green / full regression passed」の記述を撤回します。
  * 今回の最終実行結果は `docs/evidence/kaggle-submission-safety-gate-final-review-20260718.md` に記録する。Full regression が未完了の場合は PASS と表記しない。

## 7. Corrective and Preventive Actions (恒久対策)

### 2026-07-18 二回目の Validation Episode 再発

再提出 ref `54798845` も Validation Episode で `ERROR` となり、二回目の提出枠を消費した。実際のトレースバックは次のとおりである。

```text
File "/kaggle_simulations/agent/main.py", line 24, in <module>
    raise RuntimeError(
RuntimeError: submission package root could not be resolved: /kaggle/working
```

本番は Python 3.11、Kaggle Environments 1.32.0、source `/kaggle_simulations/agent/main.py`、cwd `/kaggle/working`、`__file__` 未定義、`get_last_callable()` の `exec(code_object, env)` 経路だった。二回目の修正は `Path.cwd()` を package root と仮定したため、source location と cwd が分離する本番で失敗した。

工程上の原因は、旧 Safety Gate が archive 展開 root をcwdとして実行し、誤った `Path.cwd()` fallbackが必ず成功する条件へGate自体を過適合させたことである。一回目の再発防止策はloader経路だけに注目し、Python minor version、source/cwdの独立性、archive外module originを機械的なpostconditionにしていなかった。このため旧 `LOCAL_SUBMISSION_READY` 判定は誤りであり、再発防止策が再び不十分だった。

恒久対策として、generated entrypointはcwdをroot候補から除外し、`__file__`、module frameの `co_filename`、検証済み `/kaggle_simulations/agent` の順に必須memberを確認してfail closedにした。G3〜G6は `<tmp>/kaggle_simulations/agent` と `<tmp>/kaggle/working` を分離し、Python 3.11とKaggle Environments 1.32.0を実測・強制する。新archive SHAは `390dfcfb886bbdaacb6624a537d160444331f71bcae59b7028b4ea2716f32962`、model SHAは不変である。Kaggleへの再提出は実行していない。

| No. | アクション (Action) | 実装場所 (Implementation Path) | 担当 (Owner) | ステータス (Status) | 検証方法 (Verification) | 再発防止メカニズム (Recurrence Prevention) |
|---|---|---|---|---|---|---|
| 1 | **機械的検証スクリプトの追加** | `scripts/verify_kaggle_submission_candidate.py` | Antigravity | **DONE** (本セッションで実装、監査修正完了) | テストスイートによる異常アーカイブの拒否検証 | 検証された SHA のみ `submission_verification.json` を発行する。アーカイブ完全独立（G6フックによる外部ファイル検知付き）。 |
| 2 | **提出ラッパースクリプトの追加** | `scripts/submit_verified_kaggle_candidate.py` | Antigravity | **DONE** (本セッションで実装) | テストスイートによる未検証・改変アーカイブの提出拒否検証 | json 記載のハッシュ、実際のアーカイブのハッシュ、`--confirm-sha` が 3 者完全一致しない限り Kaggle CLI を呼び出さない。 |
| 3 | **本番 Loader & Raw Exec の回帰テスト** | `tests/test_offline_training_v1.py` | Antigravity | **DONE** | pytest 実行 | 今後エントリポイントテンプレートが改変されて `__file__` への静的依存が再発した際に、即時ビルド前テストで検知する。 |
| 4 | **提出プロセスの Runbook 定義** | `docs/runbooks/kaggle-submission-safety-gate.md` | Antigravity | **DONE** (監査修正完了) | 文書レビュー | 手動の生 `kaggle submit` コマンドの使用を明示的に禁止し、ラッパー経由の提出フローを唯一の正典プロセスとする。 |

## 8. Lessons Learned (教訓)

> [!IMPORTANT]
> **人間の記憶や注意喚起は Gate ではない。**
> どんなに優秀な開発者であっても、手動のチェックリストや記憶に頼る検証は必ず漏れを生じる。
> 提出可能であるかどうかの判定は、提出対象と同一のアーティファクト SHA-256 に紐づく自動検証スクリプトによる完全な GREEN パスのみによって証明され、かつ専用のラッパースクリプト経由でのみ提出が実行される仕組み（強制システム）を構築しなければならない。
