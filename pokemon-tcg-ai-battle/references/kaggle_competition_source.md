# Kaggle Competition Source Notes

## URL
- Simulation Division (メインコード): [pokemon-tcg-ai-battle](https://www.kaggle.com/competitions/pokemon-tcg-ai-battle)
- Strategy Division (分析・手法): [pokemon-tcg-ai-battle-challenge-strategy](https://www.kaggle.com/competitions/pokemon-tcg-ai-battle-challenge-strategy)

## Overview (コンペ概要)
- **主催**: The Pokémon Company（株式会社ポケモン）
- **目的**: ポケモンカードゲーム（PTCG / ポケカ）をプレイする優れたAIエージェントを構築する。
- **特徴**: 
  - ランダム要素（コイントスやドローなど）、非対称な情報（手札や山札などの隠し情報）、複雑なゲーム状態への対応が必要。
  - ルールベースの単純なAIだけでは難しく、適応性の高い機械学習アプローチが求められる。

## Gameplay & Simulation Environment (ゲーム・シミュレーター環境)
- **提供コード (C++ベース)**: 
  - Kaggle サーバー側での評価環境と同じ挙動をするシミュレーター（`ptcg_engine`）のソースコードが配布されており、ローカル環境での強化学習トレーニングやデバッグに使用可能。
- **カードプール**: スタンダードレギュレーションから厳選された約2,000枚のカードが使用される。

## Data Provided (提供されているデータ一覧)
Kaggle API 等を通じて以下のデータが提供されています。
- `EN_Card_Data.csv`: 英語のカード属性（Card ID, Name, HP, Stage, Category等）を格納したCSVファイル
- `JP_Card_Data.csv`: 日本語のカード属性を格納したCSVファイル
- `Card_ID List_EN.pdf`: 各種カード画像とCard IDの一覧（英語版）
- `Card_ID List_JP.pdf`: 各種カード画像とCard IDの一覧（日本語版）
- `ptcg_engine/`: ローカル環境等で動くゲームシミュレーターのソースコード（C++）

## Rules & Participation (主なルール)
- **チーム人数**: 最大5名（同一チームは両部門でメンバー構成が同じである必要がある）。
- **評価方法**: シミュレーション対戦のキュー結果に基づきリーダーボードでランク付けされる。
- **提出制限 (Submission Limits)**:
  - 1日あたりの最大提出回数: **5回**。
  - 常時アクティブにできるエージェント数: **最大2つ**（これらがサーバー上で対戦エピソードをプレイします）。新しく提出すると古いエージェントは自動的に無効化されます。
  - 最終選考サブミッション数: **最大2つ**。
- **タイムライン (Timeline)**:
  - チーム合併締め切り: **2026年8月9日**
  - 最終提出締め切り: **2026年8月16日**
  - リーダーボード最終収束: **2026年8月31日頃**
- **コード制限と実行制約 (Constraints)**:
  - 対戦は制限時間およびステップ制限（ステップ数上限）があります。無限ループに陥ったエージェントはタイムアウトで自動的に敗北となります。
  - チーム外でのコードやデッキレシピの非公開共有は厳禁。共有はKaggleフォーラムで行う必要があります。

