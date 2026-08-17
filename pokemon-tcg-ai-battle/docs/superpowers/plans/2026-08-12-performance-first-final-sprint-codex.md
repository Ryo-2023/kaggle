# Performance-First Final Sprint 実行計画（2026-08-12）

## 目的

研究モデルの局所改善を先に進めず、実際に提出可能な `deck.csv` × `main.py` と、同じ identity を測る評価器・archive を閉じる。Kaggle への送信はユーザーが明示するまで行わない。

## 固定する制約

- CABT の legality を hard truth とする。
- 相手の非公開情報を入力・target・評価証跡へ混入させない。
- fault は勝率の分母から除外しない。
- policy、deck、checkpoint、package、evaluator の SHA を各 artifact へ保存する。
- engine RNG を固定できない現状では、同じ base seed を paired evidence と呼ばず、層化した独立評価として扱う。
- 既存 dirty 差分を削除・reset・commit・push しない。

## 実行順

1. root `main.py`、root `deck.csv`、branch/HEAD、既存 package、研究 checkpoint の実体を監査し、提出中候補と研究上の候補を分離する。
2. process pool と game-level atomic ledger を持つ研究用並列 CABT evaluator を追加し、直列 runner との smoke で整合性と throughput を確認する。
3. deck 重複、policy 重複、seat、opponent identity を含む coherent pair inventory と broad pool を freeze する。
4. current submission pair、Rule v0 + Archaludon、Wave6 seed0/seed1 + 対応 Archaludon deckを同一 evaluator で段階評価する。24 局だけで昇格せず、96→384→768→1536 局を逐次拡張する。
5. Rule v0 + root deck と Wave6 V4 + Archaludon deckを archive-only で展開し、repo/worktree 非依存、CPU、legality、fault、latency、checkpoint/deck identity を検証する。
6. 最も強い coherent pair を固定して deck 候補を Stage 1 から比較し、上位だけ Stage 2 以降へ進める。
7. package と broad arena が閉じた後に限り、outcome/value based learning を一系列だけ GO/NO-GO 判定する。constant value baseline を超えない場合は長時間学習を開始しない。
8. 最終成果物、未解決点、`LONGRUN_STARTED` または `LONGRUN_NOT_STARTED` を status/handoff/context pack へ記録する。

## 非目標

- residual、V5、teacher、threshold、Rule prior の追加 sweep。
- 24〜192 局の小標本だけを根拠にした Champion 変更。
- Kaggle への無断 submission。
- 無関係な formatter、refactor、既存 dirty 差分の cleanup。

## 完了条件

- 実際の entrypoint と package identity が一次証拠で説明できる。
- evaluator が game 単位の fault、outcome、policy/deck/opponent/seat/block/runtime を保存し、直列 smoke と整合する。
- 少なくとも二つの coherent pair が archive-only smoke を通る。
- broad arena の局数、pool、noise、fault、seat/opponent 別結果が再現可能な artifact に閉じる。
- package 推奨順位と、学習を始めるかどうかを明示できる。
