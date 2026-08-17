# Runbook: Kaggle Submission Safety Gate

本ドキュメントは、Kaggle への提出プロセスの安全性とプラットフォーム互換性を機械的に担保するための正典ランブックです。
記憶や手動のチェックリストに依存した提出を廃止し、自動化された検証ゲートと専用の提出ラッパー経由のみでの提出を義務付けます。

---

## 1. 提出ライフサイクルにおける5つの状態の定義

エージェントおよびモデルの状態を混同せず、以下の4つの状態名を用いて明確に区別します。

### 1. MODEL_PROMOTION_CANDIDATE
* **定義**: 独立評価で統計的有意な性能向上が実証され、昇格（Promotion）の候補として認められたモデル状態。
* **判定条件**: 1000試合以上の paired 対戦等で、現 Champion に対する性能優位が証明されたこと。
* **注意**: この時点ではまだ提出パッケージの作成やプラットフォーム互換性の検証は完了していません。

### 2. LOCAL_SUBMISSION_CANDIDATE
* **定義**: 提出用パッケージ（`submission.tar.gz`）がビルドされた直後の状態。
* **判定条件**: `package.build_package()` により tarball が出力されたこと。
* **注意**: 提出可否は未判定であり、この状態のまま提出を実行することは固く禁止されます。

### 3. LOCAL_SUBMISSION_READY
* **定義**: 同一の tarball に対し、ローカルの自動検証ゲート（G1〜G7）がすべて PASS した状態。
* **判定条件**: 自動検証スクリプト `scripts/verify_kaggle_submission_candidate.py` が非ゼロ終了することなく完了し、`local_submission_ready: true` が書き込まれた検証 JSON が生成されていること。
* **注意**: ローカルプレフライトを通過したのみであり、Kaggle 側の Validation を通過した状態（`KAGGLE_VALIDATION_PASSED`）と混同してはなりません。

### 4. KAGGLE_VALIDATION_PASSED
* **定義**: Kaggle プラットフォームへ実際に提出が行われ、プラットフォーム側の Validation Episode を正常にクリアし、アクティブな提出物として受理された状態。
* **判定条件**: Kaggle の My Submissions 画面でステータスが `Complete` となり、Validation スコアが計測されたこと。

### 5. REJECTED_KAGGLE_VALIDATION
* **定義**: ローカル判定にかかわらず、Kaggle platformのValidation Episodeで`ERROR`、`INVALID`、`TIMEOUT`となったartifact。
* **判定条件**: Kaggle Replayまたは提出画面で失敗を確認したこと。
* **注意**: 対応する旧verification JSONと`LOCAL_SUBMISSION_READY`は無効とし、同一SHAを再提出しない。

---

## 2. 検証ゲート (G1 〜 G8) の定義

Kaggleへの提出を承認するためには、同一のアーカイブファイル（`submission.tar.gz`）に対して、以下の全検証ゲートを機械的に通過する必要があります。

### G1: Archive Integrity (アーカイブ整合性)
* **目的**: アーカイブファイルが物理的に破損しておらず、安全であることを検証する。
* **検証内容**:
  * tarball の正常な展開。
  * 展開されるファイル名における Path Traversal 脆弱性や Symlink 不正使用の排除。
  * 必須メンバーファイル（`main.py`, `runtime_main.py`, `deck.csv`, `models/neural-student-v1.json` など）が揃っていること。
  * メンバーサイズ制限および重複メンバーの不在の確認。
  * 機密情報マーカー（秘密情報）の非混入スキャン。

### G2: Dependency Closure / Local Import (依存性閉包)
* **目的**: パッケージがローカルのワークスペースパス（`src/` や `runs/`）に依存せず、パッケージ内のコードのみで自己完結していることを保証する。
* **検証内容**:
  * リポジトリ外の一時ディレクトリに tarball を展開。
  * ランタイム環境の `sys.path` から現在のワークスペースパスを完全に排除した状態で、展開されたエージェントコードをインポート。
  * 外部への未定義のローカルモジュール参照がないこと。
  * モデルファイル（`models/neural-student-v1.json`）をアーカイブ内から正しく相対パスでロードできること。

### G3: Kaggle Raw Exec (Kaggle 環境エミュレーションロード)
* **目的**: Kaggle Environments がエージェントを `exec()` を用いてロードする際の環境を再現し、NameError などを防止する。
* **検証内容**:
  * 展開されたアーカイブから `main.py` の生ソーステキストをロード。
  * `__file__` および `__package__` をグローバル変数辞書（`env`）から完全に排除。
  * `exec(compile(main_source, str(extracted_root / "main.py"), "exec"), env)` を実行し、例外なしで成功すること。
  * Kaggle Environments 1.32.0の実 `get_last_callable(main_source, path=...)` を用いてcallableを取得すること。通常importで代用しない。
  * archive rootをcwdにした検証は禁止する。G3〜G6は `<tmp>/kaggle_simulations/agent` をsource、空の `<tmp>/kaggle/working` をcwdとして実行する。
  * Python 3.11.xとKaggle Environments 1.32.0をruntime probeで実測し、異なる場合はverification JSONを発行しない。

### G4: Initial Lifecycle (ステップ 0 動作検証)
* **目的**: プラットフォームでのゲーム開始時（Step 0）のデッキ登録契約をエミュレートし、エージェントが正常に反応するかをテストする。
* **検証内容**:
  * 実際の Kaggle Step 0 observation（以下）をロードした callable エージェントへ入力する。
    ```json
    {"current": null, "logs": [], "remainingOverageTime": 600, "search_begin_input": null, "select": null, "step": 0}
    ```
  * 例外を一切出さず、かつ正常に **60枚の整数リスト（デッキ登録アクション）** を返すこと。
  * ロード直後にモデル推論を不適切に呼び出さないこと（モデルロード処理が安全にバイパスまたは初期化されていること）。

### G5: Local Validation Episode (ローカル模擬対戦)
* **目的**: 本番の Kaggle 環境設定で 1 エピソードをエミュレートし、複数ステップの前進と正常終了を確認する。
* **検証内容**:
  * `kaggle_environments.make("cabt", configuration={...})` を用いて、本番の設定値（`actTimeout: 0`, `episodeSteps: 10000000`, `runTimeout: 2000`, `seed: 0`）でエミュレーションを実行する。
  * 生成された `main.py` のパスを Agent 0, Agent 1 双方に割り当てる。
  * エピソードを最後まで実行し、Agent 0 status, Agent 1 status ともに `DONE` となり、`ERROR`、`INVALID`、`TIMEOUT`が発生しないこと。Step 1到達だけではPASSにしない。

### G6: Artifact Runtime Smoke (実対戦動作確認 - アーカイブ完全独立)
* **目的**: モデルの推論ロジックを含め、実戦で非合法手（invalid action）や実行時エラーが発生しないかを検証する。
* **検証内容**:
  * 新規の未使用シード（例: `34000` など、過去の検証と重複しないこと）を用いて、展開されたアーカイブ内の `main.py` 同士による 20 試合の対戦を `kaggle_environments` 上で直接実施する。
  * 外部の viability 評価ハーネス（`run_actual_agent_viability.py` など）は、ワークスペースや候補ソース側の `manifest.json` に依存するため使用しない。
  * 対戦中の非合法手（invalid action）率が 0.0、クラッシュ/タイムアウト 0 件であること。
  * **外部ファイル読み込みの排除**: 展開先、Python 標準ライブラリ、明示した venv site-packages、OS runtime 以外は許可しない。`builtins.open`、`io.open`、`os.open` と import 済み runtime module の起源を監査し、archive 外の read が 1 件でもあれば G6 を失敗にする。これは任意の native 拡張による I/O の完全捕捉を主張するものではなく、clean-room の import 境界と archive-only fixture による回帰テストを併用する。
  * **フォールバック telemetry**: `NeuralRuntimePolicy.choose` がアーカイブ内 module として解決できる場合は fallback reason と Student selection count を記録する。計測不能時は `UNAVAILABLE_FROM_ARCHIVE_RUNTIME` として記録し、0 件とみなさない。

### G7: Candidate Freeze (候補メタデータの固定)
* **目的**: 検証が成功したアーカイブの厳格なバージョン・メタデータを確定させる。
* **検証内容**:
  * G1〜G6 がすべて PASS した場合にのみ、検証スクリプトが同一のアーカイブハッシュに対して署名（検証メタデータ出力）を行う。
  * メタデータには、アーカイブSHA、サイズ、エントリポイントSHA、モデルSHA、意味的モデルハッシュ、ビルドコミット、実行環境のライブラリバージョンなどを保存する。
  * `cwd_decoupled_verification: true`、`code_root: extracted_archive`、`working_directory: separate_empty_directory`、Python 3.11.x、Kaggle Environments 1.32.0を保存する。
  * **アーカイブ完全独立性の保証**: `archive_only_verification` が `true` であり、かつ `external_files_read` が空 `[]` であることを検証・記録する。外部ファイルの読み込みが 1 件でもある場合、G6 を失敗させ、検証 JSON を発行しない。

### G8: Explicit Submission Approval (提出前最終確認)
* **目的**: 人間の意図的な意思決定を挟み、検証を通過していないアーカイブが誤ってアップロードされるのを防ぐ。
* **確認内容**:
  * G1〜G7 がすべて PASS し、`submission_verification.json` が出力されていること。
  * アーカイブファイルの実ハッシュと検証ファイル記載のハッシュが完全に一致すること。
  * entrypoint、runtime_main、model、verifier bytesのSHAが一致し、verification commitがHEADのancestorであること。
  * `archive_only_verification`と`cwd_decoupled_verification`がtrue、`external_files_read`が空、Python 3.11.x、Kaggle Environments 1.32.0であること。
  * Git ワークツリーが clean であり、証跡コミットが push 済みであること。
  * ユーザーの承認フラグ `--execute` を明示指定し、提出ラッパースクリプトを呼び出すこと。

---

## 3. 禁止事項

> [!WARNING]
> **Kaggle への提出を実行するコードはリポジトリに存在しません。**
>
> `kaggle competitions submit`、Kaggle API の提出呼び出し、またはそれらを起動するラッパーを追加してはなりません。提出が必要になった場合は、リポジトリ外でユーザー本人が実行します。
