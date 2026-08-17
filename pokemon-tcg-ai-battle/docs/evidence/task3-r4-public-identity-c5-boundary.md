# Task 3 R4: 公開 ActionKey と C5 永続化境界

## 結論

R4 は、C4 の feature 読取と C5 の永続化を意図的に別の信頼境界にした。
v2 ActionKey と public action ID の SHA-256 は canonical content/integrity ID
であり、**署名、MAC、authenticated producer、または historical origin の証明ではない**。
悪意ある writer が全 JSON と hash を再計算できるなら、hash だけでは過去の生成元を
復元できない。origin が必要な運用は trusted producer boundary、append-only trusted log、
または別鍵の署名/MAC を追加して扱う。

## 実装した境界

- exported `build_action_key` は caller-provided public identity を受け付けない。
  generic の `fields` と `private_source_redacted` は actor identity から exact に導出する。
- `build_decision_state` だけが raw public board を解決し、module-private resolved builder
  で non-redacted Skill/Tool locator を作る。direct builder は Skill を redacted にし、
  Tool を fail closed にする。
- non-redacted Skill の public locator は top-level `active` / `bench` / `discard` の
  `(id, serial)` から domain-separated `card_ref` を作る。C1 は attachment/stadium の
  raw pair を保持しないため、それらの Skill source は必ず redacted のまま残す。
- non-redacted Tool locator は C1 の host zone/slot と `tools_count` に照合する。
- 通常の private-v2 deserialize は non-redacted Skill/Tool に public resolution context
  を要求する。C4 の v2 feature reader は runtime-only private capability を持つ
  structural reader であり、membership/origin を主張しない。
- C5 persistence は `validate_persistable_public_action_payload` を使用し、当該 record の
  `public_observation` に locator が実在することを検証する。public C5 feature vector も
  structural reader であり、永続化許可や origin の証拠ではない。

## C5 v1 closed envelope

`canonical-decision-v1` は 20 top-level keys と fixed nested keys を exact に検証する。
candidate の `action_id` / public payload / features、selection と C1 select metadata、
rule ranking coverage、student/C3 typed union、public trace digest、unique public action ID を
cross-field で検証する。追加 field は open extension として受け入れず、将来必要なら
別 schema version を dispatch する。

`record_id` と `content_hash` はここでも content/integrity ID に過ぎない。検証で保証する
のは canonical C5 schema と record 内 C1 membership であって、外部 writer が生成した
history の真実性ではない。

## R4 regressions

- caller-provided public identity の exported builder 注入を拒否する。
- generic allowed field の値を rehash しても actor projection と不一致なら拒否する。
- non-redacted serialized Skill は public resolution context なしでは拒否する。
- C4/C5 feature reader は structural vector を読めるが、そのことから C5 persistability を
  推論しない。
- C5 は forged Skill/Tool locator、open fixed container、selection/payload/features mismatch、
  duplicate public action、rule ranking coverage 不足、student/C3 の不正 shape、stale public
  trace digest、`True == 1` 型混同を拒否する。
- zero option かつ `min_count=max_count=0` の production decision は一つの empty trace を
  出力し、synthetic envelope は non-persistable のままである。

## 非目標

この変更は historical producer authentication を実装しない。また C1 が保持しない
attachment/stadium Skill pair を C5 locator として後から復元しない。どちらも data schema と
鍵管理を伴う別設計であり、unkeyed hash や serializable `verified` flag で代用しない。
