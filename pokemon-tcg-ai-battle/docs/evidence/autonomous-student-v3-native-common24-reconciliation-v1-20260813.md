# Student v3 / native Tomato common24 reconciliation v1

## 結論

Student v3 candidate と native `tomatomato_archaludon` の**別々の CABT
ledger**を、同一 common24 protocol の証拠として扱えるかを fail-closed で
判定する postprocess utility を実装した。96 / 384 は screen、768 / 1536 は
最低 2 independent blocks を要求する long-run gate として機械判定する。
候補の差が小さい、0、または負であること自体は自動棄却理由にしない。

この作業では CABT、学習、package build、Champion 変更、commit、push、提出を
実行していない。2026-08-13 の検証時点では、formal Student v3 candidate artifact
と、それを native Tomato と比較した fresh common24 ledger がまだ揃っていないため、
**実候補の reconciliation result は未生成**である。実装・契約・synthetic fixture
による gate oracle は利用可能である。

## 追加したファイル

| 役割 | パス | SHA-256 |
|---|---|---|
| strict consumer / API | `src/mage_ptcg/meta_specialist/student_v3_native_common24_reconcile_v1.py` | `6954e290622c0345bbafccda80781852ba0736f14fe740056c941a63b45ddc66` |
| CLI | `scripts/reconcile_student_v3_native_common24_v1.py` | `14f897cbd9be24c3fcbd5459f105f608b5a8061d5bdc97718b201d32a6623269` |
| focused tests | `tests/meta_specialist/test_student_v3_native_common24_reconcile_v1.py` | `0c4ecb55ea3ffd0fe72dbae2cc89ec13ab00301b0beaca2c11df10a123df2ce8` |

上記 3 ファイルと本 evidence は新規ファイルであり、既存 evaluator、runner、GPU
dataset、candidate builder は編集していない。

## 公開 API と CLI

Python API:

```python
from mage_ptcg.meta_specialist.student_v3_native_common24_reconcile_v1 import (
    reconcile_student_v3_native_common24_v1,
    write_student_v3_native_common24_reconciliation_v1,
)

report = reconcile_student_v3_native_common24_v1("request.json")
written = write_student_v3_native_common24_reconciliation_v1(
    "request.json",
    "reconciliation.json",
)
```

CLI:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/reconcile_student_v3_native_common24_v1.py \
  --request /absolute/path/to/request.json \
  --output /absolute/path/to/reconciliation.json
```

既存 output は既定で拒否する。再検証後に置換する場合だけ `--overwrite` を明示する。
出力ファイルは canonical JSON、末尾 newline なし、atomic replace で公開される。CLI
stdout は status、artifact SHA、semantic reconciliation SHA、target、score delta、
authority の bounded summary だけである。

## request artifact 契約

request schema は
`meta-specialist-student-v3-native-common24-reconcile-request-v1` で、最上位と
主要 nested object は closed schema である。必須 binding は次の通り。

- common24 reference config の path / file SHA と exact 24 opponent IDs
- `engine_seed_supported=false`
- `pairing=independent_stratified_not_game_paired`
- `max_steps`、`timeout_seconds`、evaluator implementation SHA
- formal Student v3 candidate artifact の path / file SHA、candidate ID、policy identity
  SHA、deck SHA、固定 runner ref
- native Tomato の raw policy path / SHA、deck path / SHA、固定 runner ref
- comparison block ID、arm ごとの block ID、base seed、timeout、runner ref
- arm ごとの `ledger.jsonl`、`manifest.json`、`summary.json` の path / file SHA
- target games per arm は exact `96 / 384 / 768 / 1536` のいずれか
- authority は `training / promotion / submission / longrun` の全てが exact `false`

Student v3 artifact は既存 formal loader
`load_student_v3_candidate_artifact_v1` で再検証し、そこから導出される
`candidate_id / policy_identity_sha256 / deck_sha256` と request、全 candidate row を
一致させる。native は `tomatomato_archaludon` に固定し、raw policy/deck bytes の
SHA と request、全 native row を一致させる。native env / score bias は空でなければ
拒否するため、tuned native を control と誤認しない。

## ledger / manifest / summary の照合

各 paired block について、次を全て満たす必要がある。

1. reference config の順序で `24 opponents × 2 seats × repetitions` が exact 1 回ずつ
   存在する。
2. 行順と manifest `game_ids` が exact に一致し、game ID は全 arm / block を通じて
   unique である。
3. seed は arm ごとに `base_seed + ordinal` と一致する。block 間の seed set overlap
   は拒否する。
4. candidate と native の対応 stratum で opponent identity が exact に一致する。
   同一 opponent の policy/deck identity が seat/repetition/block 間で変化しても拒否する。
5. `max_steps`、evaluator SHA、engine seed contract、runner declaration、timeout
   declaration が request と一致する。
6. subject policy/deck identity と row metadata provenance が candidate/native artifact
   closure と一致する。
7. non-fault row は `status/raw_status=DONE` かつ outcome と winner/seat が一致する。
   fault row は `status=FAULT` である。
8. manifest requested count / game IDs / block IDs / completed / faults と ledger を再集計
   した値が一致する。
9. summary の requested denominator、W/D/L/fault、score rate、fault rate が ledger の
   再集計と一致する。

出力 block receipt は、arm ごとの base seed、seed min/max、exact seed-set SHA、exact
game-ID-set SHA、requested count、および ledger/manifest/summary path+SHA を保持する。
seed は CABT engine RNG の common-random-number pairing を意味せず、独立 stratified
schedule の provenance である。

## fault-inclusive 集計と seat gap

score は次の requested-game denominator で再計算する。

```text
score_rate = (wins + 0.5 * draws) / requested_games
requested_games = wins + draws + losses + faults
```

fault は 0 点として分母へ残す。candidate/native それぞれについて overall と seat 0 / 1
の W/D/L/fault/score を出し、次も保存する。

- signed `seat_gap_score_rate = seat0_score - seat1_score`
- absolute seat gap
- overall candidate minus native score rate / wins
- seat 0 / seat 1 ごとの candidate minus native score rate

## gate oracle

| target / arm | block 要件 | fault 0 の status | mechanical eligibility |
|---:|---|---|---|
| 96 | 1 以上 | `SCREEN_COMPLETE_CONTINUE` | false |
| 384 | 1 以上 | `SCREEN_COMPLETE_CONTINUE` | false |
| 768 | seed-disjoint な 2 以上 | `LONGRUN_REVIEW_READY` | true |
| 1536 | seed-disjoint な 2 以上 | `FINAL_REVIEW_READY` | true |

1 件でも fault があれば stage に関係なく `BLOCKED_FAULTS`、mechanical eligibility
false になる。protocol mismatch、欠落、重複 game ID、seed mismatch、identity mismatch、
denominator mismatch は malformed evidence として例外で fail-closed にする。

`promotion_gate_eligible=true` は「構造的に long-run evidence review へ進める」という
意味だけである。`promotion_authority` は常に false であり、性能差の採用判断、Champion
変更、package、submission を許可しない。candidate delta の符号や大きさだけで自動
NO-GO にせず、`performance_auto_reject=false` と human performance decision required
を明示する。

## TDD と検証結果

### RED 1: core module

先に focused test を追加し、未実装 module import が失敗することを確認した。

```text
ModuleNotFoundError:
No module named 'mage_ptcg.meta_specialist.student_v3_native_common24_reconcile_v1'
```

### RED 2: CLI

core GREEN 後、CLI test を先に追加し、未実装 script import が失敗することを確認した。

```text
ModuleNotFoundError:
No module named 'scripts.reconcile_student_v3_native_common24_v1'
```

### GREEN: focused

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q \
  tests/meta_specialist/test_student_v3_native_common24_reconcile_v1.py
```

```text
20 passed in 0.48s
```

focused tests は 4 exact stages、small negative delta、fault-inclusive block、missing
stratum、duplicate game ID、seed/max-steps/evaluator/winner/opponent/subject/denominator/
timeout tamper、long-run 1-block misuse、block seed overlap、canonical atomic writer、CLI
を含む。

### GREEN: nearby regression

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q \
  tests/test_parallel_cabt_evaluator_v1.py \
  tests/meta_specialist/test_run_student_v3_set_candidate_pilot_v1.py \
  tests/meta_specialist/test_run_native_policy_candidate_pilot_v1.py \
  tests/meta_specialist/test_run_deck_mutation_common_protocol_v1.py \
  tests/meta_specialist/test_student_v3_native_common24_reconcile_v1.py
```

```text
48 passed in 17.54s
```

共有 workspace で最初の nearby run 中に、別 lane が同時更新していた未追跡 Student v3
runner の telemetry test が 1 件だけ一時的に失敗した。同一 test を現行 bytes で単独
再実行すると `1 passed`、最終の上記 full nearby rerun は `48 passed` だった。今回の
lane は当該 runner/test/runtime を編集していない。

追加確認:

```bash
python -m py_compile \
  src/mage_ptcg/meta_specialist/student_v3_native_common24_reconcile_v1.py \
  scripts/reconcile_student_v3_native_common24_v1.py

git diff --check -- \
  src/mage_ptcg/meta_specialist/student_v3_native_common24_reconcile_v1.py \
  scripts/reconcile_student_v3_native_common24_v1.py \
  tests/meta_specialist/test_student_v3_native_common24_reconcile_v1.py
```

いずれも exit 0。

docs 構造検証も fresh 実行し、`Validated 13 canonical documents.`、exit 0 を確認した。

## 実 artifact 生成の再開条件

実 reconciliation を作るには同一 evaluator closure で、各 stage について次が必要。

1. formal verify 済み Student v3 candidate artifact
2. candidate common24 ledger/manifest/summary
3. exact native Tomato common24 ledger/manifest/summary
4. 両 arm の base seed、timeout、runner ref、target、file SHA を閉じた request JSON
5. 768 / 1536 では各 arm に seed-disjoint な最低 2 blocks

古い native ledger を fresh Student candidate ledgerへ無条件に混ぜてはならない。
evaluator SHA、reference config、opponent identity、max steps、timeout declaration のどれかが
一致しなければ新しい native control を同じ protocol で再実行する。

## 残リスク

1. evaluator-v1 row/manifest は `timeout_seconds` と `runner_ref` を保存しない。この
   utility は request と arm declaration の一致を検証するが、ledger bytes だけから実際に
   適用された timeout/runner を観測証明できない。将来は両値または pre-run plan SHA を
   evaluator row/manifest metadata へ保存すべきである。
2. `engine_seed_supported=false` のため seed set は schedule provenance であり、candidate/
   native の game-level paired RNG を保証しない。比較は independent stratified である。
3. 96→384→768→1536 の各 exact stage は判定するが、前段 artifact の SHA chain を次段へ
   必須化していない。1536 evidence 自体は検証できるが「96 で一旦判断してから進んだ」
   という運用順序までは証明しない。
4. score差に統計的な自動採否閾値を置いていない。これは小差の自動棄却を避けるための
   意図的な設計であり、最終 GO/NO-GO には block方向、CI、seat gap、runtime、package
   closure を含む別の人間判断が必要である。
5. actual Student v3 vs native Tomato common24 ledger は本作業範囲では未実行であり、ここに
   勝率改善の主張は含まれない。

## 実 artifact 結果（2026-08-13 追加）

上記の再開条件を満たす fresh common24 run を生成し、θ0 と AWR の両方を同一 native
Tomato control と照合した。3 arm は同一 reference config（24 opponents）、両 seat、各
2 repetition、base seed `13000000`、`max_steps=2000`、timeout 600 秒、evaluator SHA
`0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84` である。engine seed
setter は無いため、seed は独立層化 schedule の provenance であり、game-level paired RNG
ではない。全 arm は 96/96 DONE、fault 0、draw 0 だった。

| arm | W-D-L / 96 | score | nativeとの差 | reconciliation |
|---|---:|---:|---:|---|
| native Tomato | 66-0-30 | 68.750% | control | — |
| Student θ0 | 7-0-89 | 7.292% | −61.458pt / −59勝 | `SCREEN_COMPLETE_CONTINUE` |
| Student AWR | 3-0-93 | 3.125% | −65.625pt / −63勝 | `SCREEN_COMPLETE_CONTINUE` |

一次 artifact は次の通りである。

| 内容 | path | SHA-256 |
|---|---|---|
| native ledger | `runs/final-sprint-autonomous/native-tomato-common24-96-v2/ledger.jsonl` | `bd2b5b420286c6b77960009d05e31f69df3ed512251b514b6e00d046021fadf7` |
| native summary | `runs/final-sprint-autonomous/native-tomato-common24-96-v2/summary.json` | `f8427563ac6427910e854465732257a13a4a2fd989fbf4d957888ec271d97279` |
| θ0 ledger | `runs/final-sprint-autonomous/student-v3-theta0-common24-96-v2/ledger.jsonl` | `67765b935239d495b424f463076288a9065244082c3aeff5688863e5b3b38707` |
| θ0 summary | `runs/final-sprint-autonomous/student-v3-theta0-common24-96-v2/summary.json` | `a70b5ec0a07eced5d21e72634f97e00ee9db2c9ef523d535bad574af716b9532` |
| θ0 reconciliation | `runs/final-sprint-autonomous/student-v3-native-common24-reconcile-96-v2/reconciliation.json` | `81bfda4621ec1fc6952dd781e04a569d41c5e5389e5dabe821f2ecce03fab0bf` |
| AWR ledger | `runs/final-sprint-autonomous/student-v3-awr-common24-96-v2/ledger.jsonl` | `b968276f873ad9a8755208d43f11b3c7deb643124017651ecc6215196a24cbc6` |
| AWR summary | `runs/final-sprint-autonomous/student-v3-awr-common24-96-v2/summary.json` | `d68ae001974e9835bc62d8bcd96e07e820a1ea9ccb7bbc5e3a124410d5c797c4` |
| AWR reconciliation | `runs/final-sprint-autonomous/student-v3-awr-native-common24-reconcile-96-v2/reconciliation.json` | `6482a3f613af330985bc0d5bcb829884f744a20433cc6e612d71aae189f38b93` |

formal reconciliation request の SHA は θ0 が
`13ec6ab7a8206b6a7a820cfcf8699c69d1324ab22fd102a04533e89db458c728`、AWR が
`b6fc34b2eefed0fe74b0ddddf547acc6b4b5dce365cb0303fa94cfd571a9f889` である。AWR reconciliation
semantic SHA は `10fd95ea939b332c2c49ed3e1687040a5e186cb7f3bdf3dc6431f2b56518bae5` である。candidate
artifact は strict-loader で再検証され、全 authority flag は false である。runtime
telemetry は θ0 の allowlisted ordered-selection fallback 2 回、AWR 0 回を観測した。
これは runtime 観測であり、性能上の利益ではない。

### 判定

結果は NLL や validation exact-set の差ではなく、実戦 action/runtime の大幅な不足を示す。
θ0/AWR とも native BestKnown に近くないため、同型 AWR/hard-BC の 384/768/1536 延長と
longrun 起動は停止する。Student v3 の実装・データ経路は「動作し、faultなく評価できる」
ことまでは確認したが、現行 teacher bridge と set policy 表現を native policy の代替へ
昇格する性能根拠は無い。Full6 repair、native-population META_TRAIN、deck race は継続し、
次の候補は別の policy/value/search route として native control と再比較する。
