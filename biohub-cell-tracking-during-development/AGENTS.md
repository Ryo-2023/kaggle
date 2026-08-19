# AGENTS.md — Biohub Cell Tracking 共通ガイド

Claude Code、Codex、その他の AI エージェントが共有する開発ガイド。プロジェクト共通ルールはこのファイルに集約し、ツール別ファイルへ同じ内容を重複して書かない。

## 1. プロジェクト概要

Kaggle **Biohub – Cell Tracking During Development** 向けに、3D+time 顕微鏡画像から細胞を検出し、時系列で追跡する手法を開発する。

主なデータ形式は次のとおり。

| 対象 | 形式 / 意味 |
|---|---|
| 画像 | OME-Zarr、基本軸 `(T, Z, Y, X)` |
| 追跡正解 | GEFF graph |
| node | 時刻と細胞中心 `(t, z, y, x)` |
| edge | 時刻間の同一細胞対応 |
| division | 1 parent → 2 children の分岐 |

正解アノテーションは疎である。**未注釈細胞を背景・負例と決めつけない**こと。

コンペ規則、評価式、提出形式、期限など変更されうる事項は、重要な判断の前に Kaggle 公式情報を確認する。推測した仕様を正典にしない。

## 2. 現在の開発環境

ローカル開発の正規環境は **Docker Compose 上の Ubuntu 24.04**。

```text
MacBook / host
    │
    │ bind mount
    ▼
Docker: biohub-dev
Ubuntu 24.04
/workspace
    ├── src/
    ├── tests/
    ├── data/
    └── ...
```

- Compose service: `biohub`
- container name: `biohub-dev`
- workspace: `/workspace`
- Python: 3.11
- package manager: `uv`
- MacBook の Docker 環境は CPU-only
- 重い学習・全データ評価は将来 Kaggle / NVIDIA Linux 環境へ移してよい

### 実行場所の原則

**ソース編集は host 側でもよいが、Python・テスト・lint・学習・評価は Ubuntu container 内で実行する。**

host から実行する場合:

```bash
docker compose exec -T biohub uv run pytest -q
docker compose exec -T biohub uv run ruff check .
docker compose exec -T biohub python path/to/script.py
```

すでに `biohub-dev` / `/workspace` 内にいる場合は通常どおり実行してよい。

```bash
uv run pytest -q
uv run ruff check .
python path/to/script.py
```

### 禁止事項

- host macOS に `pip install` しない。
- host に新しい venv / conda 環境を作らない。
- container 内でも別の venv / conda を勝手に追加しない。
- 一時的な手作業だけで依存を追加しない。必要な依存は `pyproject.toml` / `uv.lock` に反映する。
- Docker image、volume、container を理由なく削除・初期化しない。

環境定義を変更した場合は `Dockerfile`、`docker-compose.yml`、`pyproject.toml`、`setup.sh` の整合性を確認する。

## 3. 正典と参照順

現時点では次を優先する。

| ファイル | 役割 |
|---|---|
| `AGENTS.md` | AI エージェント共通ルール |
| `README.md` | 開発環境・利用手順の入口 |
| `pyproject.toml` | Python version / dependency の正典 |
| `docker/Dockerfile` | Ubuntu image の定義 |
| `docker-compose.yml` | local development container の定義 |
| `setup.sh` | 初回セットアップと環境検証 |
| `tests/` | 実行可能な期待仕様 |

文書と実際の挙動が違う場合は、差異を隠さず報告する。**古い文書へ実装を無理に合わせるのではなく、どちらが正しいか根拠を確認する。**

## 4. 作業開始時

作業開始時は次を短く確認する。

1. `AGENTS.md` と対象機能の README / 既存コード / テストを読む。
2. `git status --short` で既存差分を確認する。
3. ユーザーや別エージェントの未コミット変更を上書き・削除・無関係に整形しない。
4. Docker を使う作業なら `docker compose ps` で `biohub-dev` の状態を確認する。
5. 変更前に「何を変えるか」「何をもって成功とするか」を明確にする。

曖昧さが軽微なら妥当な仮定を明示して進めてよい。ただし、**評価の公平性、データリーク、提出、正解形式、大規模な設計変更**に関わる曖昧さは推測で確定しない。

## 5. 実装方針

### 小さく変える

- 1タスクで複数の独立した設計変更を混ぜない。
- 既存コードで自然に表現できるなら再利用する。
- 新規ファイルは、責務を独立させる意味がある場合だけ作る。
- 関係のないリファクタや依存更新を便乗させない。

### 問題を隠さない

- broad `except` で例外を握りつぶさない。
- データ不足、shape mismatch、NaN / Inf、I/O error を無意味な default 値で隠さない。
- fallback は実運用上成立する代替経路だけに使う。
- テストを通すために評価条件や本質的アルゴリズムを歪めない。

### デバッグ

不具合時は「とりあえず修正を重ねる」より原因切り分けを優先する。

```text
再現
  ↓
エラー・入力・環境を確認
  ↓
壊れている境界を特定
  ↓
最小の修正
  ↓
同じ再現手順で検証
```

3個の推測修正を重ねるより、1個の根本原因を確認する。

## 6. Biohub 固有の注意

### sparse ground truth

- 未注釈 = negative と扱わない。
- training loss、sampling、metric 実装で sparse annotation の前提を壊さない。
- dense label を暗黙に仮定する変更は、根拠と影響を明示する。

### 座標・軸

- `(T, Z, Y, X)` の軸順を勝手に入れ替えない。
- voxel coordinate と physical coordinate を混同しない。
- 座標変換・scale を導入する場合は入力と出力の単位を明示する。

### tracking graph

- detection quality と linking quality を区別して評価する。
- division を通常 edge と同じ扱いで破壊しない。
- CSV / GEFF 変換では node、edge、division の意味保存を検証する。

## 7. 実験・評価の規律

AV-Suara や過去 Kaggle 開発で重要だった原則として、**「改善したつもり」と「比較可能な改善」を分ける。**

### 基本ループ

```text
Baseline 再現
    ↓
仮説を1つ立てる
    ↓
最小変更
    ↓
同条件で評価
    ↓
結果と失敗理由を記録
    ↓
採用 / 棄却 / 次仮説
```

- baseline を再現できる前に大規模改造へ進まない。
- 比較時は split、seed、metric、pre/post-processing、評価対象を揃える。
- 条件を変えた比較は、同条件比較であるかのように扱わない。
- 一度に多数の変更を入れて「どれが効いたか不明」にしない。
- public leaderboard だけで改善判断しない。local validation を持つ。
- leaderboard へ過剰適応する試行を、一般化性能改善と表現しない。
- 良い seed / checkpoint / subset だけを後から選んで通常結果として提示しない。
- 無効 run、途中停止、データ欠損も必要なら記録する。

### 性能主張

「改善した」「best」「直った」「再現できた」は、**実行結果がある場合だけ**使う。

最低限、次を追跡できるようにする。

- 実行コマンド
- config / split / seed
- 使用データ
- checkpoint または artifact path
- metric
- baseline との差

証拠がない数値を埋めない。

## 8. データ・生成物

- Kaggle data、checkpoint、prediction、submission CSV、大容量 artifact は原則 Git 管理外。
- `.gitignore` を尊重する。
- Kaggle credential、API token、秘密情報をコード・ログ・文書・commit に入れない。
- `~/.kaggle` は認証用であり内容を読み上げたりコピーしたりしない。
- データが無いとき、乱数やダミーデータで本実験の結果を代用しない。
- synthetic / dummy data は unit test・smoke test にのみ使い、その旨を明示する。
- 全 competition data のダウンロードが不要なら、必要な範囲だけ取得する。

## 9. Kaggle 提出

**Kaggle への実提出はユーザーの明示指示がある場合だけ行う。**

次の状態は提出許可を意味しない。

- validation が改善した
- submission.csv が生成できた
- tests が通った
- agent が「ready」と判断した

ローカルで提出物を生成・検証することはよいが、外部送信は別操作として扱う。

コンペ規約で認められていないデータ、他参加者の非公開成果物、秘密情報を取得・利用しない。

## 10. Git 運用

- `git commit`、`git push`、branch 削除、merge はユーザーから明示的に依頼された場合だけ行う。
- 既存の差分を無断で revert しない。
- destructive command (`reset --hard`, `clean -fd`, force push 等) を独断で使わない。
- commit する場合は対象変更だけを含め、無関係な生成物を混ぜない。

## 11. テスト・検証

変更内容に対応する最小テストを先に選び、最後に関連範囲を広げる。

通常の基本確認:

```bash
uv run pytest -q
uv run ruff check .
```

host からなら:

```bash
docker compose exec -T biohub uv run pytest -q
docker compose exec -T biohub uv run ruff check .
```

環境変更時は最低限次も確認する。

```bash
cat /etc/os-release
python --version
uv --version
```

**テスト未実行なら「通った」と書かない。** 実行できなかった場合は理由を明記する。

## 12. AI エージェント運用

- 調査、実装、検証を分ける。
- 別エージェントの自己申告ではなく、差分と実行結果を主担当が確認する。
- 並列化する場合は責務・対象ファイルを分離する。
- 同じファイルを複数エージェントが同時編集しない。
- 安いモデルから順番に失敗させるためだけの無意味な多段実行をしない。
- deterministic に確認できることは grep、diff、test、metric script 等で確認し、モデルの感想で代替しない。
- 高性能モデルを使っても commit / push / submit の権限は増えない。

## 13. ドキュメント・報告

文章は**短く、構造化し、後から読んでも判断根拠が分かる形**にする。

### 文章ルール

- 結論を先に書く。
- 1段落 = 1論点。
- 3項目以上の並列・比較は箇条書きか表にする。
- 関係性や処理フローは、文章より明確になる場合だけ Mermaid 図を使う。
- 同じ内容を本文・表・図で繰り返さない。
- 同義反復や長い前置きを削る。
- 既存文書を編集するときは、必要以上に文章量を増やさない。
- 初見で意味が分からない独自略語・造語を増やさない。
- 未検証の性能・結果を断定しない。

### 作業報告

長い生ログをそのまま貼らず、基本は次の順で報告する。

1. **結論** — 何が分かった / 何を変更したか。
2. **根拠** — metric、test、diff、主要ログ。
3. **成果物** — 変更ファイル、artifact path。
4. **残課題** — BLOCKED、未検証、次の一手。

必要な再現コマンドは載せるが、数百行のログは要約する。

## 14. 完了条件

タスクは「コードを書いた」だけでは完了しない。

```text
要求を満たす変更
    +
関連テスト / 評価
    +
結果確認
    +
未解決事項の明示
    = 完了
```

完了報告では、実際に確認したことと未確認のことを分ける。

## 15. BLOCKED 時

進められない場合はダミー結果で穴埋めせず、短く次を示す。

- どこで止まったか
- 再現コマンド / エラー要点
- 確認済み原因
- 未確認事項
- 次に必要な判断または資源

**推測を事実として埋めるより、正確に BLOCKED と報告する。**
