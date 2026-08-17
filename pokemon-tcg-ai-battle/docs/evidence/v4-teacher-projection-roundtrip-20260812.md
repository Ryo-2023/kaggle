# V4 qualified teacher projection round-trip audit（2026-08-12）

## 結論

tomatomato／lucifer19 の qualified teacher sealed record を、

```text
physical teacher action
→ semantic action class
→ V4 shared semantic legality / decoder
→ current physical legal action
```

の順で再構成した。全 9,322 record・9,322 hard-selection mass row が 0 fault で通過し、semantic target が変わる不一致は 0 件だった。したがって、今回確認した corpus 範囲では teacher projection/converter/V4 decoder の round-trip 不一致は、性能停滞の主因としては確認されなかった。

この監査は read-only であり、teacher 再収集、production adapter の変更、学習、CABT 評価、Champion 変更、提出は行っていない。

## 実施範囲と再現コマンド

対象 corpus は次の 3 つ。

| corpus | teacher | record |
|---|---|---:|
| `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-24` | `tomatomato_archaludon` | 1,386 |
| `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96` | `tomatomato_archaludon` | 5,146 |
| `runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-48` | `lucifer19_battlecore` | 2,790 |
| **合計** |  | **9,322** |

実行コマンド:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/audit_teacher_projection_roundtrip_v1.py \
  --output runs/meta-specialist-teacher-projection-roundtrip-audit-20260812-all-v2.json
```

成果物 SHA-256:

| artifact | SHA-256 |
|---|---|
| `scripts/audit_teacher_projection_roundtrip_v1.py` | `f6e84e34940793a2a3aeb04e7ff75a65e23322253ca37f56049df0ebefe28986` |
| `tests/meta_specialist/test_teacher_projection_roundtrip_audit_v1.py` | `66f1d0e4136fd18364d34342176031980fedff6979391f2c57565b37b7bc159b` |
| `runs/meta-specialist-teacher-projection-roundtrip-audit-20260812-all-v2.json` | `303fd26a6a08082f2865182782f7cfc41710f7e4861ae86c8bbfd8c7fe511d4c` |

監査は registry からロードした production vocabulary（`meta-specialist-en-card-database-v1`、1,267 card IDs、source SHA `a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373`）を使用した。

## 全量結果

| 指標 | 結果 |
|---|---:|
| records seen / passed | 9,322 / 9,322 |
| records failed | 0 |
| teacher mass rows | 9,322 |
| roundtrip rows | 9,322 |
| legal final STOP rows | 9,322 |
| empty selection rows | 124 |
| selected `END`（option type 14） | 191 |
| selected `RETREAT`（option type 12） | 148 |
| duplicate semantic-key groups | 6,593 |
| records with duplicate semantic key | 4,438 |
| 最大 alias multiplicity | 11 |
| selected alias rows（重複 semantic key を選んだ行） | 3,128 |
| physical ID が semantic decode と同一（unordered は multiset 同一） | 8,186 |
| unordered の physical 順序だけが正規化された行 | 243 |
| deterministic alias substitution（semantic は同一） | 1,136 |
| semantic target 不一致 / legal index 不一致 | 0 |

`teacher.target_kind` は全 9,322 件が `hard_selection` で、各 record の `mass_rows` は 1 行だった。監査では次を毎行再実行した。

1. `validate_local_record_v2` による sealed local record、min/max、重複選択、legal candidate の再検証。
2. `_state_payload_from_record` → `deserialize_actor_visible_decision_state_v2` による typed actor-visible state の再構築。
3. `extract_specialist_model_input_v1` と stored `semantic_action` の一致検証。
4. teacher の physical local ID を semantic row へ射影。
5. `choose_lexicographic_alias_v1` で semantic row 列を physical alias 列へ再デコード。
6. `RuntimeDecisionEnvelope.complete_action` と `decode_option_indices` で current legal physical action へ戻ることを検証。
7. `semantic_runtime_complete_action_from_runtime_action_v2` で semantic class 列が teacher target と一致することを検証。
8. 最終 `STOP` が `build_step_input` 上で合法であることを検証。

alias substitution は失敗ではない。semantic class を選んだ後の V4 decoder は、同じ semantic key を持つ physical alias のうち deterministic な代表を選ぶため、teacher が別の duplicate local ID を選んでいても semantic equivalence が保たれれば PASS とした。unordered selection では local-ID の順序を physical multiset として扱い、同一 multiset の順序正規化を alias substitution から分離した。

## corpus 内のカテゴリ coverage

全 record が CABT contract 上 `unordered_set` だった。見つかった selection schema は次の通り。

| selection type / context | records |
|---|---:|
| 0 / 0 | 4,835 |
| 1 / 1 | 168 |
| 1 / 2 | 66 |
| 1 / 3 | 222 |
| 1 / 4 | 260 |
| 1 / 7 | 1,763 |
| 1 / 8 | 234 |
| 1 / 21 | 860 |
| 1 / 22 | 416 |
| 4 / 30 | 23 |
| 8 / 38 | 34 |
| 9 / 41 | 96 |
| 9 / 42 | 52 |
| 9 / 43 | 293 |
| **ordered schema（5 / 34）** | **0** |

min/max は `(0,1)` 737、`(0,2)` 13、`(0,3)` 118、`(1,1)` 7,495、`(1,2)` 232、`(2,2)` 646、`(3,3)` 48、`(4,4)` 9、`(5,5)` 12、`(6,6)` 6、`(7,7)` 2、`(8,8)` 3、`(13,13)` 1 だった。範囲外の selection は 0 件。

## fixture smoke（corpus にない境界）

sealed corpus に ordered selection が無かったため、audit script 内の test vocabulary fixture で次を確認した。全ケース PASS。

| fixture | rows | 結果 |
|---|---:|---|
| empty selection / implicit STOP | 1 | PASS |
| duplicate semantic alias | 3 | PASS |
| ordered prefix（forward / reverse / empty） | 3 | PASS |
| `END` option type 14 | 1 | PASS |
| `RETREAT` option type 12 | 1 | PASS |

専用テスト:

```bash
TMPDIR=/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.tmp-test \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/pytest -q --capture=no \
  tests/meta_specialist/test_teacher_projection_roundtrip_audit_v1.py
```

結果: `1 passed in 0.07s`。

## 解釈と残課題

今回の結果は、既存 tomatomato/lucifer sealed record が semantic class と legal physical action の間で壊れている、という仮説を支持しない。したがって次の teacher 学習を converter 修正のために止める必要はない。

ただし次は未実測のまま残る。

- 実 corpus に ordered `(selection_type=5, context=34)` が 0 件であり、ordered を teacher record そのもので量的に検証したわけではない。fixture smoke と既存 runtime contract test で構造を検証した。
- 全 record は `hard_selection` で、soft teacher mass、複数 `mass_rows`、複数 completion distribution の round-trip は未観測である。別 target kind を導入する場合は同じ監査を再実行する必要がある。
- 物理 alias の deterministic representative は teacher が元々選んだ serial/ordinal と同じとは限らない。ここで保証したのは semantic equivalence と current legal execution であり、alias 個体の同一性ではない。
- この監査は teacher の行動が outcome 上正しいか、policy drift、評価ノイズ、recurrent amplification を測らない。今回の結論は converter/round-trip 境界だけに限定する。

