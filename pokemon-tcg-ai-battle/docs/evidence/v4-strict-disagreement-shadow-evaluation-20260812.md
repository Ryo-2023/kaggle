---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-12
---

# V4 strict disagreement / shadow evaluation evidence

## 結論

strict disagreement の抽出経路と、固定済み shadow pool の同条件評価経路を実装・検証した。GPU が利用できないため、このターンでは新しい strict-disagreement BC 学習は実行していない。比較可能な既存 Wave4 strict-paired candidate と Wave6 baseline を、同じ subject deck・両 seat・同じ base seed・max steps で shadow pool に流した結果は、4 games/相手/seat の診断値で candidate 65/96 (67.71%)、baseline 60/96 (62.50%)、差 +5.21 ポイントだった。ただし seed 0 は -1/48、seed 1 は +6/48 と非対称で、セルあたり4局に過ぎないため promotion gate や Champion 変更の根拠にはしない。

## 1. strict disagreement の定義と実装

`strict_disagreement_metadata_v4` は、記録済み transition の各 public prefix について、学生が実際に選んだ legal-domain index と、同じ prefix chain を teacher で relabel した target index を比較する。teacher が最初に異なる token を選んだ後の counterfactual state は生成しない。eligible transition が1件でもある場合は、その game の全 transition を recurrent episode として保持する。

この定義により、次を分離して監査できる。

- `disagreement`: 学生と teacher の public prefix target が異なるか。
- `eligible`: action-type focus と mean behavior log-probability 閾値を含む選定条件を満たすか。
- `effective_loss_mass`: relabel 済み step の `reach_mass * quality_weight`。
- forced sole STOP は teacher query を作らず、forced-stop disagreement として数えない。

主な変更対象は `src/mage_ptcg/meta_specialist/dagger_v4.py` と `scripts/run_meta_specialist_v4_dagger_bc.py`。BC runner には `--strict-disagreement-targets`、`--strict-disagreement-action-types`、`--strict-max-mean-behavior-log-probability` を追加し、seed ごとの screen/checkpoint provenance と strict selection report を保持する。paired seed の training material は seed 間で取り違えないよう、seed 固有の input binding を解決してから学習する。

## 2. Wave6 seed1 Screen の offline strict 抽出

入力は `runs/meta-specialist-v4-archaludon-dagger-wave6-screen-seed1-v2/` の fault-free VALID screen である。

| 項目 | 値 |
|---|---|
| screen SHA-256 | `aed7438628ffdcb4a4d0c11a844c6ddea4a75017be44912180e3f6dc90abf1f1` |
| transitions SHA-256 | `2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26` |
| games / transitions | 96 / 5,590 |
| teacher policy version | `b89ca316191957b26e5afa37c6cd121f61ba43435724aa6b982b3b06b07ff6e` |

全 action type・閾値なしの広い監査では、3,592 transition が disagreement、effective loss mass は 3,707、96/96 game が少なくとも1件の disagreement を持った（train 69、validation 27、seat 0/1 は各48）。結果は [broad-report.json](../../runs/meta-specialist-v4-strict-disagreement-wave6-seed1/broad-report.json) に保存した。

既存の弱点行動に絞り、teacher target action type `9,13,14` と mean behavior log-probability `<= -0.2` を適用した場合は、91 game、985 eligible transition、effective loss mass 985（train 65、validation 26）となった。結果は [action-9-13-14-threshold-m02.json](../../runs/meta-specialist-v4-strict-disagreement-wave6-seed1/action-9-13-14-threshold-m02.json) に保存した。両レポートで forced-stop disagreement は0である。

## 3. shadow pool freeze

shadow cohort は、既存 fixed-six と identity が重ならない public/local-eval-only opponent 6件を選び、source manifest の SHA を固定した。

- manifest: [shadow_pool_manifest.json](../../runs/meta-specialist-v4-shadow-pool-20260812/shadow_pool_manifest.json)
- manifest SHA-256: `6ddaf3588bb22869a808fd75f84721b640dde6d75f665a11beb10f578af72107`
- source `opponents/pool_manifest.json` SHA-256: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- IDs: `aristophanivan_multiply`, `kiyotah_abomasnow`, `masamikobayashi_garchomp`, `naoto714_kangaskhan`, `naoto714_slowking`, `yaminh_agent`

freeze は identity 分離を保証するだけで、強度・汎化・fault 0 を保証しない。各 candidate の deck/policy/source bytes と pool registry identity は評価開始前に再検証した。

## 4. 同条件 shadow 評価

評価スクリプトは [measure_v4_checkpoint_strength_shadow.py](../../scripts/measure_v4_checkpoint_strength_shadow.py)。fixed-six evaluator を変更せず、manifest に列挙された6件だけを使う。条件は subject deck `opponents/public_archaludon_cinderace_r7/deck.csv`（SHA-256 `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`）、両 seat、各 opponent×seat 4局、base seed `10100000`、max steps `2000`、protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba` で揃えた。

| arm | seed | wins / 48 | score | faults |
|---|---:|---:|---:|---:|
| Wave6 baseline | 0 | 30 | 62.50% | 0 |
| Wave6 baseline | 1 | 30 | 62.50% | 0 |
| strict-paired candidate | 0 | 29 | 60.42% | 0 |
| strict-paired candidate | 1 | 36 | 75.00% | 0 |
| 合計 baseline | — | 60/96 | 62.50% | 0 |
| 合計 candidate | — | 65/96 | 67.71% | 0 |

出力は [baseline-seed0-48.json](../../runs/meta-specialist-v4-shadow-eval-20260812/baseline-seed0-48.json)、[baseline-seed1-48.json](../../runs/meta-specialist-v4-shadow-eval-20260812/baseline-seed1-48.json)、[strict-paired-seed0-48.json](../../runs/meta-specialist-v4-shadow-eval-20260812/strict-paired-seed0-48.json)、[strict-paired-seed1-48.json](../../runs/meta-specialist-v4-shadow-eval-20260812/strict-paired-seed1-48.json) に保存した。4本すべて `comparison_status=valid`、fault 0 である。各ファイルには checkpoint file/tensor SHA、opponent deck/policy/source fingerprint、seat/opponent の内訳を含めた。

これは既存 Wave4 strict-paired の fixed-six 192局結果（candidate 101/192 対 baseline 93/192、+4.17ポイント）とは別の shadow cohort である。shadow の +5.21ポイントは方向性の参考にはなるが、seed 間の反転と少数局のため、事前に定めた promotion gate を通過したとは扱わない。

## 5. 検証と未実施項目

実行済み:

```text
45 passed
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/meta_specialist/test_dagger_v4.py \
  tests/meta_specialist/test_run_meta_specialist_v4_dagger_bc.py \
  tests/meta_specialist/test_run_meta_specialist_v4_dagger_screen.py \
  tests/meta_specialist/test_measure_v4_checkpoint_strength.py \
  tests/meta_specialist/test_measure_v4_checkpoint_strength_shadow.py
```

`py_compile` と `git diff --check` も pass した。CUDA はこの環境で利用できず（`torch.cuda.is_available()` false、NVML unavailable）、新規 strict-disagreement BC の2 seed学習、そこから生成した fresh checkpoint の fixed-six/shadow 評価は未実施である。

## 判定

現 Champion、提出契約、Kaggle submission は変更しない。次に再開する場合は、GPU 上で action-type strict arm を2 seed学習し、まず validation と fixed-six 192局、次にこの frozen shadow pool 48局/arm で再評価する。両 seed の非悪化、seat guardrail、相手別安定性、fault 0 を同時に確認できるまで長時間学習へ進まない。
