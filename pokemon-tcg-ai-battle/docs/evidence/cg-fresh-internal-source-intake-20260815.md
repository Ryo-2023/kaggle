# cg fresh internal source intake — 2026-08-15

## 結論

公開sourceを解禁せず、許可済み `origin/agents/*` のbranch snapshotだけから、既存poolと重複しない新しいlocal-eval候補を1件stagedできた。候補は `ozawa-rocket-rule` の commit `de797c3646e935157618be3edea17615430ccfec` である。現行 `opponents/`、BestKnown、Champion、production、提出packageは変更していない。

この成果物はmeta sourceの供給方法とfreshness契約を閉じたものであり、性能改善結果ではない。候補はasset preflightと両seatの1局ずつのCABT plumbing smokeを通過したが、policy CEM、独立seedの性能確認、deck phase、BestKnown昇格はまだ実施していない。

## 入力と権限境界

- permission: `configs/opponents/permissions/pokemon_team_agents_internal_v1.yaml`
- ref glob: `refs/remotes/origin/agents/*`
- self-owned除外: `refs/remotes/origin/agents/ono-cg-lethal-v1`
- rules attestation: `UNVERIFIED_RULES_CONSTRAINT`。公開 `PUBLIC_OTHER` は引き続きarchive-onlyで、今回の入力に含めていない。
- 現行pool: `opponents/pool_manifest.json`、SHA `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`

discoveryはGit checkout、import、network access、現行pool書換えを行わない。候補のstatic scanはimport inventoryとenvironment keyを記録し、network/subprocess/dynamic execution/secret/unsafe filesystem writeをfail-closedにする。提出bundleへの再配布、automatic Champion promotion、training authorityは全てfalseである。

## accepted candidate

| field | value |
|---|---|
| staged root | `runs/cg-fresh-internal-meta-intake-20260815-f/` |
| candidate id | `internal_ozawa-rocket-rule_de797c3646e9` |
| source branch | `agents/ozawa-rocket-rule` |
| source commit | `de797c3646e935157618be3edea17615430ccfec` |
| source policy SHA | `8025ae95503ef10cc82a433518e81ba61554ce1547846eecc582610a85ae6c7f` |
| staged policy SHA | `159a5d61ce7d1d12cf955a5d2bf99845b25d3d32eedc3904ee46e21143be053e` |
| source deck bytes SHA | `caed4ca0b98d53f66cf6e54a77241e818fd59ecfd0c9591e16e4c23a7b15cc12` |
| canonical deck SHA | `d61230a21f488d4e78b28b37187c6a468168c0a2fff7842025e6c0409da3614a` |
| pool manifest SHA | `99942d1081ce1105bde2e2f19007986a866073aeb92e30528520732a4c982513` |
| fresh meta SHA | `ae0c3f3606565556cbe7b4dc95553005bb4c1cde79852fa1b89f56deb8213438` |
| freshness evidence SHA | `466521bb319d52f614892cede4161bb88a791a50dac7e9a571b40a38871359bf` |

元branchはKaggle提出環境のcwd相対 `deck.csv` を読むため、staged `main.py`には既存frozen opponentと同じ `LOCAL_DECK_SIDECAR_V1` を適用した。source policy SHAとstaged policy SHAを別フィールドで保持し、候補自身の横のdeckを読むことを確認した。

## freshnessと除外

14 remote tracking refを走査し、1件をaccepted、13件をrejectedした。既存source commit／policy identity／artifact identityの再利用を除外し、self-owned P1を明示除外した。`ozawa-grimmsnarl-rule+RL` は `GRIMMSNARL_PLAN_TELEMETRY` を介した任意pathへのappendがあるため `filesystem_write` でquarantineした。これは安全検査を緩めて採用していないことの確認である。

fresh manifestは `meta-specialist-cg-fresh-meta-batch-v1` として、pool SHA、staged policy SHA、canonical deck SHA、証跡SHA、source epoch `internal-agents-20260815`、seed namespace `internal-agents-seed-20260815`、authority falseを固定している。`build_fresh_meta_batch_v1` と `load_opponent_pool_v1` はPASSした。

## CABT plumbing smoke

現BestKnown root deck／built-in `rule` と候補を1局ずつ両seatで実行した。いずれも `agent_status=[DONE,DONE]`、fault/invalid/timeoutなしで、これは実性能の勝率証拠ではない。

| seat | seed | steps | result | artifact SHA |
|---|---:|---:|---|---|
| rule → candidate | 20260815 | 121 | DONE、candidate勝ち | `ae68b6cd0356c55092b2003f4567d7524361a2dbfa6b02285fd3d532a6679edf` |
| candidate → rule | 20260816 | 80 | DONE、candidate勝ち | `97a9996131bdb866ab7efe3beaca6179fb059c8d0610c0976400c9dcb36201b3` |

runnerは `engine_seed_supported=false` を報告する既存 `scripts/test_sim.py` であり、2局はplumbing smokeに限定する。candidateの勝率、native比較、CEM elite選択の根拠には使わない。

既存 `scripts/run_cg_p1_cem_v1.py` に `--pool-root` と `run_campaign(..., pool_root=...)` を追加し、staged poolをcurrent `opponents/`へコピーせずに注入できるようにした。`build_paired_games` のread-only preflightではこの候補IDを4 game payload（両arm・両seat）へ解決できた。これはCABTを起動せず、CEMを実行した結果でもない。

## 次の再開条件

1. `internal_ozawa-rocket-rule_de797c3646e9` を新fresh batchのlocal-eval opponentとして固定する。
2. 可能ならfilesystem writeを除去した別internal snapshotを追加し、少なくとも複数policyのsource diversityを作る。
3. staged poolを使うcustom runnerでP1 control対policy CEMをscreenし、独立seed・fault0・seat gap≤5%を確認する。
4. positiveが再現した候補だけを `cg_bestknown_loop_v1.py` へ渡し、`policy → deck → policy`へ進める。

ここでのfresh batchは研究専用で、Champion変更、提出、commit、push、公開source利用を許可しない。
