# Outcome-only deck/policy alternating loop v1（2026-08-14）

## 結論

既存の単発 `POLICY_FIXED_SHORT` stage と `DECK_FIXED_LONG` stage を、1回の bounded iteration として接続する research-only 実行入口を追加した。最初にデッキ候補を親controlと比較し、positive stageのときだけ、同じ候補デッキを固定して policy candidate と policy control を比較する。fault、seat、paired-strata、source SHAの既存stage gateを再利用し、negativeまたは未実行時は次のphaseを起動しない。training、promotion、submission、longrun authorityは常にfalseである。

## 実装

- module: `src/mage_ptcg/meta_specialist/outcome_only_alternating_loop_v1.py`
  - SHA `a2b65e08e5992e3b3745a4786747b71e1c3b937ec6c01c5d1e5044d384513ac9`
  - `POLICY_FIXED_SHORT` は policy identityを固定し、deck identityだけを変更
  - `DECK_FIXED_LONG` は deck identityを固定し、policy/config identityだけを変更
  - 96→384→768→1536のstage列以外を拒否
  - positiveなdeck phase以外ではpolicy phaseを起動しない
- CLI: `scripts/run_outcome_only_alternating_loop_v1.py`
  - SHA `ce60634a96fbe30fb038d19cb3fff787288eb013c5374c28c0d36a79608f5d33`
  - workers既定 `12`、worker recycle既定 `16`
  - fresh root、atomic no-clobber iteration manifest
- tests: `tests/meta_specialist/test_outcome_only_alternating_loop_v1.py`
  - SHA `17755055b75dcf6a29ac0d1e0ebe79cf2642217c005d59cc1b8d2e6b66744821`

## 確認

実際のCABTを起動しないdry-runで、既存のroot Rule v0 parentと既存candidate deckを入力に、deck phaseとpolicy phaseのidentityをmaterializeした。

- root: `runs/final-sprint-autonomous/outcome-only-alternating-loop-dryrun-v3-20260814/`
- iteration manifest SHA `913a10469f7656c8d904f6c8afed705196529f98a040e17a2c2def8e5b707518`
- stage: `POLICY_FIXED_SHORT`, 96 slots, `execution_started=false`
- policy phase: dry-runでは未起動（実測positive gateを要求）
- authority: execute/training/promotion/submission/longrun 全false

検証結果:

- focused alternating loop + existing runtime tests: `10 passed`
- `py_compile`: PASS
- `git diff --check`: PASS
- dry-run時点の実性能run: 未起動

## Fresh performance screen（epoch 1）

既存candidateを再実行せず、META_TRAIN重み付き生成から得た新規deck候補2件を、同じRule v0 parent control・別seed・workers12/recycle16で1件ずつscreenした。どちらも `POLICY_FIXED_SHORT` のdeck phaseで負差となったため、契約どおり `DECK_FIXED_LONG` policy phaseは起動していない。全192局はDONE、fault0、draw0、各arm seat 48/48であり、authorityは全falseのままである。

| candidate | mutation | candidate deck SHA | candidate W-D-L | control W-D-L | delta | 判定 |
|---|---|---|---:|---:|---:|---|
| `eaccae4d585650b6513e2f71e561e16d2fa5e50a92ea284085c0999441e8e442` | `1142→3` | `8761e3f747edd2873d48a479db2b1db0e6358cb6b697cfde391a51aa25b8c7d7` | 10-0-86 / 96 | 17-0-79 / 96 | −7.2917pt | STOP / candidate-only |
| `3221d4614edf7f7dd11dcd3d8af884641d9a90657b18ee3641c4e1ceb009cdbd` | `1141→1086` | `7f52ae1f4a2523e5ea66f8304e8e0a9a5fbc30bd71adfd8894d890b119724136` | 12-0-84 / 96 | 13-0-83 / 96 | −1.0417pt | STOP / candidate-only |

共通設定は root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、root policy SHA `806284f8f03d974fdb8e8dd6020c1e6dd25d7936430119e8c2b8baa1d973eef7`、reference config `configs/meta_specialist/performance_first_broad_pool_v1.json`、workers `12`、worker recycle `16`。候補source manifest SHAは `e57dd433f840428069bdf9777200e1f92a164c40386601290982728faf534108`。

- `eacc...` root: `runs/final-sprint-autonomous/alternating-loop-epoch1-eacc-96-20260814/`
  - deck manifest SHA `0f9354954262a674132b797c1fc8519860e3ef826c02ade8cd85438c3770dbdf`
  - iteration SHA `0b9533a3c4dd55de65fe23219eeff74a197f41860695870b2e50a9193636125e`
  - iteration file SHA `e3996ce24a89b59102f8a6847e5836cc76a63b6d18babcf6ab8d4576e23275c8`
- `3221...` root: `runs/final-sprint-autonomous/alternating-loop-epoch1-3221-96-20260814/`
  - deck manifest SHA `a0383515d1e39c4338e6d6f6665e9497cb2d233f2191ea1147763ca4b52f1615`
  - iteration SHA `d42e95f61636315eda15784162b99b70dfa407375ce7207c8d6616c9f11e5fd8`
  - iteration file SHA `5317b2a46ab7499852025cc2804bf8d91f1ff98511997af3094b5cc83bc3ec3c`

このepochではpositive deck phaseが存在しないため、384確認・policy更新・training・promotion・longrun・submissionへは進めない。同じ2候補の再実行もしない。

## 次の使い方

新しいdeck candidateと、同じ候補deckへ束縛したpolicy candidate/controlを用意した後、次のように実行する。既定値は速度優先のworkers12/recycle16であり、必要な場合だけCLIで変更する。

```bash
PYTHONPATH=.:src .venv/bin/python \
  scripts/run_outcome_only_alternating_loop_v1.py \
  --output-root runs/final-sprint-autonomous/<fresh-loop-root> \
  --deck-candidate-id <deck-candidate> \
  --deck-candidate-main <policy-main.py> \
  --deck-candidate-deck <candidate-deck.csv> \
  --native-control-id <native-control> \
  --native-control-main <control-main.py> \
  --native-control-deck <control-deck.csv> \
  --policy-candidate-id <policy-candidate> \
  --policy-candidate-main <policy-candidate-main.py> \
  --policy-candidate-deck <candidate-deck.csv> \
  --policy-control-id <policy-control> \
  --policy-control-main <control-main.py> \
  --policy-control-deck <candidate-deck.csv> \
  --reference-config configs/meta_specialist/performance_first_broad_pool_v1.json \
  --base-seed <disjoint-seed> --execute
```

これは自動promotionや無制限longrunではない。新しい候補の性能が確認できた場合のみ、次のstageを別fresh rootで明示的に起動する。
