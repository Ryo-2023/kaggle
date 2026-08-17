---
project: MAGE-PTCG
slice: C5
status: offline-adapter-validated
as_of: 2026-07-16
---

# C5 ActionKey Adapter

## 結論

curated pack の 22 件 teacher rule を、既存の Stable ActionKey candidate 契約へ決定的・fail-closed に対応付ける ActionKey adapter を追加した。adapter は新しい ActionKey schema を作らず、rule の自然言語 condition を解釈せず、一意に束縛できない candidate を選ばない。実 C4 fixture は rule binding の attestation を持たないため、decision への適用は正直に 0 件である。これは redacted public projection の反映であり、coverage の偽装ではない。Champion と submission default は Rule Agent v0 のままである。

## Base と作業ブランチ

- base SHA: `1445ed4c62a0833f60152139f155267f7a57f5ea`（`feature/belief-guided-search`）
- work branch: `feature/c5-actionkey-adapter`（canonical HEAD から分岐、canonical へ merge/push しない）

## Adapter API

`src/mage_ptcg/distillation/actionkey_adapter.py`。

- `adapt_teacher_rule(rule, observation, legal_candidates, *, hard_constraint_ids=frozenset()) -> TeacherApplication`
- `adapt_decision(observation, legal_candidates, knowledge) -> DecisionAdaptation`（全 22 rule を集約）
- `adapt_records(records, knowledge) -> dict`（build 用の決定的 manifest）
- `classify_teacher_rule(rule) -> RuleSupportClass`、`normalize_action_type(raw)`、`adapter_config_hash()`

`TeacherApplication` は `teacher_id`／`status`／`matched_candidate_ids`／`score_adjustments`／`hard_rejections`／`skip_reason`／`provenance` を保持する。`status` は `APPLIED`／`SKIPPED_NO_MATCH`／`SKIPPED_AMBIGUOUS`／`SKIPPED_UNSUPPORTED`／`SKIPPED_PRIVATE`／`SKIPPED_CONFLICT`／`FALLBACK_RULE_V0`。

## Mapping と安全境界

- **既存 schema の再利用**: legal candidate 検証・public 検査・formula delta は `mage_ptcg.distillation.knowledge` の `_legal_candidates`／`_ensure_public`／`_formula_delta` を再利用する。新しい互換性のない ActionKey schema を作らない。
- **Direct mapping**: teacher の `candidate_action_type` を candidate の attested `action_type`、無ければ redacted public `semantic_operation` family へ正規化して照合する。束縛は candidate の明示的 attestation（`applicable_rule_ids` + `observable_condition_met is True`）と portable な数値 delta があるときだけ成立する。adapter は attestation を rule prose から推測しない。
- **Normalization**: 表記のみ正規化する（大文字化、空白、既知 alias、family への写像）。意味的推測はしない。
- **Ambiguous**: 複数 candidate が同一条件を満たし一意に決められない rule、または重複した ActionKey mapping は先頭選択・index 順に頼らず `SKIPPED_AMBIGUOUS` にする。
- **Unsupported**: 現 ActionKey で表現できない rule は schema を拡張せず `SKIPPED_UNSUPPORTED`（teacher_id と理由を記録）。
- **Private**: forbidden／credential 形状の observation・candidate は照合前に `SKIPPED_PRIVATE`。
- **Multi-select**: minCount／maxCount と candidate 一意性を保ち、matched_candidate_ids を action_id 昇順で返す。重複 stable id は `SKIPPED_AMBIGUOUS`。
- **Hard constraint precedence**: hard 拒否 candidate（HC 違反 attestation、`curated_score_delta <= -1000`、`hard_reject`）は soft scoring 前に除外し、matched に入れない。全 matched が hard 拒否なら `SKIPPED_CONFLICT`。
- **Registration separation**: `select` を持たない registration observation はどの in-game teacher rule でも `SKIPPED_UNSUPPORTED`。
- **順序非依存**: matched ids・score adjustments・manifest はすべて action_id／teacher_id でソートし、candidate 入力順に依存しない。

## 22 件の rule 分類

分類は `_SUPPORT_TABLE`（人手 curate、code が正）に固定し、`classification_summary` が集計する。

| class | 件数 | teacher_id |
|---|---:|---|
| directly supported | 1 | TR-000010 |
| condition only | 7 | TR-000002, TR-000008, TR-000011, TR-000013, TR-000015, TR-000016, TR-000020 |
| ambiguous | 2 | TR-000001, TR-000021 |
| unsupported | 12 | TR-000003, TR-000004, TR-000005, TR-000006, TR-000007, TR-000009, TR-000012, TR-000014, TR-000017, TR-000018, TR-000019, TR-000022 |

- **directly supported (1)**: TR-000010 のみ。portable な `-1` penalty を持ち、明示 condition attestation 経由で束縛できる。
- **normalized (0)**: registry の action_type token は既に canonical のため、実 22 件で表記正規化を要する rule は 0。alias／小文字入力の正規化は synthetic fixture で検証。
- **condition only (7)**: condition の一部は public field（deckCount／handCount／minCount）で計算できるが、対象 card identity が redacted のため一意束縛できず fail-closed。
- **ambiguous (2)**: option index の tie-break に依存するため適用しない。
- **unsupported (12)**: card identity、energy 状態、cross-action lookahead、multi-step macro を要し、現 ActionKey で表現できない。

## Decision 適用（実 fixture）

3 decision fixture C5 build（各 22 rule = 66 decision-rule pair）:

| 指標 | 値 |
|---|---:|
| decisions considered | 3 |
| decisions with applied rule | 0 |
| teacher_rules_applied | 0 |
| candidate_matches | 0 |
| decision_rule_pairs no_match | 3 |
| decision_rule_pairs unsupported | 57 |
| decision_rule_pairs ambiguous | 6 |
| decision_rule_pairs private / conflict | 0 / 0 |
| hard_constraint_rejections | 0 |
| rule_v0_fallbacks | 3 |

適用 0 件は attestation 不在による正直な結果。adapter 単体は fixture で APPLIED／ambiguous／hard-reject／multi-select／private／registration を検証した（`tests/test_actionkey_adapter.py`、28 件）。

## Privacy・Determinism・Provenance

- privacy: forbidden observation／candidate field を照合前に fail-closed 拒否。非公開情報を照合入力に混入しない。
- determinism: `adapter_config_hash` は classification table／alias／family map の digest。build summary は `input_hash`（record content_hash の digest）と adapter manifest を出力し、同一入力・config で byte 一致。candidate 入力順を反転しても metrics・manifest 不変（test で確認）。
- provenance: 各 application と applied_binding に `teacher_id`／`canonical_rule_id`／`support_class`／`adapter_version` を保持。applied rule だけが score／label に影響し、skipped rule は影響ゼロ。適用不能 decision に label を生成しない。

## Dataset 接続

`scripts/c5_distillation.py` の build は `--curated-knowledge-dir` 指定時に `adapt_records` を実行し、mode `offline-actionkey-adapter` として manifest を summary へ出力する。canonical decision dataset（Rule v0 label）と episode／near-duplicate split は変更しない。hard constraint 違反 candidate は常に除外する。

## Submission 不変

submission runtime は `main.py`／`deck.csv`／`agents/__init__.py`／`agents/rule_agent.py` のみで、本変更はいずれも触れない。build/verify は content_hash `cb778ba0ab31aabc74eca7b763cebea80b3df85f78df5ad9025f8948f91361f8`、tar.gz sha256 `c26b98d0d7ed80eb288a36c924babcf96ada7405aa3a965a54714d67295b8f6b`、clean-room deck_size 60・mandatory `[0,1]` を確認。default で submission runtime へ接続しない。

## 検証

```bash
python -m pytest tests/test_knowledge_wiring.py tests/test_actionkey_adapter.py \
  tests/test_targeted_distillation_v0.py tests/test_student_v0.py \
  tests/test_public_belief_decision_loop.py tests/test_knowledge_rule_adapter.py -q
# 111 passed

python -m pytest -q
# 553 passed, 3 warnings（baseline 525 + adapter 28）、failed 0・skipped 0

python scripts/curate_team_knowledge.py --check
# 全 integrity check true

python scripts/build_submission.py --output-dir <fresh>
python scripts/build_submission.py --verify-dir <same>
# clean-room pass、Rule v0 runtime files のみ
```

actual cabt は本タスク対象外。status `NOT_RUN`、reason `CAPABILITY_UNAVAILABLE`、promotion `NO_DECISION`。synthetic 結果を actual 結果として扱わない。

## 残リスクと次のコマンド

- 実データ適用は 0 件であり、adapter の APPLIED 経路は synthetic attestation でのみ検証済み。実 attestation を供給する offline binder は未実装。
- condition-only 7 件は public condition を計算できても対象 card identity が redacted のため適用できない。card identity を安全に公開する契約が無い限り applied には昇格しない。

```bash
python -m pytest -q
```
