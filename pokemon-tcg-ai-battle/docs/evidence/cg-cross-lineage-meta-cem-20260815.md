# Cross-lineage meta source recipe / CEM evidence (2026-08-15)

## 結論

公開 source の単純な再利用に依存せず、smoke 済み source の policy lineage と
deck lineage を別々に選び、wrapper と `policy×deck` identity を再生成する
`CROSS_LINEAGE_POLICY_DECK_RECOMBINATION_V1` を実装した。v1 は静的・合法性ゲートを
通過した4候補を生成し、P1 対象の両seat runtime smoke 8/8 を fault 0 で完了した。
昇格後の fresh meta と custom split を `run_cg_p1_cem_v1.py` へ接続できたが、2世代の
risk-aware CEM は独立 lower-tail positive と seat-safe を同時に満たさず、P1 center を
保持した。したがって性能昇格はなく、BestKnown、Champion、production、submission は
不変である。

## source recipe

実装:

- `src/mage_ptcg/opponent_ingest/cross_lineage_meta_v1.py`
- `scripts/generate_cross_lineage_meta_v1.py`
- `scripts/rebind_cross_lineage_split_v1.py`
- `tests/test_cross_lineage_meta_v1.py`

生成器は sealed candidate root（または multi-row pool 内の candidate root）を明示的に
受け取り、policy parent と deck parent の直積から同一 lineage の組み合わせを除外する。
payload は変更せず、repository-owned wrapper を候補 IDに対して再生成するため、wrapper
policy SHA と `policy×deck` pair identity は新しくなる。元の public/kernel source bytes、
parent policy SHA、parent deck canonical SHA、recipe、evidence SHA を `SOURCE.md`、pool、
fresh metaへ記録する。

静的ゲートは exact 60、local official card ID、公式 catalog がある場合の ACE SPEC exactly
one、payload AST safety、symlink/cache 除外を確認する。生成直後の pool は
`smoke_ok=false` とし、既存の bounded CABT smoke → `promote_historical_meta_smoke_v1.py`
→ split rebind の順でのみ CEMへ昇格できる。生成 artifact はすべて
`local_eval_only`、authority 全 false である。

## v1 artifact

入力 policy parent は v7 Raunak と v9 Prvsiyan control、deck parent は v7/v9 と v8
Faheem Dragapult を使った。v8 deck は過去 CEMへ投入済みのため、v8 deckを含む候補は
「新しい pair identity」ではあるが、parent deck自体の未使用性を主張しない。v7 policy ×
v9 deck と v9 policy × v7 deckは smoke-only parent 同士の新しい組み合わせである。

| artifact | 値 |
|---|---|
| generated root | `runs/cg-cross-lineage-meta-v1-20260815/` |
| generated pool SHA | `e8a94ae352df0b4a0506b6e79f1b81c412cb7c4ee54570363e409f32a7ee7bdb` |
| promoted root | `runs/cg-cross-lineage-meta-promoted-v1-20260815/` |
| promoted pool SHA | `611b3e1bd2ccbffc655dea39a6c9ed16cc3842010c03caff98100cf8362c8a5f` |
| promoted fresh meta SHA | `7284a36278cf3d8ff2d888a5966cfe54ee5fab6897869cdc9c5232d1e211985f` |
| rebound split SHA | `a29482721e319fd55a40de9c199eb61cfb6bc55e204451bb8a689a79c4234742` |
| accepted candidates | 4 |
| parent policy/deck pair reuse | 0 |
| static findings | 0 |

P1 package は policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、
root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` で固定した。

## runtime smoke

`scripts/run_historical_meta_smoke_v1.py` を base seed `20260880`、両seat各1局、8 games、
worker 8、timeout 120秒で実行した。

- requested/completed: `8/8`
- `DONE`: 8
- faults: 0
- outcomes: 5 win / 0 draw / 3 loss
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- smoke summary: `runs/cg-cross-lineage-meta-smoke-v1-20260815/smoke_summary.json`

この結果を全4件について昇格し、promoted root上で `fresh_meta` と `cg_historical_split`
の hash binding を再検証した。

## CEM

`runs/cg-cross-lineage-cem-v1-20260815/` で P1 control 固定の policy CEM を実行した。
`META_TRAIN` は2件、`META_DEV` 1件、`META_FINAL` 1件とし、FINALは選定へ読んでいない。
population 12、elite 3、2 generations、campaign seed `20260881`、screenは全 train refs、
独立 re-evaluation 2 block、positive-delta gate、risk-aware updateを使用した。

- total evaluator rows: 304（全て DONE、fault 0）
- gen0 screen上位: `+37.50pt`。independent deltaは `−12.50pt`、worst `−25.00pt`
- gen1 screen上位: `+25.00pt`。independent deltaは `−25.00pt`、worst `−25.00pt`
- gen1で最も良かった独立候補: mean delta `0pt`、worst `0pt`
- robust positive / seat-safe candidate: 0
- center update: 0回（両世代 `incumbent-center`）
- FINAL access: なし（未使用のまま保全）

従って本epochは `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL` と分類する。
screenの一時的な差は独立再評価で反転または消失しており、P1をP3へ昇格させる根拠には
ならない。

## 次の判断

この recipe は「新しい meta source を作り、fault-free smoke後に `cg_bestknown_loop_v1.py`
へ接続する」実装仮説を実証した。一方、CEM改善は未達である。次回は同じ pair の blind
retryをせず、(1) policy parent を未性能使用の source に限定した cross-lineage batch、
または (2) actor-visible failure-conditioned adapter と cross-lineage deckの混合で、
TRAINの source diversity を増やす。どちらも `legality → static → fault0 → independent
positive → seat-safe → unused DEV → unused FINAL` を順守し、positive gateを通らない限り
BestKnown/Championを変更しない。
