# 公開kernel fresh epoch6c／P1 CEM（2026-08-16）

## 結論

新しいmeta sourceの獲得方法として、Kaggle公開kernel outputをローカルへ取得し、tar（または取得済み`main.py`＋`deck.csv`の再パック）をSHA固定して、静的安全検査・60枚／ACE SPEC合法性・過去exposure identity検査・bounded runtime smokeへ通す経路を実証した。epoch6cは8候補中6 sourceを受理し、P1対の24局smokeを24/24 `DONE`・fault0で完了した。

このfresh poolをP1のpolicy CEMへ接続したが、screen上位候補は独立再評価で再現せず、P1 centerを保持した。BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py`接続は不変である。判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。

## source intake

- config: `configs/meta_specialist/cg_kaggle_kernel_meta_public_more_epoch6c_20260816.json`（初回runs全体scan試行は停止）
- 実運用config生成器: `runs/cg-kaggle-kernel-discovery-20260816-public-more3/build_epoch6c_config.py`
- 正しいconfig: `configs/meta_specialist/cg_kaggle_kernel_meta_public_more_epoch6c_20260816.json`
- intake root: `runs/cg-kaggle-kernel-meta-intake-public-more-epoch6c-20260816/`
- intake pool SHA: `e546951d8e51f78f4bcaaecff23cde229253f4696b15722545170756751db498`
- fresh meta SHA: `20f3e2b0493e92ca3d18f56b7f5540367466b17f8ec189162dcb9662fdb1f6ae`
- accepted: `kaggle_ravi_battlecore_compact_20260816`, `kaggle_naoto_bloodmoon_ursaluna_20260816`, `kaggle_naoto_leafeon_energy_punish_20260816`, `kaggle_naoto_mega_gengar_prize_loop_20260816`, `kaggle_naoto_mega_kangaskhan_speed_20260816`, `kaggle_naoto_slowking_copy_attack_20260816`
- rejected: Naoto Bronzong／Empoleon（`invalid_ace_spec_count`）
- source boundary: all accepted rows are `kaggle_public_kernel`＋`local_eval_only`; authority is false for training／promotion／longrun／submission。
- Naoto sourceはKaggle outputにsubmission archiveが無かったため、取得済みroot `main.py`＋`deck.csv`だけを`repacked-main-deck.tar.gz`へ再パックした。元output directoryとoutput logは保持している。
- safety: source codeのimportはintake中に行っていない。static findingsはaccepted 6件で空、tar member path／link safetyもPASS。

前段で取得した9候補（Mktdev、Jazi、Prvsiyan、Seraria、Rahul等）は、8件が過去source policy identity reuse、またはdynamic/network findingで除外された。Dedquocだけは別のepoch6b sourceとして受理・smoke済みだが、6c CEMには混ぜていない。

## runtime smoke／promotion／split

- smoke root: `runs/cg-kaggle-kernel-meta-smoke-public-more-epoch6c-p1-20260816/`
- smoke seed: `202608969`
- coverage: 6 opponents × 2 seats × 2 games = 24 games
- result: 24/24 `DONE`、fault0、6W-18L、score 25.0%（これはsource smoke qualityであり、P1の提出性能ではない）
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- promoted root: `runs/cg-kaggle-kernel-meta-promoted-public-more-epoch6c-p1-20260816/`
- promoted pool SHA: `0b940f87cd3d073ee42ffab717f2842d08d8f54582ba2a62347c435fd11485a3`
- promoted fresh SHA: `8dfd26927121511e62c62a9fa41de1a04bd513d1081a7354b9c67f241df0b3d5`
- meta manifest SHA: `46d15a44baf3458ce0104174ff99f3f117e024aa1113995ca5fb452ca418dbac`
- split SHA: `d854f72c7541d709dbff0386a9de7ad255ef55670dc6487d5d81ec6205d7e6e6`
- split: META_TRAIN 4（Ravi＋Bloodmoon＋Leafeon＋Mega Gengar）／META_DEV 1（Mega Kangaskhan）／META_FINAL 1（Slowking）
- split loader／source verification: PASS。全行`training_exposure=0`、`local_eval_only`、both seats、fault-inclusive。

## CEM

`runs/cg-self-owned-cg-policy-cem-epoch6c-g01-20260816/`で、self-owned deck-bound package（deck SHA `21620b5f30317f380c020f98672c524ba243b04f180df22830693e8f5acbaff2`）とP1 policy（SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`）を使用した。

- campaign seed: `202608970`
- population／elite: `8／2`
- generation: 1
- META_TRAIN only: 4 refs、2 games／opponent／seat、screen 144 rows
- independent re-evaluation: top c01/c04、2 blocks × 2 games／opponent／seat、各 candidate/control 32 rows
- 全row: `DONE`・fault0
- campaign manifest SHA: `6b02442a14d6fe3608e63aa105a6e6fc8e8eec4af55b18a50ac07d7502b2f7f9`

実際の候補結果は以下の通り。

| candidate | screen delta | independent deltas | 判定 |
|---|---:|---:|---|
| c01 `cg-p1-cem-g00-c01-0db5720d3036` | +25.0pt | −12.5pt / +6.25pt（mean −3.125pt、min −12.5pt） | control非再現、opponent-seat safe false |
| c04 `cg-p1-cem-g00-c04-bfc960d51940` | +12.5pt | −3.125pt / 0pt（mean −1.5625pt、min −3.125pt） | control非再現、opponent-seat safe false |

selectionは`risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`、elitesは`incumbent-center`×2、new centerはP1 defaultのままである。screen上の最大差を採用せず、独立seed・両seat・opponent×seat gateを優先した。

## 次の扱い

- epoch6c TRAINは性能使用済み。source／seed／c01／c04のblind retryは禁止。
- META_DEV／META_FINALはCEM選抜で読んでいないため、未使用holdoutとして保全する。ただし候補が独立positive／seat-safeを満たしていないので、今回はholdout評価を起動しない。
- 次の候補は、Naoto同一作者内相関を下げる別作者・別policy lineageの追加取得、またはself-owned rendererで構造的に異なるpolicy familyを新epoch化する。source smokeとexposure ledgerを先にsealする。
- `cg_bestknown_loop_v1.py`へ接続、P2／P3昇格、deck phase、BestKnown／Champion／production／submission変更は、strict gate全通過まで行わない。

再現コマンド（研究専用、提出・commit・pushなし）:

```bash
PYTHONPATH=.:src .venv/bin/python scripts/generate_kaggle_kernel_meta_v1.py --config configs/meta_specialist/cg_kaggle_kernel_meta_public_more_epoch6c_20260816.json --dry-run
PYTHONPATH=.:src .venv/bin/python scripts/run_historical_meta_smoke_v1.py --pool-root runs/cg-kaggle-kernel-meta-intake-public-more-epoch6c-20260816 --candidate-package runs/cg-self-owned-cg-policy-family-v12-crossed-20260816/p1-source-core --output <new-smoke-root> --base-seed 202608969 --games-per-opponent-seat 2 --workers 12 --timeout-seconds 120
PYTHONPATH=.:src .venv/bin/python scripts/run_self_owned_cg_policy_cem_v1.py --execute --output <new-cem-root> --split runs/cg-kaggle-kernel-meta-promoted-public-more-epoch6c-p1-20260816/cg_historical_split.json --source-package runs/cg-self-owned-cg-policy-family-v12-crossed-20260816/p1-source-core --self-owned-deck-package runs/cg-self-owned-cg-policy-family-v12-crossed-20260816/p1-core-control --control-package runs/cg-self-owned-cg-policy-family-v12-crossed-20260816/p1-core-control --pool-root runs/cg-kaggle-kernel-meta-promoted-public-more-epoch6c-p1-20260816 --generations 1 --campaign-seed 202608970 --population-size 8 --elite-count 2 --reeval-repeats 2 --reeval-games-per-opponent-seat 2 --initial-scale-fraction 0.20
```
