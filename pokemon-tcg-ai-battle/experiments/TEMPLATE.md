# <実験名または提出名>

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | YYYY-MM-DD HH:MM TZ |
| 担当 | human / agent 名 |
| 種別 | local experiment / ablation / Kaggle submission |
| commit | `<full commit hash>` |
| branch | `<branch>` |
| model provenance | model ID / provider / effort / CLI version、または `モデルなし` |
| simulator / data | version、hash、取得日、または識別可能な path |

## 目的と反証条件

- **問い**: 何を確認するか。
- **仮説**: 何が真なら採用候補になるか。
- **反証条件**: 何が観測されたら棄却または再検証するか。
- **変更点**: baseline から変えた要素を 1 つずつ書く。
- **固定条件**: deck、opponent、seed、対戦数、時間制限など。

## 再現

```bash
# 実行コマンド
```

生成物は Git 管理外へ置き、ここには path、hash、size を記録する。

## 結果

| condition | seeds | games | win rate | timeout | illegal action | runtime | 備考 |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline |  |  |  |  |  |  |  |
| proposed |  |  |  |  |  |  |  |

- **sanity check**: 桁、範囲、集計件数、欠損、異常終了の確認。
- **負の所見**: 悪化、失敗、想定外を省略しない。
- **不確実性**: seed 間ばらつき、標本数、既知の評価バイアス。

## 解釈と判断

- **観測事実**: 結果表から直接言えること。
- **解釈**: 事実を説明する仮説。代替説明も書く。
- **判断**: 採用 / 棄却 / 保留 / 再実験。
- **言わないこと**: この条件から一般化できない主張。
- **次 action**: 最大 3 件。owner と停止条件を添える。

## Kaggle 提出（該当時）

| 項目 | 値 |
|---|---|
| submission name |  |
| submitted at |  |
| source commit |  |
| local verification |  |
| Public LB | 結果待ち / score |
| Private LB | 未確定 / score |
| Kaggle URL / ID |  |
| 備考 |  |
