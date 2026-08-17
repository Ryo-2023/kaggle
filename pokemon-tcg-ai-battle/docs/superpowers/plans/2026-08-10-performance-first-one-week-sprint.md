# 性能優先・1週間スプリント実装計画

## 結論

目標は学習開始ではなく、独立 validation と対戦評価で再現可能な性能向上を得ることである。研究用の高速経路と formal authority 経路を分離し、合法手、非公開情報境界、split leakage、episode reset、checkpoint reload、有限勾配だけを高速経路の必須条件とする。

## 並行経路

### Track A: current-R2 champion–challenger

- 実在する load 済み checkpoint を固定 baseline とする。
- 各 optimizer update は新規 rollout 一式を一度だけ消費し、同じ batch を次 update に再利用しない。
- actor 更新前に current-R2 内蔵 value head を短く warm-up する。
- actor は小さい学習率で 1 update だけ行い、checkpoint 保存・新 process reload を確認する。
- train と異なる opponent instance、両 seat で quick screen を行い、悪化時は baseline へ rollback する。

### Track B: recurrent v4 BC

- `representation_v4` / `SpecialistModelV4` / recurrent v4 sequence を使う。
- teacher-quality formal READY 不在は研究用 uniform weight として明示し、promotion authority には使わない。
- component 分離済み train/validation の小規模 subset、2 seed で complete-action NLL を測る。
- validation 改善が再現した場合だけ full-corpus BC と GPU 長時間実行へ進む。

## 合格条件

- illegal action 0、未処理 fault 0。
- train/validation の episode・component overlap 0。
- checkpoint 保存後の reload で state hash 一致。
- 2 seed の validation が平均で改善し、lane 単位に大幅回帰しない。
- 対戦 quick screen で平均が正方向、または統計的不確実性内でも lane 別に重大な悪化がない。
- 合格しない候補は長時間実行しない。

## 実行順

1. Track A runner、Track B trainer、opponent/eval inventory を並行実装・監査する。
2. focused unit/integration tests と CPU bounded smoke を実行する。
3. Sol high の独立レビューで、性能選択ロジックを反証する。
4. GPU host 用コマンドを一つにまとめ、2 lane × 2 seed の短期 pilot を実行する。
5. 合格した経路のみ長時間学習へ昇格する。

## 非目標

- 研究 pilot 前の 384-game formal campaign。
- 18-cell formal Gate の完遂。
- 性能選択に使わない artifact hardening の追加。
- commit、push、Kaggle 提出。
