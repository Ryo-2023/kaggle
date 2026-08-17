# Autonomous Meta Fine-Tuning Design

## Goal

既存の強い native `deck + agent` population を保持したまま、観測済み opponent pool の重み付き分布へ適応し、native BestKnown を超える提出可能な pair と再開可能な長時間ループへつなげる。

## Boundary

- 既存の pool、native `main.py`、提出 entrypoint、Champion、Kaggle archive は変更しない。
- `local_eval_only` は評価・選抜には使えるが、teacher collection、行動ラベル、submission へ拡張しない。
- `META_TRAIN` は training permission が明示された pair だけを teacher/behavior source として許可する。
- 24/48局の小差、NLL単独改善、Lucifer hard-BC、tomato AWR、既存 residual/prior/V5系列は再実行しない。
- CABT engine seed setter がないため、block/seat/opponent の独立層化として扱い、paired と呼ばない。

## Components

1. `meta_distribution_v1`: census と native ranking artifact から、`META_TRAIN` / `META_DEV` / `META_FINAL`、top-meta / hard-negative / diversity の重み、permission scope、source SHA を閉じた manifest にする。
2. `native_tuning_surface_v1`: native source を読み取り、直接変更可能な score/threshold/search parameter、deck identity、fallback 可否、runtime class を記録する。native bytes は不変にする。
3. `native_preserving_adapter_v1`: native policy を fallback として保持し、明示的に許可された bounded override だけを適用する。未知状態、malformed action、timeout は native へ戻す。
4. `alternating_meta_optimizer_v1`: policy config と合法 deck mutation を別々の時間尺度で候補化し、同一 meta schedule と native baseline で successive-halving する。
5. `longrun_autonomous_v1`: rollout/score、hard-negative更新、policy候補、deck race、META_DEV評価、rollback、atomic checkpoint/resume を固定 budget で反復する。LONGRUN_READY gate を通るまで execute は fail-closed にする。

## Data flow

`census + ranking artifacts -> immutable meta manifest -> permission-aware schedule -> native-preserving candidate -> 96/384/768/1536 arena -> native comparison -> candidate/deck checkpoint -> dev update`

`META_FINAL` は candidate selection へ渡さず、最終確認時まで未使用であることを manifest の authority と hash に保存する。

## Acceptance gates

- Manifest は全 source SHA と split disjointness を検証する。
- training schedule は permission false の row を fail-closed で拒否する。
- native baseline の deck/policy SHA は candidate artifact に bind され、fallback は常に利用可能である。
- candidate は複数 block・複数 seat・fault 0 で native baseline を上回る方向を示す。
- 片 seat の悪化は概ね5pt以内、exact opponent 1件だけの改善は不合格とする。
- LONGRUN_STARTED は checkpoint/resume、stop/rollback、evaluator、package closure が固定されてから開始する。

