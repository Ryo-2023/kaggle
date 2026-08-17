# Self-owned public state/action advantage diagnostic — common24 v1

## 結論

既存の real self-owned Rule v0 common24 96局（24 opponent IDs × 両 seat × 2 repetition、DONE 96/96、fault 0、public action examples 2,865）から、公開 state feature bucket × action type の outcome diagnostic を生成した。特徴量は `phase/action ordinal`、board の public flags、両 seat の active HP/energy、hand/deck/prize count、status mask のみで、card identity・private hand/deck/prize・teacher labels は利用していない。

診断の support は state buckets 1,886、eligible action cells 100 / 417 examples、競合 action が十分な state buckets 3、mixed-sign buckets 2 に留まった。したがって `ready_for_candidate_screen=false`（reasons: `few_competing_state_buckets`, `insufficient_mixed_sign_state_buckets`）であり、candidate evaluator・384局確認・longrun は起動していない。これは signal の不足を示す NO-GO で、native BestKnown の性能評価を置き換えない。

## 再現コマンド

```bash
PYTHONPATH=.:src .venv/bin/python \
  scripts/build_self_owned_public_advantage_v1.py \
  --records runs/final-sprint-autonomous/self-owned-public-outcome-common24-rollout-v1/public-outcome-records.json \
  --evidence-root runs/final-sprint-autonomous/self-owned-public-outcome-common24-rollout-v1/public-evidence \
  --source-manifest runs/final-sprint-autonomous/self-owned-public-outcome-common24-rollout-v1/source-manifest.json \
  --output-dir runs/final-sprint-autonomous/self-owned-public-state-action-advantage-common24-v1
```

このコマンドは source SHA を再検証してから state/action table と bundle manifest を exclusive-create で保存する。既存ファイルの異なる bytes は拒否し、候補 screen を自動起動しない。

## 一次 artifact / SHA

| artifact | SHA-256 |
|---|---|
| bundle manifest | `1f77598ae20e91453c3bf27b1987f5d09581e71a07a4622e93af7b30ee4c0649` |
| state/action table bytes | `1e2348b8bccbff40e5b5b7298001de221d5bfbbdae34f87d2a1afb5b5e15189e` |
| table semantic `table_sha256` | `6078a40d838d57929fa9e20784b9da50fe06d1aa45149603eb29d8ec5b0a6358` |
| source rollout manifest | `3e56a3911367cbcc53436c883371d6f1ff1ba169c8ecd1dc3162c6570b31e388` |
| source records | `c78e5666acd697482dcdafa1bb59b814a9cecd99c80e24d76c83d22d56d221b2` |
| source public evidence | `runs/final-sprint-autonomous/self-owned-public-outcome-common24-rollout-v1/public-evidence/` |

Source identity は root policy `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`、root deck `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、evaluator `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`、engine seed `ENGINE_SEED_UNSUPPORTED` に bind されている。

## 実装・検証

- `src/mage_ptcg/meta_specialist/self_owned_public_advantage_v1.py` — public projection loader、allowlist feature extraction、state/action aggregate、support/mixed-sign quality gate。
- `scripts/build_self_owned_public_advantage_v1.py` — real source SHA 再検証、atomic/exclusive table + manifest materialization。performance run は起動しない。
- `tests/meta_specialist/test_self_owned_public_advantage_v1.py` — feature privacy、source binding、sparse gate、conflicting output rejection。

直近の focused suite は 5 passed、`py_compile` と `git diff --check` も pass。authority は全て false、`candidate_screen_started=false`、`performance_run_started=false`、`ready_for_longrun=false` である。
