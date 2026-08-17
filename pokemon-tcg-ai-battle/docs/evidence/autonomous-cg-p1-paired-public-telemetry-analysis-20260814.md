# Autonomous cg P1/P0 paired public telemetry analysis — 2026-08-14

## 結論

P1 `cg-lethal-target-v1` と P0 `root-cg-self-owned-v1` の同一 96 strata telemetry を、`(game_id, seat)` ごとの共通 public-state prefix に限定して比較した。4,077 行（P1）と 3,584 行（P0）から 94 行だけが strict に同一 public state として対応し、operation difference は 0 件だった。したがって因果的な action candidate は生成せず、candidate screen / common24 / 384 / 768 / training / teacher / promotion / submission / longrun は起動していない。

この分析は、P1 と P0 の軌跡が最初の state/action divergence 後に同じ counterfactual state ではなくなることを明示的に扱う。後続の非対応行を無理に位置合わせせず、public allowlist 外の field は fail-closed で拒否する。terminal WDL は各 arm の ledger にだけ結合し、相手の hand/prize/deck、teacher/native label、logprob は読まない。

## 入力と結果

| 項目 | 値 |
|---|---|
| P1 telemetry | `runs/final-sprint-autonomous/cg-p1-public-telemetry-96-20260814-v1/telemetry` |
| P0 telemetry | `runs/final-sprint-autonomous/cg-p0-public-telemetry-96-20260814-v2/telemetry` |
| P1 decision rows | 4,077 |
| P0 decision rows | 3,584 |
| strict paired public-prefix rows | 94 |
| operation differences | 0 |
| operation pairs / supported pairs | 0 / 0 |
| candidates | 0 |
| ready_for_candidate_screen | `false` |
| analysis artifact | `runs/final-sprint-autonomous/cg-p1-paired-public-telemetry-analysis-20260814-v1/analysis.json` |
| analysis SHA256 | `5dce92ebeed7011d06525bb8147302dd5f7c148037e2ea105c1a9b841047c8fd` |

The analyzer emitted `insufficient_action_differences`, `insufficient_operation_pair_support`, `insufficient_mixed_sign_paired_outcomes`, and `no_bounded_paired_hypothesis`. This is an information gate, not a performance result.

## 実装と検証

- module: `src/mage_ptcg/meta_specialist/cg_p1_paired_telemetry_v1.py` — SHA `f012098a9899002c41a7d8456bcefc84a30fcc5ff12772f6b0780bdd78d6ae66`
- CLI: `scripts/analyze_cg_p1_paired_telemetry_v1.py` — SHA `a5832313f4b4144209758204a4fe63671f8c494fa7cb0d522b9e2bda01a23de2`
- tests: `tests/meta_specialist/test_cg_p1_paired_telemetry_v1.py` — SHA `09c0745dc4e4fe98ad6e043faf56fa0e0f43f2b652b54ee16798f24facd0378e`
- focused test: `3 passed`
- CLI real-source reload: `ready_for_candidate_screen=false`
- authority: training/teacher/promotion/submission/longrun all `false`

再開条件は、別の public observation source から support・mixed-sign・operation difference が十分に得られ、P1 control と同一 strata で bounded candidate を定義できること。既存 P1/P0 candidate、既評価 failure screen、同一 seed の blind retry は行わない。
