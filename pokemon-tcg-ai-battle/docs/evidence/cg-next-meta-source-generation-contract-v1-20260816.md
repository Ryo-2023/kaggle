# 次の meta source 生成契約 v1（2026-08-16）

## 結論

v21までの independent-root policy surface は、TRAIN 4 source・2世代の risk-aware CEM でも strict lower-tail positive を作れなかった。公開 source intake の直近監査も、新しい安全な外部 snapshot を安定して追加できる状態ではない。したがって、同じ renderer の係数や seed を増やすのではなく、**deck と policy を同時に変える source-side hard-negative factory**を次の研究方式にする。

この文書は方式選定と境界の固定であり、CABT実行・BestKnown／Champion変更・production変更・提出を行った記録ではない。

## 現在の前提

- 現行 BestKnown は self-authored P1 policy（SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`）＋ common/public root deck（SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）である。pair 全体を self-owned deck＋policy とは呼ばない。
- v21 `runs/cg-self-owned-independent-cross-lineage-v21-20260816/` は source generation／promotion／runtime 4x smoke／TRAIN CEM／DEV 診断まで完了したが、candidate は lower-tail、seat-safe、opponent×seat-safe を同時に満たさず、BestKnown loop へ接続していない。
- `scripts/run_self_owned_cg_deck_screen_v1.py` は candidate 側の self-owned package manifest、candidate/control の deck identity、同一 opponent・seat・seed strata を要求する。deck phase は policy gateを通過した候補だけに許可する。
- 既存の source-side adversarial CEM、action-conditioned、deck-adaptive、cross-lineage、public intake はそれぞれ過去の性能 exposure を持つ。既存 pool／seed／candidate の blind retry は行わない。

## 新方式: portfolio-hard-negative source factory

### 生成単位

1. 公式カード CSV と新しい role specification から、互いに canonical deck hash が異なる 4 件以上の self-owned deck を生成する。
2. P1 の parameterized overlay だけでなく、deck-bound package として各 deck に policy configuration を束ねる。`main.py`、`deck.csv`、`cg/` runtime、package manifest は候補ごとに完全封印する。
3. `policy config × deck recipe` の Cartesian candidate を source-side subject とし、同じ policy SHA、deck SHA、source commit、generator plan を再利用しない。

### source-side objective

- CABT の terminal WDL、seat、opponent identity だけを使う。action trace、private field、teacher label は使わない。
- 固定 reference portfolio に対する `0.5 * (mean_score + worst_reference_score) - fault_rate` を基本 objective とする。
- reference ごとに両 seat を要求し、fault 0、seat gap 5% 以下、完全な seat collapse なしを必須にする。
- screen の上位1件だけを採用せず、独立 seed の複数 block で全 elite 候補を再評価する。

### source pool の構成

- source-side selection は `META_TRAIN` だけで行う。
- source identity と freshness evidence を生成時点で封印し、`META_DEV` と `META_FINAL` は source-side selection、policy CEM、candidate selection の全てから隔離する。
- 最終 pool は少なくとも 4 source、2 以上の deck family、2 以上の policy configuration family を含める。各 row に deck/policy/config/seed/source lineage の SHA を持たせる。
- source-side hard-negative が P1 だけへの過適合にならないよう、複数 reference の worst score を使う。ただし同一 reference を DEV／FINALへ再利用しない。

## 実行ゲート

```text
deck legality
  → static safety / no hidden fields
  → package runtime smoke: 各 source × seat 4局以上、fault 0
  → source-side independent validation: 複数 seed、複数 reference、seat gap ≤5%
  → TRAIN-only meta split seal
  → P1 policy CEM（TRAINのみ）
  → candidate independent blocks: positive lower-tail、fault 0、seat-safe、opponent×seat-safe
  → 未使用 DEV
  → 未使用 FINAL
  → 通過した場合のみ cg_bestknown_loop_v1.py の policy phase
  → policy phase 通過後のみ deck phase
```

いずれかの段階で失敗したら、その source epoch と seed namespace は性能使用済みとして封印し、同じ候補の retry や DEV／FINAL の読み出しをしない。

## 成功条件と停止条件

成功条件は、source generation 自体ではなく、同じ未使用 split で P1 に対する candidate が独立複数 block の全てで正差を示し、fault 0、seat-safe、opponent×seat-safe を満たすこと。その後の未使用 DEV／FINALでも正差が再現した場合だけ research parent に昇格する。

次の場合は方式を再設計する。

- 4 source 以上を作れても source-side validation が seat collapse／runtime fault になる。
- source-side objective は改善するが、P1 policy CEM の独立 blockで正差へ転移しない。
- deck family を増やしても policy/deck hash 相関が高く、worst reference が一つの family に偏る。

## 権限境界

この方式は research-only、local-eval-only である。training、promotion、longrun、submission authority は全て false とする。BestKnown、Champion、`deck.csv`、production package、commit、push、Kaggle submission は strict gate 通過後もユーザーの明示許可なしには変更しない。

## 再開コマンドの前提

実装開始前に、次を生成して hash を記録する。

- 新しい source plan（4 deck recipe 以上、policy variant 以上、seed namespace）
- source factory manifest と candidate identity table
- source-side TRAIN／DEV／FINAL split
- package runtime smoke manifest

これらが揃うまで CABT の heavy source search、P1 CEM、deck screen は起動しない。

