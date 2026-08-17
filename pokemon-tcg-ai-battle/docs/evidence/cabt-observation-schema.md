# cabt Observation Trace v0 — 観測構造と正規化スキーマ

結論: cabt の生観測は `main.py` の既存エージェント契約（`select is None` でデッキ登録、そうでなければ選択肢応答）と一致する形で安定しており、`search_begin_input` / `logs` / `remainingOverageTime` の3フィールドを除外し、対戦相手の非公開情報（手札内容・山札内容・プライズ内容）を一切保存しない限定的な正規化を行えば、privacy-safe な JSONL トレースを安全に取得できる。実装は [scripts/cabt_trace.py](../../scripts/cabt_trace.py) と [src/mage_ptcg/observability/cabt_trace.py](../../src/mage_ptcg/observability/cabt_trace.py) を正とする。

> **取り扱い注意（重要）**: 本トレースは対戦相手の非公開情報（手札・山札内容・プライズ内容）を除外しているが、**行動主体自身の手札カード ID（`self.hand_card_ids`）とデッキ登録時に提出した60枚全件（`deck_card_ids`）は意図的に保存している**。これは相手視点からは非公開の自分専用情報であり、トレース全体は依然として内部限定（internal-sensitive）の成果物である。別途の redaction／export 手順を経ずに、本トレースをそのまま公開・配布してはならない。本タスクではこれらのフィールドを削除しない。

## 1. 証拠の来歴（provenance）

- 本文書の分布数値は、self-play 8 episode・474 観測点（`random` seed 100–107 vs `deterministic`、`deck.csv` 使用）の再現実行から得た。**代表値であり正典ではない**。エンジンは `engine_seed_supported: false`（[scripts/test_sim.py](../../scripts/test_sim.py) の既存記録と一致）であり、同一 seed でも観測列は再現しない。
- 構造（キー集合、マスキングの有無、正規化に用いた scalar フィールド一覧）は全 474 観測で安定していたため、これらは構造的事実として扱う。個々の出現数・比率は参考値。
- カード要素の形状（`active`/`discard`/`hand` の内部フィールド）は、同一 episode 内の 1 回に限定した狙い撃ち調査（追加の大規模実験ではない）で確認した。

## 2. 観測トップレベルキー

全観測で安定:

```text
current, logs, remainingOverageTime, search_begin_input, select, step
```

`select` と `current` はデッキ登録時（1 episode につき 2 seat で計 1 回ずつ、観測全体の 16/474）に両方とも `null` になる。`main.py` の `_selection_contract` はこの `select is None` のみを判定条件としており、本実装の `record_type` 分類もこれに一致させた。

## 3. `select` / `current` の観測キー

`select`（458/474 で存在）:

```text
context, contextCard, deck, effect, maxCount, minCount, option,
remainDamageCounter, remainEnergyCost, type
```

`current`（458/474 で存在）:

```text
yourIndex, turn, turnActionCount, firstPlayer, result,
players, energyAttached, retreated, stadium, stadiumPlayed,
supporterPlayed, looking
```

`current.players[i]`（常に 2 要素）:

```text
active, asleep, bench, benchMax, burned, confused, deckCount,
discard, hand, handCount, paralyzed, poisoned, prize
```

`current.yourIndex ∈ {0, 1}` が行動主体の seat であり、本トレースの decision レコードにおける `seat` はこの値を正とする（実装値ではなくエンジン観測値を優先する）。

## 4. マスキングの確認結果

| フィールド | 自分視点 | 相手視点 | 分類 |
|---|---|---|---|
| `hand` | list（カード要素） | **`null`（458/458 で一貫）** | 完全マスク |
| `handCount` | int | int | 件数のみ公開 |
| `deckCount` | int | int | 件数のみ公開（要素リストなし） |
| `active` / `bench` / `discard` / `prize` | list | list（非 null） | **要素の内容が安全とは限らない** |
| `benchMax` | int | int | 公開スカラー |
| `poisoned`/`burned`/`asleep`/`paralyzed`/`confused` | bool | bool | 公開フラグ |

重要な限定事項: 相手の `active`/`bench`/`discard`/`prize` はコンテナとして非 null で観測されたが、**要素の中身が公開情報として安全である保証はない**。とくに `prize`（プライズ）は本来どちらの視点からも非公開であるべき情報であり、コンテナが non-null だからといって内容を保存してよいとは判断しない。v0 はこの不確実性に対して、要素内容を一切信頼せず明示的な正規化器（[`normalize_visible_card`](../../src/mage_ptcg/observability/cabt_trace.py)）を通し、`prize` は自分・相手ともに **件数のみ** を保存する。

## 5. `select.type` / `(type, context)` の観測分布（代表値）

`select.type`（n=474）: `0`→306, `1`→94, `4`→44, `9`→8, `8`→6, `None`（デッキ登録）→16。

`(type, context)`:

| type | context（件数） |
|---|---|
| 0 | 0 (306) |
| 1 | 1(16), 2(5), 3(6), 4(11), 7(32), 21(12), 22(12) |
| 4 | 30 (44) |
| 8 | 38 (6) |
| 9 | 41 (8) |

`type == 0` は `main.py` の `_MAIN_SELECT_TYPE` と一致するため `"MAIN"` と命名する。他の `type`（1, 4, 8, 9）はリポジトリ内に既存の対応付けがなく、**意味未解決** として扱う。

## 6. `option.type` の観測分布（代表値）

n=3986: `8`→2368, `7`→357, `13`→317, `14`→306, `3`→243, `9`→217, `6`→128, `12`→26, `0`→22, `2`→8, `1`→8。`10`（ABILITY、`main.py` の `_OPTION_TYPE_NAMES` に既存）は本サンプルでは未出現。

## 7. enum の既知/未解決区分

`main.py` に既存の対応付けがあるものだけを `type_name` として付与する。

| 値 | 名称 | 出典 |
|---|---|---|
| `select.type` 0 | MAIN | `main.py` の `_MAIN_SELECT_TYPE` |
| `option.type` 7 | PLAY | `main.py` の `_OPTION_TYPE_NAMES` |
| `option.type` 8 | ATTACH | 同上 |
| `option.type` 9 | EVOLVE | 同上 |
| `option.type` 10 | ABILITY | 同上（本サンプルでは未観測） |
| `option.type` 13 | ATTACK | 同上 |
| `option.type` 14 | END | 同上 |

以下は数値のみを記録し、意味づけを行わない（`type_name` は `null`）:

```text
select.type 1, 4, 8, 9
option.type 0, 1, 2, 3, 6, 12
```

## 8. プライバシー境界（実装契約）

行動主体（`current.yourIndex`）について:

- 手札のカード識別子（`hand_card_ids`、各要素の `id` のみ）と手札件数は保存してよい。
- 山札件数・プライズ件数は保存してよい。**プライズの要素内容と山札の順序・内容は保存しない。**
- `active`/`bench`/`discard` は安全なカード正規化器を通した場合のみ保存する。

対戦相手について:

- `hand` は一切保存しない。**`handCount` のみ**。
- `deckCount` のみ。プライズは**件数のみ**。
- `active`/`bench`/`discard` は自分側と同じ正規化器を通した場合のみ保存する。
- 状態異常フラグと `benchMax` は保存してよい。

非公開札の推測・再構成は行わない。実装は [`normalize_player_view`](../../src/mage_ptcg/observability/cabt_trace.py) にこの境界を集約する。

## 9. 除外フィールドと理由

| フィールド | 理由 |
|---|---|
| `search_begin_input` | Base64 様の不透明なエンジン再開トークン（観測長 85–1892 文字、状態とともに増大）。中身は**意図的にデコードしていない**。不透明であること自体と、非公開状態を含みうることが除外の十分条件であり、内容確認は不要と判断した。 |
| `logs` | episode 内で単調増加するリスト（観測長 0–103）。トレースサイズの肥大とスキーマの不安定化を避けるため除外する。 |
| `remainingOverageTime` | エージェントの残り思考時間予算であり、ゲーム状態そのものではない。 |

`select.deck` / `select.effect` / `select.contextCard` / `select.remainDamageCounter` / `select.remainEnergyCost` は本サンプルでは大半が `null` で意味が確認できておらず、**v1 スキーマの `select` オブジェクトには含めない**（意味不明なまま非公開情報を含むフィールドを保存するリスクを避ける）。

## 10. v1 JSONL スキーマ

1 行 1 レコード。`record_type` は `"deck_registration"` と `"decision"` の 2 種類。

### decision レコード

```json
{
  "schema_version": 1,
  "source": "official_cabt_agent_observation",
  "record_type": "decision",
  "episode_index": 0,
  "decision_index": 0,
  "seat_decision_index": 0,
  "engine_seed_supported": false,
  "seat": 0,
  "step": 1,
  "turn": 0,
  "turn_action_count": 0,
  "first_player": -1,
  "observed_result": -1,
  "select": {
    "type": 0,
    "type_name": "MAIN",
    "context": 0,
    "min_count": 1,
    "max_count": 1,
    "option_count": 6
  },
  "options": [
    {"option_index": 0, "type": 14, "type_name": "END", "fields": {}, "unknown_keys": []}
  ],
  "action": [0],
  "self": {
    "hand_card_ids": [],
    "hand_count": 0,
    "deck_count": 0,
    "prize_count": 0,
    "active": [],
    "bench": [],
    "discard": [],
    "bench_max": 5,
    "status": {"poisoned": false, "burned": false, "asleep": false, "paralyzed": false, "confused": false}
  },
  "opponent": {
    "hand_count": 0,
    "deck_count": 0,
    "prize_count": 0,
    "active": [],
    "bench": [],
    "discard": [],
    "bench_max": 5,
    "status": {"poisoned": false, "burned": false, "asleep": false, "paralyzed": false, "confused": false}
  },
  "board": {"stadium": null, "stadium_played": false, "supporter_played": false, "energy_attached": false, "retreated": false}
}
```

`options[i]` の `fields` は次の許可リストに含まれる scalar のみを保持し、他のキー名は値を持たず `unknown_keys` に列挙する:

```text
index, area, inPlayArea, inPlayIndex, playerIndex, energyIndex, count, number, attackId
```

`active`/`bench`/`discard` の各要素は `null`（空スロット）か、次の許可リストによる正規化結果 `{"fields": {...}, "unknown_keys": [...]}`:

```text
id, serial, playerIndex, hp, maxHp, appearThisTurn   # scalar のまま保持
energies, energyCards, tools, preEvolution           # 件数（"<field>_count"）へ縮約
```

`observed_result` は `current.result` が観測に存在した場合のみ出力する。

### deck_registration レコード

```json
{
  "schema_version": 1,
  "source": "official_cabt_agent_observation",
  "record_type": "deck_registration",
  "episode_index": 0,
  "decision_index": 0,
  "seat_decision_index": 0,
  "engine_seed_supported": false,
  "seat": 0,
  "deck_size": 60,
  "deck_card_ids": [],
  "deck_sha256": "..."
}
```

`deck_sha256` はエージェント自身が提出した 60 枚（自分の既知デッキであり非公開情報ではない）を `sorted()` してから JSON エンコードし SHA-256 したもの（[`canonical_deck_sha256`](../../src/mage_ptcg/observability/cabt_trace.py)）。提出順ではなく構成で同一性を判定する。

### 順序性

タイムスタンプ・UUID は使用しない。順序は `episode_index` / `decision_index`（episode 内で両 seat を跨いで共有するカウンタ）/ `seat_decision_index`（seat ごとのカウンタ）/ `step` / `turn` / `turn_action_count` の構造的フィールドで表現する。

## 11. 制限事項

- §4 の通り、相手（および自分）の `prize` は要素内容を一切確認しておらず、件数のみを安全側に倒して保存している。`active`/`bench`/`discard` も同様に、公開されているカードそのものではなく、限定された scalar フィールドのみを保存する設計とした。
- `select.type` ∈ {1, 4, 8, 9} と `option.type` ∈ {0, 1, 2, 3, 6, 12} の意味は未解決（§7）。
- `board.stadium` の非 null 時の形状は本サンプルで一度も観測されておらず、`{"id": <int>}` への防御的縮約のみ実装した（要検証）。
- `current.looking` は本サンプルで一度も非 null を観測しておらず、v1 スキーマに含めていない。
- `option.area` と `option.index` のzone／位置としての意味は未検証である。C1のActionKeyは、acting playerの手札IDを補助的に対応付けられる場合にも`index`を保持する。一意でないカードIDだけでは異なる合法選択肢を識別できないためであり、area/indexの意味づけを確定するものではない。
- 分布数値（§5, §6）は 8 episode の代表値であり、デッキやエージェント方策を変えた場合の分布を保証しない。

## 12. 次の Rule Agent 統合ポイント

`decision` レコードの `select` / `options` / `self` / `opponent` / `board` は、Rule Agent が合法手候補と盤面要約を構築する際の入力として転用できる形に揃えてある（[docs/plan/MAGE_PTCG_v5_README.md](../plan/MAGE_PTCG_v5_README.md) の実装順に従う場合、Rule Agent 実装時に本スキーマを一次データソースとして参照する）。ただし §7・§11 の未解決 enum とマスキング境界を Rule Agent 側のロジックへそのまま持ち込まないこと。特に `select.type` ∈ {1, 4, 8, 9} を要求する分岐（サブ選択）は、意味が確認できるまで安全側のデフォルト処理に倒す。
