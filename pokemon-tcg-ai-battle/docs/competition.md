# Pokemon TCG AI Battle Competition Notes

このドキュメントは、Kaggleコンペ
[The Pokemon Company - PTCG AI Battle Challenge Simulation](https://www.kaggle.com/competitions/pokemon-tcg-ai-battle)
に取り組むためのプロジェクト内メモです。

Kaggleの公式ページはJavaScriptで本文が描画されるため、ここでは以下を根拠に整理しています。

- Kaggle公式ページのメタ情報: titleは `The Pokemon Company - PTCG AI Battle Challenge Simulation`、descriptionは `Build an AI Training Agent to play the Pokemon Trading Card Game`
- Kaggleから取得済みの `data/raw/pokemon-tcg-ai-battle.zip`
- ZIP内の `ptcg_engine/ptcgProgram 22/README.md`
- ZIP内の `sample_submission/sample_submission/main.py`
- 既存メモ: [references/kaggle_competition_source.md](../references/kaggle_competition_source.md)

最終的なルール、期限、提出制限は必ずKaggleの `Overview` / `Rules` / `Submit` タブで確認してください。

## 1. コンペの目的

このコンペは、ポケモンカードゲームを自動でプレイするAIエージェントを作るコンペです。

通常のKaggleコンペのように「テストデータに対する予測CSVを提出する」形式ではなく、ゲーム状態を受け取り、合法手の中から次の行動を選ぶエージェントコードを提出するタイプです。評価は、提出したエージェントがシミュレーション対戦を行い、その対戦結果に基づいて順位が決まる形式です。

このプロジェクトで作るべきものは大きく2つです。

1. 合法な60枚デッキを返す処理
2. 毎回の選択局面で、提示された選択肢から有利そうな行動を返す `agent` 関数

## 2. 競技の基本構造

### Simulation Division

このリポジトリで主に扱うのはSimulation Divisionです。

- AIエージェントのPythonコードを提出する
- サーバー側のシミュレーター上で他エージェントと対戦する
- 勝率、レーティング、対戦キューの結果に基づいてリーダーボード順位が決まる
- 評価は一回の固定データ予測ではなく、複数回の対戦結果で揺れる可能性がある

### Strategy Division

別枠としてStrategy Divisionも用意されています。

- コード提出ではなく、手法、戦略、学習方針、分析をまとめる部門
- Simulation Divisionで作った方針を文章化・分析する用途に近い

Strategy Divisionに参加するかどうかはチーム方針次第です。まずはSimulation Divisionで動くエージェントを作ることを優先します。

## 3. 提供データとファイル構成

ローカルには以下のファイルが存在します。

```text
data/raw/
├── EN_Card_Data.csv
├── JP_Card_Data.csv
├── Card_ID List_EN.pdf
├── Card_ID List_JP.pdf
└── pokemon-tcg-ai-battle.zip
```

ZIP内には以下が含まれます。

| パス | 内容 |
|---|---|
| `EN_Card_Data.csv` | 英語カードデータ。2102行、17カラム |
| `JP_Card_Data.csv` | 日本語カードデータ。2102行、17カラム |
| `Card_ID List_EN.pdf` | 英語カード画像とカードIDの一覧 |
| `Card_ID List_JP.pdf` | 日本語カード画像とカードIDの一覧 |
| `ptcg_engine/ptcgProgram 22/` | C++20ベースの対戦シミュレーター |
| `sample_submission/sample_submission/` | 提出用サンプルコード一式 |

カードCSVの主なカラムは以下です。

| カラム | 意味 |
|---|---|
| `Card ID` / `カード ID` | 提出デッキで使うカードID |
| `Card Name` / `カード名` | カード名 |
| `Expansion` / `エキスパンションマーク` | 収録弾 |
| `Stage ...` | たね、1進化、2進化、エネルギー、トレーナーズ等 |
| `Rule` | exなどの特殊ルール |
| `Category` | ポケモン、グッズ、サポート等の分類 |
| `Previous stage` / `進化前` | 進化元 |
| `HP` | HP |
| `Type` / `タイプ` | タイプ |
| `Weakness` / `弱点` | 弱点 |
| `Resistance` / `抵抗力` | 抵抗力 |
| `Retreat` / `にげる` | にげるコスト |
| `Move Name` / `ワザ名` | ワザ名 |
| `Cost` / `コスト` | ワザのエネルギーコスト |
| `Damage` / `ダメージ` | ワザのダメージ |
| `Effect Explanation` / `効果の説明` | ワザやカード効果の説明 |

## 4. 提出コードのインターフェース

サンプル提出コードの中心は `main.py` の `agent(obs_dict: dict) -> list[int]` です。

ZIP内のサンプル実装では、次のような流れになっています。

```python
from cg.api import Observation, to_observation_class

def agent(obs_dict: dict) -> list[int]:
    obs: Observation = to_observation_class(obs_dict)
    if obs.select == None:
        return read_deck_csv()

    return random.sample(list(range(len(obs.select.option))), obs.select.maxCount)
```

重要な仕様は以下です。

| タイミング | エージェントが返すもの |
|---|---|
| `obs.select is None` | 初期選択。60枚のデッキをカードIDのリストで返す |
| `obs.select` がある | 選択肢 `obs.select.option` のインデックスをリストで返す |

通常の選択局面で返すリストには制約があります。

- 各要素は `0 <= index < len(obs.select.option)`
- 要素数は `obs.select.minCount` 以上、`obs.select.maxCount` 以下
- 同じインデックスを重複して返さない
- 返した選択が合法手でないと、評価エラーや敗北につながる可能性がある

## 5. デッキ仕様

初期選択では、60枚のカードIDリストを返します。

サンプルでは `deck.csv` から60行を読み込み、その整数IDを返しています。

```text
1158
721
721
...
```

注意点:

- デッキは必ず60枚
- Pokemon TCGのデッキ構築ルールに従う必要がある
- カードIDは `EN_Card_Data.csv` / `JP_Card_Data.csv` と `Card_ID List_*.pdf` で確認する
- 同名カード枚数制限、基本エネルギー、ACE SPEC等の特殊ルールはKaggle公式ルールとシミュレーター挙動で確認する

このリポジトリ直下の `deck.csv` は現時点では空です。提出前に、サンプルの `deck.csv` をコピーするか、自作デッキを作る必要があります。

## 6. シミュレーター

ZIP内の `ptcg_engine/ptcgProgram 22/README.md` によると、配布されているエンジンはコンペ用のPTCG対戦エンジンです。

特徴:

- C++20ベース
- `Export.cpp` / `All.h` がエントリポイント
- Visual Studio 2022 solution付き
- C++標準ライブラリのみで、外部依存はなし
- サンプル提出にはPythonラッパー `cg/` と共有ライブラリ `libcg.so`, `libcg.dylib`, `cg.dll` が含まれる

ライセンス上の注意:

- エンジンやカードデータはコンペ参加目的でのみ使用する
- チーム外へ再配布しない
- コンペ終了後の扱いはKaggle公式ルールとZIP内ライセンスに従う

## 7. 評価の考え方

このコンペでは、単純な正解ラベル予測ではなく、ゲームプレイの強さが評価されます。

考えるべき観点:

- 勝率を上げる
- タイムアウトしない
- ルール違反の選択を返さない
- 不完全情報を扱う
- ランダム要素を考慮する
- 長期的に有利な盤面を作る

不完全情報として、相手の手札、山札、サイドなどは見えません。ドロー、コイントス、サーチ、シャッフルなどのランダム性もあります。短期的に最大ダメージを出すだけでは弱くなる可能性があります。

## 8. 提出制限と期限

既存メモでは以下の制限が記録されています。

| 項目 | 内容 |
|---|---|
| 1日あたりの提出回数 | 最大5回 |
| 同時にアクティブなエージェント | 最大2つ |
| 最終選考対象の提出 | 最大2つ |
| チーム合併期限 | 2026-08-09 |
| 最終サブミッション期限 | 2026-08-16 |
| リーダーボード収束目安 | 2026-08-31頃 |

この表はKaggle画面で再確認してください。Kaggleのルール・日程・提出制限は変更される可能性があります。

## 9. このリポジトリでの推奨作業順

### Step 1: サンプル提出を展開して動かす

まずはZIP内のサンプルを展開し、`sample_submission/sample_submission/main.py` の `agent` がどう呼ばれるかを確認します。

```bash
mkdir -p data/raw/extracted
unzip data/raw/pokemon-tcg-ai-battle.zip -d data/raw/extracted
```

### Step 2: データを確認する

カードCSVの行数、カラム、欠損を確認します。

```bash
python scripts/inspect_data.py
```

現状の `scripts/inspect_data.py` は `data/raw/sample_submission/...` を見に行くため、ZIP展開先に合わせて修正が必要な可能性があります。

### Step 3: 最小の合法エージェントを作る

最初は強さよりも、以下を満たすことを優先します。

- `agent(obs_dict)` が必ず返る
- 選択肢の範囲外を返さない
- `minCount` / `maxCount` を守る
- 同じ選択肢を重複しない
- デッキを60枚返す

### Step 4: ルールベースで改善する

初期版は以下のようなルールベースで十分です。

- 取れるサイド数が増える攻撃を優先
- きぜつを狙える攻撃を優先
- 手札を増やすカードを優先
- エネルギー加速を優先
- 進化できるなら進化する
- ベンチ展開を優先
- 無駄な選択を避ける

### Step 5: ローカル対戦で勝率を見る

ランダムエージェント、サンプルエージェント、自作エージェント同士で対戦し、以下を記録します。

- 対戦数
- 勝率
- タイムアウト数
- 例外数
- 平均ターン数
- よく負ける局面

### Step 6: 提出ログを残す

提出ごとに `experiments/` 配下へメモを残します。

```text
experiments/
└── 2026-07-09-baseline-random.md
```

記録する内容:

- 提出日時
- git commit hash
- 使ったデッキ
- エージェントの方針
- ローカル検証結果
- Kaggle提出メッセージ
- LB結果
- 次に直すこと

## 10. 実装方針の候補

### A. ルールベース

最初に作るべき方針です。

長所:

- デバッグしやすい
- タイムアウトしにくい
- バグの原因を追いやすい

短所:

- 複雑な盤面で限界が来る
- デッキごとの調整が必要

### B. モンテカルロ・探索

合法手ごとに簡易シミュレーションを行い、期待値が高い行動を選びます。

長所:

- 不確実性に対応しやすい
- 短期的な読みを入れられる

短所:

- 実行時間制限に弱い
- シミュレーター呼び出しコストが高い可能性がある

### C. 強化学習・模倣学習

セルフプレイやログから方策を学習します。

長所:

- ルールベースより複雑な判断を学習できる可能性がある
- デッキと環境に適応できる可能性がある

短所:

- 学習環境整備が重い
- 評価が不安定
- 初期の実装コストが高い

このプロジェクトでは、まずAのルールベースを堅く作り、ローカル対戦ログが取れるようになってからBまたはCを検討するのが現実的です。

## 11. 提出前チェックリスト

- [ ] `agent(obs_dict)` が定義されている
- [ ] 初期選択で60枚のデッキを返す
- [ ] すべての通常選択で合法なインデックスだけを返す
- [ ] `minCount` / `maxCount` を守る
- [ ] 例外を握りつぶさず、想定外でも安全な合法手を返す
- [ ] ループが長時間止まらない
- [ ] `kaggle.json` やトークンをコミットしていない
- [ ] `data/` や大きなZIPをコミットしていない
- [ ] KaggleのRulesタブを再確認した
- [ ] 提出ログを `experiments/` に残した

## 12. 参照リンク

- Competition: https://www.kaggle.com/competitions/pokemon-tcg-ai-battle
- Rules: https://www.kaggle.com/competitions/pokemon-tcg-ai-battle/rules
- Data: https://www.kaggle.com/competitions/pokemon-tcg-ai-battle/data
- Discussion: https://www.kaggle.com/competitions/pokemon-tcg-ai-battle/discussion
- Kaggle CLI: https://github.com/Kaggle/kaggle-api
- Kaggle CLI docs: https://raw.githubusercontent.com/Kaggle/kaggle-api/main/docs/README.md
