# Autonomous Meta Fine-Tuning 派生 teacher 追加収集（2026-08-13）

## 結論

派生重みへの利用が判断記録で認められた6 teacherのうち、未収集だった
`plamen06_steel`、`nihei_alakazam`、`ozawa_grimmsnarl_v2`、
`ozawa_rocket_v2`について、各96局のcurrent-source trajectoryを新規収集し、
episode単位splitを持つtraining snapshotへsealした。4 corpusはすべて96/96局完了、
fault 0、subject first/second 48/48、unlabelled decision 0、omission 0である。

これはteacher native pairの共通arena強度や提出可否を示す証拠ではない。native code/deckは
`local_eval_only`のままであり、作成物の用途は判断記録
`docs/decisions/2026-08-05-archaludon-teacher-derivation.md` に基づく
自前派生weightの`training-local`入力に限る。

## 収集条件

- games: 96 / teacher
- opponent: pool内の`mean_decision_ms <= 1.0`から固定seed 11で16件
- seat: 各opponent巡回のblockで交互、全体48/48
- workers: 4 / teacher、4 teacher同時実行
- max steps: 2,000
- data boundary: teacherへ渡されたactor-visible observationのみ
- allowed usage: `training-local`
- engine seed: fully deterministicではないため、同seedのpaired比較とは解釈しない

## 結果

| teacher | archetype | W-L | records | train | development | test |
|---|---|---:|---:|---:|---:|---:|
| `plamen06_steel` | `archaludon` | 69-27 | 5,420 | 4,220 | 491 | 709 |
| `nihei_alakazam` | `alakazam` | 51-45 | 8,091 | 5,441 | 1,475 | 1,175 |
| `ozawa_grimmsnarl_v2` | `grimmsnarl_froslass_munkidori` | 65-31 | 7,808 | 5,260 | 1,088 | 1,460 |
| `ozawa_rocket_v2` | `rocket_mewtwo_spidops` | 53-43 | 6,048 | 4,554 | 615 | 879 |

各manifestの`matchup_record_counts`は16件を持ち、その合計は`records_written`および
実JSONL行数と一致した。各snapshotは`read_training_snapshot_v1`でshardのclosed schema、
self-hash、split count、episode/near-duplicate groupingを再検証した。

## 一次artifactとSHA-256

### plamen06_steel

- collection manifest:
  `runs/meta-specialist-teacher-records/archaludon-teacher-plamen06-96-autonomous-20260813/teacher_dataset_manifest.json`
  - `2084255352caba9fe8c7127010833ca84af1fa3c6efe75a766c36b4aa0a20348`
- dataset chunk: `a5c21e48301388be9e817a6099fdff81fe2707526bb52e62462169ed2551ce40`
- snapshot index: `10a10c2a95fb66fabe7177303c750cecf2fcb8061bc941d74c50b38db7543bd9`
- snapshot shard file: `2ef8e15808336ea39143f74abaa6e37ee4149ce11a5c56af5b53a42d994c65a3`
- snapshot ID: `3980aad9e029b6df148a39c4c5059ca30244629d29cf3b44845f956d40454879`

### nihei_alakazam

- collection manifest:
  `runs/meta-specialist-teacher-records/alakazam-teacher-nihei-96-autonomous-20260813/teacher_dataset_manifest.json`
  - `f5a0fafc0ff1cf389d51e35728f20a5b2f602944eb9e8657d9173cf181b727d2`
- dataset chunk: `bc301ad1cde4fda2619605602cc4fac2b6673f74ad4b2a180f917162a85aeebb`
- snapshot index: `ed47d0c0f3622465d4e43e64759082923e749fce11b66f8766566c369fe70df7`
- snapshot shard file: `583a9bc3d4c62fa53da26450a3eba91d5d75886f2ea49b7a93051dce41ed50da`
- snapshot ID: `048292732933eb40c469980fe829dc9ac9e7d57d93b9660bada85e7c62197172`

### ozawa_grimmsnarl_v2

- collection manifest:
  `runs/meta-specialist-teacher-records/grimmsnarl-teacher-ozawa-v2-96-autonomous-20260813/teacher_dataset_manifest.json`
  - `fb6fa69f42ae8877819321f9f5d9d806367cdcab6597341dfa857df6cae1e844`
- dataset chunk: `2cb5d12715d8a6cc1d7aedca9e2ef91bae236a417b173437a9b7aec1e4396970`
- snapshot index: `b907594b2a0927e511e7add4780e9d76e456f630830444457acc1c8fae6392b1`
- snapshot shard file: `d8d0a89f3079f3384c1c9ebd662f9f1d92480cc9313a4f286982ef0cd2c97513`
- snapshot ID: `ea99ad41a5155eead57c499afa5c18ca35b8347bb0dcd752908405a0fb363caa`

### ozawa_rocket_v2

- collection manifest:
  `runs/meta-specialist-teacher-records/rocket-teacher-ozawa-v2-96-autonomous-20260813/teacher_dataset_manifest.json`
  - `271ecd0a41ca114b551c5f2be5572a883484cd68c50abfcd7186423ee1307d58`
- dataset chunk: `a1462f3929d73ec589ea817ac855bf0c4045203a86406bb63d0c637e39bf3384`
- snapshot index: `a88989193b817c737b4cf0d0712cc84ee9a55300e5085883b4fb47026f2a7f1a`
- snapshot shard file: `2b1a846a080a59af57c145fc3e74feab41829e6712a1915e9d84b203a58e38af`
- snapshot ID: `6a5bd4ec79939cdb28052f82a6e5faa676934b7a68f76b2d46253c4096ab8ec8`

## 収集中に検出したcollector障害と修正

niheiの初回並列収集は95/96局時点で、workerごとに実行されるCABT deck qualificationが
一時的にnon-DONEとなり、`DeckQualificationError`を親へ伝播してrun全体を中断した。
同じdeckは親probe、既存95 worker、追加32並列probeで全て合法だったため、deck不正ではなく
game worker bootstrapの一時faultであることを切り分けた。

合わせて次の2件を実測で発見した。

1. resume後manifestが既存recordを`games_completed`と`seat_counts`へ加えていなかった。
2. parallel workerの`n_records`を`matchup_record_counts`へ加えておらず、新規完走runの
   matchup分布が空になっていた。

`collect_teacher_records_v1.py`を最小修正し、qualification例外を該当gameのfault rowとして
記録、resume総数を既存＋新規で集計、parallel matchup countをmergeするようにした。
CABT legalityの判定条件は変更・緩和していない。niheiは既存95局を保持して不足1局だけを
resumeし、最終96/96、fault 0となった。他3manifestもrecordから再構築した。

## 検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src TMPDIR=/tmp \
  .venv/bin/python -m pytest -q -s \
  tests/meta_specialist/test_teacher_collection_caps_v1.py \
  tests/meta_specialist/test_teacher_dataset_v1.py \
  tests/meta_specialist/test_teacher_projection_roundtrip_audit_v1.py
```

結果: `15 passed, 1 skipped`。

追加した回帰oracleは以下である。

- qualification failureがrun全体の例外ではなく1 game fault rowになる
- 95 record＋1新規成功のresume manifestが96 completed・48/48 seatになる
- parallel worker rowがmanifestのmatchup record countへ入る

## 次の境界

6 teacher catalogを全件`READY`へ更新し、source/catalog/snapshot SHAを固定する。その後、
選択契約を欠落させないgeneric Student θ0 datasetと、episode cross-fitted actor-visible
state valueによるAWR weightを生成する。native code/deckの提出利用やChampion変更を
この収集完了から推論してはならない。
