# V4 held-out checkpoint evaluation design

## 目的

closed V4 checkpoint を、学習に使わない固定 6 opponent の集合に対して、両 seat を等数にした CABT 実測で評価する。

## 境界

- 新規 runner は `scripts/measure_opponent_strength.py` を変更しない。
- subject は V4 checkpoint のみとし、file SHA-256 と tensor-state SHA-256 の両方を runner が実測・記録する。
- policy 接続は `actor_pool_v1._build_neural_agent_policy_factory_v4` と既存 `runtime.make_agent` 経路を使う。payload を読むだけの loader や V1 policy は使わない。
- 評価相手は `scripts/make_medal_opponents.py` の `EVAL_HELD_OUT_V1` と同じ順序の 6 ID に固定する。CLI から相手集合を変えられない。

## 実行と集計

各 opponent、seat 0/1、`games_per_seat` の直積を一局ずつ実行する。subject は seat 0 では agent A、seat 1 では agent B とし、既存 runner と同じ `run_match`、opponent loading、乱数 seed 初期化を使う。seed は `base_seed + game_index` を各 opponent・seat 内で使い、入力値を JSON に記録する。

fault は予定局数の score 分母に残し、score 分子には入れない。ただし fault は必ず `faults`、`fault_reasons`、相手別・seat 別集計に残す。fault が 1 件でもあれば `comparison_status` は `invalid_faults` とし、score が存在しても比較・採用の根拠にしない。

## JSON 契約

出力は schema/version、checkpoint の absolute path・file SHA・tensor-state SHA、固定 opponent IDs、games-per-seat、base seed、max steps、requested/played/fault games、全体・seat・opponent の W-D-L-F、score と Wilson interval、elapsed seconds を持つ。出力 file は明示的な `--output` を必須にする。

## テスト

CLI を module として呼び、pool/loader/factory/run_match/progress を置換した小さな fixture で、固定 6 opponent・全 seat・max_steps・hash binding・fault invalidation・JSON の実際の値を検証する。V4 checkpoint digest の抽出は小型の実 checkpoint を保存して検証する。
