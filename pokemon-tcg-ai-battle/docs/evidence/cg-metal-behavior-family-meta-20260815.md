# cg Metal/Psychic behavior-family source epoch i — 2026-08-15

## 結論

Metal/Psychic の許可済み historical snapshot から、visible-state priority tableだけを固定変換する4件の新規 policy SHAを `runs/cg-metal-behavior-family-meta-20260815-i/` へ封印した。しかし元 snapshot由来の長時間化が再現し、bounded CABT smoke は fault0 を満たさなかった。したがってこのepochでは CEM、fresh DEV/FINAL、BestKnown昇格を実施しない。Metal/Psychic sourceは runtime-safety の hard-negative として保持する。

## source identity と freshness

- base root: `runs/cg-historical-internal-meta-20260815-c/internal_ozawa-metal-psychic-search_3f5f71d4ff59/`
- branch: `agents/ozawa-metal-psychic-search`
- source commit: `3f5f71d4ff5923ffafe355a9f2e57fd0b88aa675`
- source policy SHA: `f6ef16583d322c558d14a546ddf641799070277d1e30d43bafb9c42d89e3c252`
- staged source policy SHA: `7fb13dda7f1228af5374e4618acf9b25ec35be52579bd82cbeb473336b144a06`
- canonical deck SHA: `dfdfd61d32d84ee2c181890e79ecea29a280f5636de84d3d8a418e026b5171ef`
- source epoch: `internal-metal-behavior-family-20260815-i`
- seed namespace: `internal-metal-behavior-family-seed-20260815-i`
- pool SHA: `9cf7c7646ba8aeab4d1fb0165658d08041337df0f4a615bba66eaa656051b58d`
- fresh meta SHA: `686a7bb53815b45d93bc1a941e04d0dcbf1d4d22c35e5826ec2e8d26422ec27e`
- split SHA: `8a21f9a24f4cd6eee18df84d1a7e74b359638f58324b1028c0049ccde4a0b930`

生成された variant は `PIPLUP_FIRST`、`METAGROSS_FIRST`、`RECEIVER_FIRST`、`LUCARIO_PLAN_FIRST` の4件で、全て新規 policy SHA、同一 canonical deck、static findings 0、`visible_state_only`、`local_eval_only` である。`build_fresh_meta_batch_v1` と weekend split の source verification は PASS した。

## runtime smoke

P1 `cg-lethal-target-v1`＋root deckを候補に固定し、train splitの2 variantを両seatで評価した。

| 条件 | 局数 | DONE | fault | 結果 |
|---|---:|---:|---:|---|
| 既定環境、120秒 timeout | 8 | 1 | 7 | `parent_timeout`、fault rate 87.5% |
| `SEARCH_LOCAL_FIXED_BUDGET=0.1`、120秒 timeout | 8 | 6 | 2 | fault rate 25.0%、最大 runtime 124秒級 |
| `SEARCH_LOCAL_FIXED_BUDGET=0.0`、60秒 timeout | 4 | 0 | 4 | 全局 `parent_timeout` |

既定環境の smoke summary SHAは `c366dc8c506ad23ef4ce80c9890233702268f701fdea1660c70c13840d29e1ef`、budget 0.1 は `6210031cc9e36d15442eec8e1aae150cb5afb28266bbc1fba7b371de8743916b`、budget 0 は `007e086770af16e4215d23088d6f871dc07bdedb7454ad1fef8fd75724041481` である。fault detail はいずれも `parent watchdog exceeded game timeout grace`。元の未変換 Metal/Psychic snapshotも historical c smokeで同じ `parent_timeout` を示しており、今回のpriority変換固有の import／illegal action 例外ではない。

`SEARCH_LOCAL_FIXED_BUDGET` は source intakeで検出された許可済みの環境キーだが、0.1および0.0でも fault0 には到達しなかった。したがって timeout を引き上げて性能評価を続けたり、faultを勝率へ換算したりしない。

## 判定と次の再開条件

- CEM: 未実施（smoke gate不合格）。
- fresh DEV/FINAL: 未実施。
- BestKnown、Champion、production、submission: 不変。
- このepochを再試行する条件は、同じsourceのblind retryではなく、search実行量を構造的に上限化した別の安全な generator／sourceを新epochとして実装し、短い両seat smokeで fault0 を先に確認すること。
- それまでは P1 を唯一の研究 parent とし、Metal/Psychic epoch i を性能根拠・native/public evidenceとして扱わない。

一次 artifact は `runs/cg-metal-behavior-family-meta-20260815-i/`、generator実装は `src/mage_ptcg/opponent_ingest/behavior_family_meta_v1.py`、CLIは `scripts/generate_starmie_behavior_family_meta_v1.py` である。
