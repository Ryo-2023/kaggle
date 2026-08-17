# cg stratified behavior meta v2 / CEM evidence (2026-08-15)

## 判定

`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`

新しい meta source の獲得・生成方法として、既存の監査済み visible-state exact
transform を、異なる sealed snapshot へ明示的な split 付きで適用する
`stratified_behavior_meta_v2` を実装し、CEM へ接続した。source の freshness、split
独立性、runtime fault0 は通過したが、2世代の独立再評価で robust positive candidate
が成立せず、P1 は BestKnown のまま保持した。

## 実装

- `src/mage_ptcg/opponent_ingest/stratified_behavior_meta_v2.py`
- `scripts/generate_stratified_behavior_meta_v2.py`
- `configs/meta_specialist/cg_stratified_behavior_v2.json`
- `tests/test_stratified_behavior_meta_v2.py`

v2 の fail-closed 条件は次のとおり。

1. `META_TRAIN` / `META_DEV` / `META_FINAL` を spec に明記する。
2. 各 split に2件以上、2 family 以上を要求する。
3. base candidate、source commit、derived policy SHA を pool 全体で重複禁止にする。
4. Alakazam / Comfey / Festival / Psychic と、runtime-safe Metal recipe だけを許可する。
5. current pool と指定 artifact scan に derived policy SHA が出現したら seal を拒否する。
6. static safety、exact 60、visible-state-only、`local_eval_only`、authority false を維持する。

Metal runtime-safe variant は既存 epoch j で使用済みだったため、freshness gate を緩めず、
未使用の Hydreigon snapshot に対する Festival priority transformへ置き換えた。

## sealed source pool

root: `runs/cg-stratified-behavior-meta-20260815-v2/`

| artifact | SHA-256 |
|---|---|
| pool manifest | `f3655e62b24b9b1f4651f285c155d2eb30fa1b21b1b1b67b8759444a986954b4` |
| fresh meta | `e6e6cb22febe585e4380e9697e66cbc7272d899d9a3107e29151a1ec792fab8a` |
| `cg_historical_split.json` | `1736d834a0da9fdfa64176cd5587bbb66a5930574af50f94205d86e3fe05a65d` |
| `meta_manifest.json` | `41ce070bdad79e9a897bc98a857f2927ac05f561aa48cc362ec36aea2f5a76dc` |

12件を seal した。12 distinct source commit、12 distinct base candidate、12 distinct
policy SHA で、split は次の構成である。

| split | count | family coverage |
|---|---:|---|
| META_TRAIN | 8 | Alakazam 2 / Comfey 2 / Festival 3 / Psychic 1 |
| META_DEV | 2 | Alakazam 1 / Psychic 1 |
| META_FINAL | 2 | Alakazam 1 / Festival 1 |

`META_FINAL` の2件は CEM artifact 内に identity hit がなく、CEM中は未使用のまま保持した。

## smoke と CEM

### short connection smoke

P1 control 固定、TRAIN 8 refs、両 seat、1 repetition の短い接続確認を行った。

- requested/completed: `96 / 96`
- status: `DONE=96`
- fault: `0`
- smoke evaluation summary SHA: `492f9ab289cdb1e5e7bd53fa29d9ad85704b7f32fe2efd9d7ad56909dec0ba8b`

### cheap CEM

root: `runs/cg-stratified-behavior-cem-20260815-v2/`

- population: `8`
- elite: `2`
- generations: `2`
- all TRAIN refs、両 seat、独立 re-evaluation `2 blocks`
- campaign seed: `20260962`
- `META_FINAL` identity hit: `0`
- CEM manifest SHA: `c8b702289add192d805842fe22e74ea89ecdf768ffdb1059376cd44e43cbe1bb`
- generation 0 results SHA: `f5239d899e873805a896a6ca990c41146a6ee5294d8462dd5b07a2e1aae615ab`
- generation 1 results SHA: `af4548debf3b9ceb5324e9d420b81f8340e42b6011d53ce948c4eed0db9d85b9`

全 heavy block は fault0 だった。

| block | requested/completed | fault |
|---|---:|---:|
| generation 0 screen | 288 / 288 | 0 |
| generation 0 independent | 192 / 192 | 0 |
| generation 1 screen | 288 / 288 | 0 |
| generation 1 independent | 192 / 192 | 0 |

generation 0 の screen 上位は `−12.50pt` に留まり、独立2 blockで
`+15.625pt / −18.75pt`へ分岐し、`seat_safe=false`（別の候補も screen `−15.625pt`から
`+28.125pt / −9.375pt`へ分岐）。generation 1 は screen 最大
`+18.75pt`だった候補が独立 `−9.375pt / +31.25pt` で、こちらも
`seat_safe=false`。両世代とも
`independent_reeval_x2_positive_delta_gate_preserve_center` となり、CEM center は
P1 のままである。

gen1 の `META_DEV` 診断（CEM選定には使用していない）は、P1 center のまま
candidate `14W-0D-18L` 対 control `20W-0D-12L`、差 `−18.75pt`、fault0。
したがって未使用 `META_FINAL` を消費する候補は成立しておらず、FINAL confirmation は
起動していない。

## 研究判断

今回の成果は、source generation の構造的な問題（family/split 偏り）を修正し、
12件の fresh local-eval source を安全に CEM へ接続できたことにある。性能改善は未成立で、
この pool を native/public evidence として扱わない。

P1 policy、root deck、BestKnown、Champion、production、submission authority は不変。
P2/P3昇格、deck search、commit、push、Kaggle submission は行っていない。

次は同一 pool の blind retry をせず、(a) 新しい許可済み snapshot、または (b) family 別
lower-tail をより安定に推定できる別 source composition を別 epoch で作る。fresh FINAL を
消費するのは、独立複数 blockで正差・seat-safe・fault0を満たす候補が出た場合だけにする。
