# Strong Asset native pair ranking — fast96

## 結論

`runs/meta-specialist-asset-ranking-primary-fast96-20260812/asset_ranking.json` は、102件の pool census から smoke-ready 96件を選び、各 asset の native deck + native agent pair を同一の共通 arena で 96局（24 opponents × 2 seats × 2 repetitions）評価した研究用ランキングである。student 蒸留モデルの比較ではなく、元の pair 自身を評価している。

この run は 96/101 smoke-ready assets を完了した。上位は次の通りである。

| 順位 | native asset | W/D/L/F | score rate | seat0 | seat1 |
|---:|---|---:|---:|---:|---:|
| 1 | `plamen06_steel` | 76/0/20/0 | 79.17% | 40/48 | 36/48 |
| 2 | `tomatomato_archaludon` | 73/0/23/0 | 76.04% | 39/48 | 34/48 |
| 3 | `lucifer19_battlecore` | 70/0/26/0 | 72.92% | 35/48 | 35/48 |
| 4= | `aristophanivan_multiply` | 65/0/31/0 | 67.71% | 38/48 | 27/48 |
| 4= | `nihei_alakazam` | 65/0/31/0 | 67.71% | 34/48 | 31/48 |
| 6= | `dashimaki360_crustlecounter` | 64/0/32/0 | 66.67% | 32/48 | 32/48 |
| 6= | `kojimar_lucario` | 64/0/32/0 | 66.67% | 31/48 | 33/48 |
| 8= | `ozawa_crustle_rule_rl` | 62/0/34/0 | 64.58% | 30/48 | 32/48 |
| 8= | `ozawa_crustle_v2` | 62/0/34/0 | 64.58% | 32/48 | 30/48 |
| 8= | `ozawa_grimmsnarl_rule_rl` | 62/0/34/0 | 64.58% | 28/48 | 34/48 |

完全な96件の行、相手別・seat別集計、hash、eligibility metadata は一次 JSON artifact を正本とする。96局の差はまだ screen であり、plamen06_steel と tomatomato_archaludon の優劣を確定する promotion evidence ではない。

## 実験仕様

- schema: `meta-specialist-asset-pair-ranking-v1`
- subject: native external policy + native deck（各 asset の `main.py` と `deck.csv`）
- arena: `performance_first_broad_pool_v1.json` の 24 reference IDs
- self-play: candidate 自身を opponent から除外し、fallback で reference 数を維持
- games: 96 / asset = 24 opponents × 2 seats × 2 repetitions
- requested games: 9,216 = 96 × 96
- seed: global ordinal sequence `9000000`〜`9009215`（subjectごとに96 unique seeds）
- pairing: `independent_stratified_not_game_paired`; engine RNG setter は未対応
- evaluator: bounded in-flight spawn evaluator, `max_workers=8`, `max_in_flight_games=8`, `worker_recycle_games=32`
- policy/deck/opponent identity: rowごとに SHA-256 と usage/source metadata を保存
- promotion/training/submission authority: false（ranking は research-only）

## 整合性検査

2026-08-12 実行済みの read-only 検査結果:

- `asset_ranking.json`: `asset_count=96`, ranking rows=96, requested=9,216
- `ledger.jsonl`: 9,216 rows、9,216 unique `game_id`、manifest game IDs と一致
- 96 subject 全てが 96 rows（seat0=48、seat1=48）
- subject ごとに 24 distinct opponents、self-play 0
- subject ごとに 96 unique seeds
- evaluator summary: 9,207 `DONE`、9 `FAULT`、fault denominator は requested 9,216 に保持
- outcome: W=3,697、D=8、L=5,502、fault=9、score denominator=9,216、score rate=40.1584%（全 subject 行の合計）
- 9 fault は全て `medal_0019_df6f7443` の `STEP_LIMIT; cabt terminal result unavailable`（steps=1,999）で、worker crash/timeout ではない
- `medal_0019_df6f7443` は fault-free promotion ranking から quarantine する

## coverage の注意

pool census は 102 rows、うち smoke=true は 101 rows（R7 `public_archaludon_cinderace_r7` は smoke=false）。今回の fast96 では、次の5つを slow/未完了のため含めていない。

- `kinoshita_pimc_search`
- `ozawa_metal_psychic_search`
- `tientrum_alakazam_search`
- `water_box_search`
- `waterbox_search_v3`

したがって、この artifact は「96 native pairs の完了済み ranking」であり、全101 smoke-ready（または全102登録 asset）の GlobalBestKnown 確定ではない。上記5件は別の低並列・短い diagnostic または scheduler の fail-fast 対応後に測定し、R7 smoke=false は別 quarantine row として扱う。

## 一次 artifact と SHA-256

| artifact | path | SHA-256 |
|---|---|---|
| ranking | `runs/meta-specialist-asset-ranking-primary-fast96-20260812/asset_ranking.json` | `7ad461caebd8bc8b21b1600f1719d8107f4654c0b2236c8ddcb57996f8b94b29` |
| ledger | `runs/meta-specialist-asset-ranking-primary-fast96-20260812/ledger.jsonl` | `dc68512a72d57b804589692b2603f9b7fc872a61fc336d7ab93623641e57704a` |
| evaluator summary | `runs/meta-specialist-asset-ranking-primary-fast96-20260812/summary.json` | `23f532e7b7ad5af08432ff6a37baeaf89fe1a0941fc06abcccd5790fabc10bb2` |
| evaluator manifest | `runs/meta-specialist-asset-ranking-primary-fast96-20260812/manifest.json` | `161f18d0367d456b5a7cf1680d1d1a1ec619e9bbb82f984c0d1e6940c1269147` |

evaluator implementation SHA: `ae476cc72ac4efcf080dff118b1c4ef15268edf8e1d22b9b04cb432d48f9a797`。

## 次の判断

1. `plamen06_steel`, `tomatomato_archaludon`, `lucifer19_battlecore` を暫定 top-3 として 384 → 768 → 1536局へ段階確認する。
2. 5 slow assets を全体 ranking に混ぜる前に、個別 diagnostic で runtime/step-limit を測る。
3. top-2〜3 の native pair を BestKnown candidate として freeze し、training/submission permission と package closure を別の eligibility audit で確定する。
4. native BestKnown を超えたことを確認するまで、hard-label/outcome-weighted BC の同型 sweep、Champion変更、Kaggle submission は行わない。

