# C2a Knowledge Pack v0 の実装Evidence

## 結論

2026-07-15に、現在の`deck.csv`を対象とするC2a Knowledge Pack v0を実装した。Packはimmutableなcanonical JSON snapshotとして生成・検証でき、Rule Agent v0／v1へ任意のsoft-prior tie-breakとして接続できる。Rule v0はChampionのままであり、submission defaultも変更していない。

## 対象とschema

対象deckはリポジトリ直下の`deck.csv`である。snapshotは9種類・合計60枚（ID/count: 3/35, 721/2, 722/4, 723/4, 1145/4, 1158/1, 1205/2, 1227/4, 1235/4）をcard ID昇順で保存する。

主要APIは`mage_ptcg.knowledge`で公開する。`KnowledgePack`、`TeamDeck`、`DeckEntry`、`ActionPrior`、`KnowledgeConfidence`はfrozen dataclassとtupleで構成する。`serialize_pack`／`load_pack`はcanonical JSONを往復し、読み込み時にcontent hashとpack IDを再計算して検査する。`check_compatibility`はschema version、ActionKey schema version、cabt version、card pool ID/version、deck IDの不一致を理由付きで返す。`build_team_deck_pack`、`scripts/build_knowledge_pack.py`、`scripts/verify_knowledge_pack.py`が再現可能なbuild／verify入口である。

`schema_version`は`knowledge-pack-v0`、ActionKey schema versionはC1 `DecisionState`に対応する`decision-state-v1`である。cabt依存は`requirements.txt`の`kaggle-environments==1.32.0`を`kaggle-environments-1.32.0`として記録する。

## Role tagとconfidence

role vocabularyは`CORE`、`ENGINE`、`FLEX`、`TECH`に固定した。現リポジトリには公式card dataがないため、カード効果やデッキ上の役割を推測しない。全entryを保守的に`FLEX`とし、role confidenceを`validity=1.0`、`support=0.0`、`freshness=0.0`とした。これは「形式上有効だが意味的な裏付けがない」ことを表す。

`KnowledgeConfidence`の3軸はいずれも有限な`[0.0, 1.0]`である。`validity`は形式・整合性、`support`は出典または検証の裏付け、`freshness`は情報の新しさを表す。単一のconfidence値へ縮約しない。

## Hash、provenance、互換性

content hashはdomain-separated SHA-256で、source、Team Deckのcard count／role annotation、Action Prior、schema／ActionKey／cabt／card pool compatibility値を対象にする。`content_hash`と`pack_id`自身はhash対象外で、`pack_id`は`knowledge-pack-v0-<hash先頭20桁>`として導出する。build時刻は含めないため、同一内容は同一hashになる。

sample artifactは[team_deck_v0.json](../../artifacts/knowledge/team_deck_v0.json)である。card poolの公式versionはこのworktreeに無いため、`competition-card-pool-unverified`／`unverified`を明示した。これらを推測で公式versionへ置換していない。runtimeはこの値を含む全compatibility fieldの一致がないpackを適用しない。

## Rule adapter、legality、privacy、fallback

`KnowledgeRuleAdapter`は既存Rule v0 scoreの**同点group内だけ**を、ActionKey predicateに一致する`ActionPrior`のscoreで決定的に並べ替える。異なるRule scoreを逆転せず、候補追加・削除もしない。最終的なoption index、selection count、合法性は既存Rule Agentとcabtが保持する。

`make_rule_agent(..., knowledge_pack=...)`と`make_rule_agent_v1(..., knowledge_pack=...)`がoptional接続点である。packなし、ロード・validation error、deckなどの非互換、adapter処理エラーでは既存selectionをそのまま返す。snapshotはagent生成時または最初のselection時に一度だけloadし、hot pathでJSONを再読込しない。

adapterが読むのは`select.type`、`select.context`、cabtが提示した`select.option`の既存ActionKey allowlistだけである。相手hand、deck、prize、opaque log、hidden truthを読まない。adapter自体はmutable match stateを持たず、Rule v1の既存`reset`契約を維持する。

## 独立レビュー後の修正（Finding 1〜3）

独立レビューで、runtime compatibility targetがpack manifestを自己参照していたこと、Knowledgeが中立な同点groupでもdigestによる並べ替えが起こり得たこと、Rule v1がadapter candidateを返す直前に再合法性検査していないことを検出した。2026-07-15に次を修正した。

- runtime targetは`runtime_compatibility_for_deck(deck_card_ids)`だけで構築する。schemaとActionKey schemaはruntime正典定数、card pool ID/versionはruntime既定定数、deck IDはfactoryに渡された60枚から導出する。cabt versionはインストール済み`kaggle-environments`のdistribution versionを使い、distributionが無いbundle／test環境では`requirements.txt`に固定されたbaselineへ明示的にfallbackする。pack manifestのcompatibility値は期待値に使わない。CLIとRule v0／v1 factoryはこのhelperを共有する。
- Low-1 hardeningとして、distribution metadata backendが`PackageNotFoundError`以外で壊れた場合も、metadata取得境界だけで固定baselineへfail-closed fallbackする。factory側の例外境界やpack schema／hash／prior／candidate gateは変更していない。
- ActionKey schema、cabt、card pool ID/version、Knowledge schema、deckのmismatchでは、Rule v0／v1ともKnowledgeなしのbaseline outputを完全に返す。
- 同一Rule score groupは`(-prior_score, original_index)`で扱う。ただしpriorが全候補で不一致または同値ならsortを行わずbaseline順序をそのまま保持する。digestはtie-breakに使わない。
- Rule v1はcandidateを返す直前に`_is_legal_selection`で再検査する。範囲外index、重複、`minCount`不足、`maxCount`超過、adapter例外はbaselineへfallbackする。

## C3への入口と制限

C3 bounded searchは、validated compatible `KnowledgePack`と`KnowledgeRuleAdapter.prior_score(ActionKey)`をguided priorとして受け取れる。candidate削除やvalueの断定には使わず、unguided比較と同じ合法ActionKey setを使う。

未検証の制限は、公式card dataがないためcard roleが全て低supportの`FLEX`であること、card pool versionがunverifiedであること、sample Playbookが既存Rule v0の`PLAY`方針に由来するtie-break 1件のみであること、性能評価を実施していないことである。C2aは勝率改善を主張しない。

## 実測検証

以下は今回のworktreeで実行した結果である。

```text
python -m pytest -q tests/test_knowledge_pack_v0.py tests/test_knowledge_rule_adapter.py tests/test_rule_agent.py tests/test_public_belief_decision_loop.py
65 passed, 2 skipped

python scripts/build_knowledge_pack.py
python scripts/verify_knowledge_pack.py
python scripts/build_knowledge_pack.py --output /tmp/team_deck_v0_second.json
cmp -s artifacts/knowledge/team_deck_v0.json /tmp/team_deck_v0_second.json
pack_id=knowledge-pack-v0-dd84ae655be7a79407ad
content_hash=dd84ae655be7a79407adcb682af90ed8cc8cc1deb737628605da3f05e07cdbea

python -m pytest -q
384 passed, 7 skipped

git diff --check
pass

python -c 'import main; ... make_rule_agent(knowledge_pack="artifacts/knowledge/team_deck_v0.json") ...'
clean main knowledge import ok
```

### Review fixes再検証（2026-07-15）

```text
python -m pytest -q tests/test_knowledge_pack_v0.py
16 passed

python -m pytest -q tests/test_knowledge_rule_adapter.py
16 passed

python -m pytest -q
395 passed, 7 skipped

python -m pytest -q <runtime mismatch / neutral tie / adversarial Rule v1 subset>
11 passed

python scripts/build_knowledge_pack.py
python scripts/verify_knowledge_pack.py
python scripts/build_knowledge_pack.py --output /tmp/team_deck_v0_review_fix.json
cmp -s artifacts/knowledge/team_deck_v0.json /tmp/team_deck_v0_review_fix.json
pack_id=knowledge-pack-v0-dd84ae655be7a79407ad
content_hash=dd84ae655be7a79407adcb682af90ed8cc8cc1deb737628605da3f05e07cdbea

git diff --check
pass
```

### Low-1 metadata hardening再検証（2026-07-15）

```text
python -m pytest -q tests/test_knowledge_rule_adapter.py tests/test_knowledge_pack_v0.py
34 passed

python -m pytest -q
397 passed, 7 skipped

python scripts/build_knowledge_pack.py --output /tmp/c2a-pack-1.json
python scripts/build_knowledge_pack.py --output /tmp/c2a-pack-2.json
cmp /tmp/c2a-pack-1.json /tmp/c2a-pack-2.json
content_hash=dd84ae655be7a79407adcb682af90ed8cc8cc1deb737628605da3f05e07cdbea
```
