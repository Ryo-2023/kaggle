# archaludon seed の学習利用許可

- 日付: 2026-08-05
- 判断者: リポジトリ所有者（本人による明示的な承認）
- 対象: `configs/meta_specialist/seed_candidates_v1.json` の archaludon 候補
  （`raw_deck_sha256 = 42165967…`、`source_path = opponents/tomatomato_archaludon/deck.csv`）

## 決定

上記 1 件の `permission_status` を
`DECK_ONLY_OR_LOCAL_EVAL_SOURCE_TRAINING_PERMISSION_UNKNOWN` から
`PUBLIC_SOURCE_TRAINING_APPROVED_BY_REPOSITORY_OWNER` へ変更し、
`training_permission_unknown` blocker を解除した。

結果、seed qualification は 3 → **4 レーン**（alakazam / grimmsnarl_froslass_munkidori /
rocket_mewtwo_spidops / **archaludon**）となり、`collect-trajectories` が archaludon
レーンを受け付けるようになった。

## 「team internal」と偽らなかった理由

既存の許可状態は 3 つで、Git blob からの materialization を通すのは
`TEAM_INTERNAL_POLICY_MATCH_CONDITIONAL_TECHNICAL_VALIDATION_REQUIRED` だけだった。
archaludon の出所は**公開デッキ**であり、これを team internal と記録すると registry が
資産の出所そのものについて嘘をつくことになる。したがって 4 つ目の状態を追加し、
「公開由来だが所有者が学習利用を明示的に承認した」という事実をそのまま記録した。

`usage_boundary` も専用の `public_source_training_approved_local_only` とし、
team internal 用の値を流用していない。

## 承認が解除しないもの

承認が消したのは `training_permission_unknown` の 1 件だけである。他の blocker は
それぞれの証拠で個別に解除される。実際、今回の qualification では CABT probe が
実行されて通過した結果として qualified になっている。

また、`opponents/` 配下のプール資産は依然としてすべて `local_eval_only` であり、
**提出 bundle へは入らない**。本承認はローカル学習での利用範囲に限る。

## 留意点（未確定・要ユーザー判断）

このデッキを**提出物の `deck.csv` として使う**かどうかは別問題であり、本承認には
含めていない。公開ノートブック由来の 60 枚をそのまま提出することの可否は Kaggle
Rules の確認が要る。学習の初期値・相手として使うことと、提出デッキに採用することを
同一視しないこと。

## 関連

- [公開 Archaludon R7 pilot の取り込み判断](2026-08-05-public-archaludon-r7-seed.md)
- 実測: 修正後プールで R7 63.7%、原版 61.3%（同一 16 相手・160 局）
