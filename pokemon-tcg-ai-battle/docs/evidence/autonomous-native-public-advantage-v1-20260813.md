# Autonomous Native Public Advantage v1（2026-08-13）

## 結果

Task 1 の public-state advantage artifact 契約を実装し、focused contract suite を通過させた。実装は research-only であり、native policy を最初に呼び、未知・低 support・不正・multi-select・ordered selection では native action を返す。CABT、学習、提出、既存 native `main.py`、既存性能 artifact は変更・起動していない。

## 変更範囲

- `src/mage_ptcg/meta_specialist/native_public_advantage_v1.py`
  - strict JSONL（duplicate JSON key、非有限値、未知/private field を拒否）
  - verified meta manifest と native policy SHA の binding
  - `META_TRAIN` のみを許可し、`META_DEV` / `META_FINAL` と training permission 不足を fail-closed
  - state/action weighted mean、one-pseudocount shrinkage、`delta_cap`、`min_support`
  - domain-separated canonical JSON（改行なし）による table SHA
  - single-choice `MAIN` だけを対象とする native-first bounded override
- `tests/meta_specialist/test_native_public_advantage_v1.py`
  - 15件: 集計の決定性、正負 cap、support不足、held-out拒否、private/unknown field、duplicate、非有限/digest、native fallback/override
- 本 evidence document

## 検証

RED（module 未作成時）:

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_native_public_advantage_v1.py
ModuleNotFoundError: No module named 'mage_ptcg.meta_specialist.native_public_advantage_v1'
```

実装後:

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_native_public_advantage_v1.py
15 passed in 1.24s
```

追加確認:

```text
PYTHONPATH=.:src .venv/bin/python -m py_compile src/mage_ptcg/meta_specialist/native_public_advantage_v1.py
git diff --check -- src/mage_ptcg/meta_specialist/native_public_advantage_v1.py tests/meta_specialist/test_native_public_advantage_v1.py
```

両コマンドとも exit 0。既存 native-preserving adapter suite は別プロセスで `9 passed`。一度、別の並行作業が pytest の一時 capture file を消したため collection error になったが、実装不具合ではなく、再実行した focused suite は上記の通り通過した。

## 再現 fixture の SHA と coverage

固定 `/tmp/native-public-advantage-evidence-v1` fixture（4 rows、1 state、2 action、`min_support=2`、`delta_cap=0.25`）で生成した値:

```text
meta_manifest_sha256 = a628941c07d6830290ee19acfdd6b45231bcea25ffb1df49528465b5b40164a
table_sha256        = 0f003e51beb3f5c668d3c768564c09f22fb757d074e821c05d1e25f805d6d44a
```

coverage:

```json
{
  "input_rows": 4,
  "accepted_rows": 4,
  "meta_train_rows": 4,
  "state_count": 1,
  "action_pairs": 2,
  "supported_action_pairs": 2,
  "insufficient_support": 0,
  "opponent_count": 1,
  "seat_counts": {"0": 4, "1": 0},
  "heldout_rows": 0,
  "private_state_features": false
}
```

これは synthetic contract fixture であり、性能結果や common24 screen の根拠ではない。実データでの screen は Task 5 まで起動しない。

## 未実施・懸念

- common24 96→384→768→1536、CABT、training、deck optimization、longrun は未実施。
- action key は現行 closed SHA-256 digest schema に限定した。異なる Stable ActionKey schema を受ける場合は別途 schema binding を追加する。
- source row schema に game id がないため、同一 canonical row の重複は拒否し、同一 public state/action の別 episode は outcome/seat/weight の差異がある場合に限り受け入れる。
- override margin は固定 `0.05`。candidate は native の legal option list 内の index のみで、promotion authority は付与しない。

## Git / authority

commit、push、remote branch、Champion変更、Kaggle submission は行っていない。全 authority flag は false、`research_only=true` である。

## Review fix round 1（I-1 / I-2）

レビュー指摘に対して、source opponent の `usage_boundary` を `training_local` / `training_local_and_eval` に限定し、`training_allowed=true`、`behavior_allowed=true`、`submission_allowed=false` を明示的に要求した。verified manifest の `training_authority` / `promotion_authority` / `submission_authority` が true、または `research_only` が false の場合も fail-closed とした。

`PublicAdvantageTableV1` は構築・reload時に canonical `_table_payload` を再計算し、`table_sha256` と一致しなければ拒否する。`entries` は tuple、`coverage_summary` と nested mapping/list は再帰的な immutable copy（`MappingProxyType` / tuple）として保持し、coverage mutation による SHA 乖離を防止した。`to_dict()` / `from_dict()` の roundtrip と forged SHA の拒否をテストで固定した。

fix round 1 の focused suite は 24 passed、native adapter/tuning suite は 13 passed。性能評価・CABT・学習・提出は引き続き未実施。
