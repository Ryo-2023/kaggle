# cg factorial behavior-family source / CEM — 2026-08-15

## 結論

可視状態の異なる優先度軸を直積にする factorial recipe を実装し、別deck／別sourceの2 epochを安全にCEMへ接続した。Alakazam epoch `t` と Comfey epoch `u` は、source seal、fresh split、両seat smoke、CEM、独立再評価まで fault 0 で完了した。ただし、独立性・seat安全性・fresh transferを同時に満たす候補は得られず、P1 `cg-lethal-target-v1`、root deck、BestKnown、Champion、productionは変更していない。

この実験は `local_eval_only` の相関proxyであり、public/native性能やnative上位72%到達の証拠ではない。factorial generatorは「新しいmeta sourceを生成し、既存の `cg_bestknown_loop_v1.py` 前段へ渡せる形式へ封印する」実装手段としては検証できたが、BestKnownを更新する性能源はまだ得られていない。

## 実装

- `src/mage_ptcg/opponent_ingest/behavior_factorial_meta_v1.py`
  - AlakazamのPokemon優先度軸とsetup/item軸を合成する4 variant。
  - Comfeyのdeckout reserve軸とvisible setup優先度軸を合成する4 variant。
  - 既存のexact replacementとseal helperだけを使い、deck・観測境界・static scanを保持。
- `scripts/generate_factorial_behavior_family_meta_v1.py`
  - `--family alakazam|comfey`、source epoch、seed namespace、P1 package、scan rootをhash-boundに受ける。
- `tests/test_behavior_factorial_meta_v1.py`
  - composition、recipe、未知variantのfail-closed契約を検証。

いずれもcandidate生成・Champion変更・submission権限を持たない。

## Epoch t: Alakazam factorial

baseは `internal_nihei-cynthias-garchomp_3818c21f59b6`（source commit `3818c21f59b6f3b37e63a2637a1cfe4a5aa3226a`、canonical deck SHA `4c364e3602ee174a09ea876e8b529162e753fa6cae337b4bd77c0dbf04da6edf`）。次の4 recipeをsealした。

- `ABRA_FIRST+POFFIN_FIRST`
- `ABRA_FIRST+FEZANDIPITI_DRAW_FIRST`
- `DUNSPARCE_FIRST+POFFIN_FIRST`
- `DUNSPARCE_FIRST+FEZANDIPITI_DRAW_FIRST`

artifact:

- root: `runs/cg-alakazam-factorial-meta-20260815-t/`
- pool SHA: `5e6a753faae421e0c72242611a3cc1244781a02928ef3fddf35ae4506806a42b`
- fresh meta SHA: `3618179ac14a1e804731463cfaddbffe7fd8bbb997fe8e94381cfc6bbac9611b`
- split SHA: `a018ad5a0ce173dfb2bf0300a62950fc050e53051009379bbd9a849f0632f402`
- intake report SHA: `9cb623b3cfb5532a45c3af9cc272643a3884c7d3f8cbb0ca9884342d72ec4ea6`

P1 controlの2 variant smokeはbase seed `20260939`で8/8 `DONE`、fault 0（2勝6敗）。risk-aware CEMはcampaign seed `20260940`、population 8、elite 2、2世代、独立再評価2回で272局すべて`DONE`・fault 0だったが、両世代とも`incumbent-center`（P1）を保持した。fresh DEVのcenterはcandidate 7/16、control 8/16、delta `−3.125pt`。新policyへの更新、FINAL昇格は行っていない。

主要CEM artifactは `runs/cg-alakazam-factorial-cem-20260815-t/`（manifest SHA `e14ccc2aebc085bb52df67b04ad0fdea4aa2c7a876bf27823c1e58c088f86116`）。

## Epoch u: Comfey factorial

baseは `internal_nihei-hydreigon-deckout_c8430334ca23`（source commit `c8430334ca23932f787c3873c266734bc13cd4b0`、canonical deck SHA `c07af566f685866476a258d65f5e6e3bfe5656e82a696236a4f83ff365bae9f9`）。次の4 recipeをsealした。

- `DECKOUT_AGGRESSIVE+COMFEY_SETUP_FIRST`
- `DECKOUT_AGGRESSIVE+LITWICK_SETUP_FIRST`
- `DECKOUT_CONSERVATIVE+COMFEY_SETUP_FIRST`
- `DECKOUT_CONSERVATIVE+LITWICK_SETUP_FIRST`

artifact:

- root: `runs/cg-comfey-factorial-meta-20260815-u/`
- pool SHA: `ea7909050ec3bfcbea7384d10658f9e6b5bf48d18f2ef0c8706dc29acbe7042e`
- fresh meta SHA: `e9cc9e53d17a7f07f458adba2b1bcf8d56b0f6fab7fa29cbea5a7c6eae87a4a9`
- split SHA: `d4c77561a731b97abf577a57da57940548c97107f5cd63ec784121e9608768cc`
- intake report SHA: `900a024c70f82ee0152f151f5ebbfc56aabda627c96021404d2b9d92e142990d`

P1 controlの2 variant smokeはbase seed `20260943`で8/8 `DONE`、fault 0（4勝4敗）。CEMはcampaign seed `20260944`、population 8、elite 2、2世代、独立再評価2回で272局すべて`DONE`・fault 0だった。

generation 1 candidate `cg-p1-cem-g01-c05-796b8f2986f4`（config SHA `796b8f2986f4e5351a4598e852c68453d53ebdc7bcc0148f43aff17c6481b7a0`、policy SHA `43ff265d85af61cc746438ffff6737d13a9e8c0fd5c5d416ceb600a4c74c7a0b`）は、独立2 blockで各 `+25.00pt`、fault 0だった。しかし opponent別 seat gap は `50.00%` と `25.00%`、`seat_safe=false`であり、positive-delta gateを通過しない。従ってP1 centerは保持した。

転移診断として未使用 `META_FINAL` をbase seed `20260945`で64局（候補／P1各32局、両seat各16局）評価した。候補は `13W-0D-19L`（40.625%）、P1は `17W-0D-15L`（53.125%）、差 `−12.50pt`、fault 0、候補 seat gap `6.25%`、判定 `NOT_PROMOTABLE`。一次artifactは `runs/cg-comfey-factorial-final-20260815-u/`（summary SHA `9a9a8dde752c9e2105b1dc9bf67a1e6feb11139293216e7843546e18a515dbd9`）。

主要CEM artifactは `runs/cg-comfey-factorial-cem-20260815-u/`（manifest SHA `52ea0903e66bdf1728cdb8304869ae1253efa9f43ece9ccbdd4d35fd8ff06b14`）。

## 新しいsourceの獲得状況

factorialとは別に、remote tracking refから安全な新sourceを取得する監査を実施した。

| intake | accepted | rejected | 判定 | 主な理由 |
|---|---:|---:|---|---|
| `runs/cg-source-audit-20260815-r/` | 0 | 200 | `BLOCKED_NO_SAFE_CANDIDATES` | artifact identity再利用、environment access、dynamic import、source commit再利用、deck不整合 |
| `runs/cg-source-audit-20260815-s/` | 0 | 48 | `BLOCKED_NO_SAFE_CANDIDATES` | artifact identity再利用、source commit再利用 |

したがって、現在利用可能な新sourceは「外部refから安全に得られたもの」ではなく、既存許可snapshotからの相関proxyだけである。public/native metaの代替として扱わない。

## 現在の判定と次のresearch gate

現行BestKnownは次のままである。

- policy: `cg-lethal-target-v1`、SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- P1 package local closure: PASS、ただし remote Submit contract未確認のため `submission_ready_candidate=false`

次に進む条件は、factorial variantのblind retryではなく、以下のいずれかで新しいmeta source familyを構造的に増やすことである。

1. 明示許可済みで、既存artifact／source commit／policy SHAと重複しない新snapshotを得る。
2. source runtimeがbounded（両seat smoke fault0）である別deck／別behavior familyを採取する。
3. 複数source familyを同一CEM poolへ混ぜ、source-familyごとのseat gapとlower-tailを分離して評価する。

このgateを通過したsourceだけを、P1 → policy CEM → fresh DEV/FINAL → deck → policy の順に `cg_bestknown_loop_v1.py` へ接続する。commit、push、Champion変更、Kaggle提出は明示許可があるまで実行しない。
