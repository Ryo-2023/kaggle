# CABT agent JSON selection contract v1

## 結論

`kaggle-environments==1.32.0` の Python agent 境界では、`Observation.select.type` と
`Observation.select.context` は 0 始まりの JSON 整数である。native engine 内部の
one-based C++ enum byte をこの境界へ流用しない。実装上の frozen source of truth は
`src/mage_ptcg/meta_specialist/cabt_json_contract_v1.py`、捕捉した SkillOrder の最小 fixture は
`tests/meta_specialist/fixtures/cabt_1_32_0_skill_order.json` である。

公式 API reference:

- https://matsuoinstitute.github.io/cabt/api.html
- https://www.kaggle.com/competitions/pokemon-tcg-ai-battle/overview/description

公式 API reference は `SelectType` が `Observation.select.type`、`SelectContext` が
`Observation.select.context`、`OptionType` が `Observation.select.option[*].type` に使われると明記している。
同 reference の ID を組み合わせた frozen mapping は次のとおりである。

| agent JSON select.type | 名称 | 許可 context |
|---:|---|---|
| 0 | Main | 0 |
| 1 | Card | 1..25 |
| 2 | AttachedCard | 26..28 |
| 3 | CardOrAttachedCard | 29 |
| 4 | Energy | 30..33 |
| 5 | Skill | 34 (SkillOrder) |
| 6 | Attack | 35..36 |
| 7 | Evolve | 37 |
| 8 | Count | 38..40 |
| 9 | YesNo | 41..46 |
| 10 | SpecialCondition | 47..48 |

`(5, 34)` だけを `ordered_sequence` とし、表内の他 pair は `unordered_set`、表外は
fail closed とする。公式 reference は context 34 を「Select skill activation order」と説明し、Skill option
type 15 の fields を `cardId` と `serial` としている。

## Task 5 向け canonical contract identity

`cabt_agent_json_contract_payload_v1()` は毎回 fresh な JSON-like payload を返し、top-level key は
`schema_version`、`selection_schemas`、`ordered_selection_schemas` の exact 3 keys である。
`selection_schemas` は上表の 49 pair を strict int の `[type, context]` として辞書順に並べ、
`ordered_selection_schemas` は exact `[[5,34]]` である。`MappingProxyType` や `frozenset` 自体は
serialize しない。

canonical JSON は UTF-8、`ensure_ascii=False`、`allow_nan=False`、sorted keys、compact separators で
458 bytes となる。hash input は次の exact bytes である。

```text
b"meta-specialist-cabt-agent-json-contract-v1\0" + canonical_json(payload)
```

固定 SHA-256 は
`7993f5770d088181206c00bac9f959b3c3cbb05e4ca22da38d947ac1c65b9259`。
`tests/meta_specialist/test_actions.py` が payload freshness、49 strict-int pairs、exact canonical bytes、
domain-separated digest を literal oracle で検証する。

## 1.32.0 ローカル 2 局 probe

2026-08-02 JST に、installed distribution を `importlib.metadata.version("kaggle-environments")` で
`1.32.0` と確認したうえで、同一 60 枚 deck を使う random-legal agent 同士を 2 局実行した。
deck composition SHA-256 は
`cafa7652a6349be806d8ac2b9abfdb6c72ca3821f368e0d912e2d989f3b54cdd` である。

- 両局とも両 seat が `DONE`。step 数は 244 と 228。
- reward は `[0, 0]` と `[-1, 1]`。
- `(type=5, context=34)` を合計 9 回観測。
- 最初の捕捉は `minCount=maxCount=2`、option は
  `{"type":15,"cardId":104,"serial":13}` と
  `{"type":15,"cardId":104,"serial":74}`。
- その最小 select payload を上記 fixture に保存した。board、hand、log、search payload は fixture に保存していない。

agent RNG seed は固定したが CABT engine seed support は確認できないため、この 2 局を engine-level
再現性の証拠とは呼ばない。また、probe は異なる返却順序を同一 state へ反実仮想的に適用して outcome を比較していない。
したがってここで証明するのは agent JSON の `(5,34,type 15)` shape と実出現だけであり、未計測の
ゲーム結果差は主張しない。runtime contract は公式の activation-order semantics を保持するため、
SkillOrder の返却 index sequence を sort せずそのまま実行する。

## 境界

- option ordinal は stable identity に使わない。
- Skill の raw `(cardId, serial)` は actor/private identity にだけ使い、公開 trace には verified public locator
  または exact redaction を保存する。
- SpecialCondition は agent JSON type 10、ToolCard は AttachedCard type 2 の source-literal option union（context 26..28、context 27 は discard Tool）を使う。attached-tool の実 replay は未取得なので、context 28 の synthetic payload を実挙動としては主張しない。
- `firstPlayer` は first-player 未決定の `(type=9, context=41)` setup prompt では `-1` を取りうるため、C1/C5 は non-bool の `-1/0/1` を保持する。
- complete-action envelope は 60 legal candidates を防御上限とし、それ以上を materialize しない。この値は
  CABT の 60 枚 deck contract と Task 3 safety ceiling に固定しており、2 局 probe の観測最大値を一般化したものではない。
