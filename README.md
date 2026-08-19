# kaggle

Kaggle コンペティションの作業リポジトリ。コンペごとにディレクトリを分ける。

| ディレクトリ | コンペ | 状態 |
|---|---|---|
| [biohub-cell-tracking-during-development/](biohub-cell-tracking-during-development/) | Biohub – Cell Tracking During Development | 開発中 |
| [pokemon-tcg-ai-battle/](pokemon-tcg-ai-battle/) | The Pokemon Company - PTCG AI Battle Challenge (Simulation) | 終了・アーカイブ |
| [house-prices-advanced-regression-techniques/](house-prices-advanced-regression-techniques/) | House Prices - Advanced Regression Techniques | — |
| [titanic/](titanic/) | Titanic - Machine Learning from Disaster | — |

## biohub-cell-tracking-during-development

3D+time microscopy 上の細胞検出・追跡を扱う Kaggle コンペティション用プロジェクト。MacBook では Docker ベースの CPU 開発環境を使用し、重い学習は Kaggle または NVIDIA/Linux 環境へ持ち出せる構成にする。

- [ローカル開発手順](biohub-cell-tracking-during-development/README.md)
- [データ配置と認証情報](biohub-cell-tracking-during-development/data/README.md)

## pokemon-tcg-ai-battle

2026年7月上旬から8月17日まで取り組んだ、ポケモンカードゲームの対戦 AI とデッキ構築のプロジェクト。デッキ 60 枚と、公式シミュレータ上で合法手を返す方策を開発した。

- [プロジェクト総括報告書](pokemon-tcg-ai-battle/docs/postmortems/2026-08-17-project-final-report.md) — 目的、方針転換の理由、実験結果、反省点
- [アーカイブ情報](pokemon-tcg-ai-battle/ARCHIVE.md) — 最終状態、構成、復元手順、公開範囲

**これはチーム非公開リポジトリの部分ミラーである。** 第三者の方策・提出物・配布エンジン・カードデータは再配布しないため含まれない。除外対象と理由は [ARCHIVE.md](pokemon-tcg-ai-battle/ARCHIVE.md) の「公開ミラー」節を参照。
