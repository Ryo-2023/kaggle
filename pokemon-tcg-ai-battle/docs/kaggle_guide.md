# Kaggle Beginner Guide

このドキュメントは、Kaggleを初めて触る人が、このリポジトリで迷わず作業を始めるためのガイドです。

## 1. Kaggleとは

Kaggleは、データサイエンス、機械学習、AIのためのオンラインプラットフォームです。

主な機能は以下です。

| 機能 | 何をする場所か |
|---|---|
| Competitions | 企業や研究機関が出す課題に参加し、提出物のスコアを競う |
| Datasets | 公開データセットを探す、使う、公開する |
| Notebooks / Code | ブラウザ上でPythonやRを動かす |
| Discussions | コンペ参加者同士で質問や知見を共有する |
| Models | モデルや重みを共有・利用する |
| Leaderboards | コンペの順位表を見る |

このプロジェクトでは主に `Competitions`、`Datasets`、`Discussions`、`CLI` を使います。

## 2. Kaggleコンペの基本

Kaggleコンペには、だいたい以下の情報があります。

| タブ | 見る内容 |
|---|---|
| Overview | 問題設定、目的、評価方法、期限 |
| Data | ダウンロードできるファイル、データ説明 |
| Code | 参加者のNotebookやサンプルコード |
| Discussion | 質問、運営アナウンス、参加者の知見 |
| Rules | ライセンス、チーム、外部データ、共有制限 |
| Submit | 提出方法 |
| Leaderboard | 順位表 |
| My Submissions | 自分の提出履歴 |

最初に必ず見るべき順番は以下です。

1. `Overview`
2. `Rules`
3. `Data`
4. `Submit`
5. `Discussion` の運営アナウンス

## 3. 典型的なKaggleコンペの流れ

一般的なKaggleコンペは次の流れです。

1. アカウントを作る
2. コンペページで参加し、ルールに同意する
3. データをダウンロードする
4. データを調べる
5. ベースラインを作る
6. ローカルまたはKaggle Notebookで検証する
7. 提出する
8. スコアとエラーを確認する
9. 改善して再提出する

このコンペは通常の予測CSV提出ではなく、AIエージェントコードを提出するタイプです。そのため、`submission.csv` を作るよりも、`agent` 関数とデッキを正しく作ることが中心になります。

## 4. Standard CompetitionとCode Competitionの違い

Kaggleには大きく2種類の提出形式があります。

| 種類 | 提出物 | 例 |
|---|---|---|
| Standard Competition | 予測結果のCSV | Titanicで `PassengerId,Survived` を提出 |
| Code / Simulation Competition | Notebook、スクリプト、エージェントコード | このPokemon TCG AI Battle |

Standard Competitionでは「正解に近いCSV」を作ります。

Code / Simulation Competitionでは「Kaggle側が実行するコード」を作ります。今回のコンペでは、Kaggleのシミュレーターがこちらの `agent` を呼び、対戦を進めます。

## 5. Kaggle CLIとは

Kaggle CLIは、ターミナルからKaggleを操作する公式コマンドです。

公式READMEによると、CLIでできる主な操作は以下です。

- コンペの一覧表示
- コンペデータのダウンロード
- コンペへの提出
- データセットの作成・更新・ダウンロード
- Notebookの取得・更新・実行
- Discussionの閲覧

このプロジェクトでは、主に以下を使います。

```bash
kaggle competitions files -c pokemon-tcg-ai-battle
kaggle competitions download -c pokemon-tcg-ai-battle -p data/raw
kaggle competitions submit pokemon-tcg-ai-battle -f <submission_file> -m "message"
kaggle competitions submissions -c pokemon-tcg-ai-battle
kaggle competitions leaderboard pokemon-tcg-ai-battle --show
```

提出ファイル名や形式はコンペの `Submit` タブで確認してください。

## 6. CLIセットアップ

このリポジトリでは `requirements.txt` に `kaggle` が含まれています。

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
```

インストール後に確認します。

```bash
kaggle --help
```

`kaggle: command not found` が出る場合は、仮想環境が有効化されていないか、PythonのscriptディレクトリにPATHが通っていません。

## 7. Kaggle認証

Kaggle CLIでデータ取得や提出を行うには認証が必要です。

公式CLIドキュメントでは、主に以下の方法が案内されています。

### OAuth login

```bash
kaggle auth login
```

ブラウザでログインする方式です。使えるならこれが分かりやすいです。

### API token

Kaggleの `Settings` の `API` セクションからトークンを発行します。

```bash
export KAGGLE_API_TOKEN=xxxxxxxxxxxxxx
```

### Legacy kaggle.json

Kaggleの設定画面から `kaggle.json` を発行し、以下に置きます。

```bash
mkdir -p ~/.kaggle
mv kaggle.json ~/.kaggle/kaggle.json
chmod 600 ~/.kaggle/kaggle.json
```

注意:

- `kaggle.json` は絶対にGitへコミットしない
- このリポジトリの `.gitignore` では `kaggle.json` と `.env` を除外している
- チームメンバーへトークンを共有しない

## 8. データ取得

コンペに参加し、Rulesに同意した後でデータを取得します。

```bash
mkdir -p data/raw
kaggle competitions download -c pokemon-tcg-ai-battle -p data/raw
unzip data/raw/pokemon-tcg-ai-battle.zip -d data/raw/extracted
```

このリポジトリでは `data/` はGit管理対象外です。大きいデータ、ZIP、PDF、学習済みモデルはコミットしないでください。

## 9. Kaggle Notebookを使うべきか

Kaggle Notebookはブラウザ上で動く実行環境です。

使うメリット:

- 環境構築が軽い
- GPU/TPUが使える場合がある
- Kaggleデータセットとの連携が簡単
- コンペ提出がNotebook前提のときに便利

ローカルを使うメリット:

- Gitで管理しやすい
- エディタやテストを使いやすい
- 大きなリファクタリングがしやすい
- チーム開発しやすい

このプロジェクトでは、開発はローカル、提出確認やKaggle特有の実行環境確認はKaggle側、という使い分けが現実的です。

## 10. Leaderboardの見方

Leaderboardは順位表です。

一般的なKaggleコンペでは、Public LeaderboardとPrivate Leaderboardがあります。Publicは一部データでの暫定スコア、Privateは最終評価用データでのスコアです。

今回のようなシミュレーション系では、提出エージェントが対戦キューで評価され、結果が収束するまで時間がかかる可能性があります。1回提出してすぐ出た順位だけで強さを判断しない方がよいです。

見るべきもの:

- 自分の提出がエラーになっていないか
- 評価が完了しているか
- 順位が時間とともに大きく動いていないか
- 同じ提出でも対戦数が増えると安定するか

## 11. Discussionの使い方

Discussionは重要です。

見るべき投稿:

- 運営アナウンス
- ルール変更
- データ不具合
- サンプルコードの修正
- 提出エラーの報告
- よくある質問

注意:

- チーム外で非公開にコードやデッキを共有しない
- 共有する場合はKaggle Discussionなど公開された場所で行う
- 他チームの非公開情報を使わない

## 12. このプロジェクトでの安全ルール

Gitに入れてよいもの:

- ソースコード
- 小さな設定ファイル
- ドキュメント
- 実験メモ
- 再現用スクリプト

Gitに入れないもの:

- `kaggle.json`
- `.env`
- `data/`
- ZIP
- PDF
- 学習済みモデル
- 大きなログ
- Kaggle提出用の一時成果物

このリポジトリの `.gitignore` は、`data/`, `submissions/`, `outputs/`, `models/`, `*.zip`, `*.csv`, `*.pt`, `*.pth` などを除外しています。

## 13. 初心者向けの最初の作業チェックリスト

- [ ] Kaggleアカウントを作る
- [ ] コンペページを開く
- [ ] 参加してRulesに同意する
- [ ] `Overview` を読む
- [ ] `Rules` を読む
- [ ] `Data` を確認する
- [ ] `Discussion` の運営投稿を読む
- [ ] `uv pip install -r requirements.txt` を実行する
- [ ] Kaggle CLIを認証する
- [ ] `kaggle competitions files -c pokemon-tcg-ai-battle` を実行する
- [ ] データを `data/raw` に落とす
- [ ] サンプル提出コードを読む
- [ ] ランダムエージェントをローカルで動かす
- [ ] 提出ログを `experiments/` に残す

## 14. よく使うコマンド

```bash
# コンペのファイル一覧
kaggle competitions files -c pokemon-tcg-ai-battle

# コンペデータをダウンロード
kaggle competitions download -c pokemon-tcg-ai-battle -p data/raw

# ZIPを展開
unzip data/raw/pokemon-tcg-ai-battle.zip -d data/raw/extracted

# 提出履歴を確認
kaggle competitions submissions -c pokemon-tcg-ai-battle

# リーダーボードを表示
kaggle competitions leaderboard pokemon-tcg-ai-battle --show
```

## 15. 用語集

| 用語 | 意味 |
|---|---|
| Competition | コンペ。決められた課題でスコアを競う |
| Submission | 提出物 |
| Leaderboard | 順位表 |
| Public LB | 暫定評価用の順位 |
| Private LB | 最終評価用の順位 |
| Notebook / Kernel | Kaggle上で動くコード実行環境 |
| Dataset | データセット |
| Discussion | 掲示板 |
| Host | コンペ主催者 |
| Team | コンペ参加チーム |
| Baseline | 最低限動く基準実装 |
| EDA | Exploratory Data Analysis。データ探索 |

## 16. 参照リンク

- Kaggle: https://www.kaggle.com/
- Competitions docs: https://www.kaggle.com/docs/competitions
- Notebooks docs: https://www.kaggle.com/docs/notebooks
- Datasets docs: https://www.kaggle.com/docs/datasets
- Public API docs: https://www.kaggle.com/docs/api
- Kaggle CLI repository: https://github.com/Kaggle/kaggle-api
- Kaggle CLI user docs: https://raw.githubusercontent.com/Kaggle/kaggle-api/main/docs/README.md
- Kaggle CLI competition commands: https://raw.githubusercontent.com/Kaggle/kaggle-api/main/docs/competitions.md
- Kaggle CLI tutorials: https://raw.githubusercontent.com/Kaggle/kaggle-api/main/docs/tutorials.md
