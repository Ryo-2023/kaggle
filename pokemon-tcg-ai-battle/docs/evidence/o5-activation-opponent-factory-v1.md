---
title: O5 Activation & Archetype Opponent Factory v1
date: 2026-07-20
base_commit: 3db57b9da39c62ccedaa2091d31a1a0bf7067054
status: implementation-complete-active-population-blocked
---

# O5 Activation & Archetype Opponent Factory v1

## 結論

O5 Registry Foundationを利用するための fail-closed parser、Rules／Team permission Gate、GenericArchetypeAgent、Opponent Instance、versioned Benchmark の契約を追加した。現行の attestation と permission manifest は未受領のため、外部／team artifact を active 化していない。したがって実 cabt の96試合評価は実行せず、Benchmark は `BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION` である。

## Canonical integration

最新 canonical `3db57b9da39c62ccedaa2091d31a1a0bf7067054` から `integration/o5-activation-opponent-factory-v1` を作成し、feature `8c9ed1f4fe40bd55198cba95143352af4907dcb8` を no-ff merge した。merge commit は `6fab2bc` である。integration focused は54 passed、full regressionは1519 passed／5 warnings、security/privacy・submission・package・protected suiteは93 passed、docsは12/12だった。通常 push で canonical integration commit `8e56e4ba92bfe414985ff2febeb7fba7033c59a9` を反映し、canonical divergenceは`0 0`、両 worktree は clean である。この証跡追記 commit は同じ通常 push 系列に含める。

## 実装

- `o5_payload.py` は raw stdout／stderr を parser 前に外部 archive へ保存し、BOM、ANSI、heading、suffix、JSON Lines、複数 object を byte scanner で分離する。truncated／empty／複数 payload は trusted schema として受理しない。
- `o5_activation.py` は用途別のRules Gateとpath/hash selector付き Team permission manifestを定義する。UNVERIFIED Rules と未署名 manifest は常に inactive のままである。
- `GenericArchetypeAgent` は actor-visible selection metadataだけを読み、Policy Packが欠ける、Deck不一致、未知selection、例外、NaN相当の不整合ではRule Agent v0へ決定的にfallbackする。Policyは合法候補を削除しない。
- Opponent instance IDとBenchmark manifest hashはpath、時刻、run IDを含めない。engine metadataは`engine_seed_supported=false`、`pairing_mode=seat_matched_unseeded`、`exact_paired_inference=false`で固定した。

## 実行結果とGate

feature worktreeで再inventoryした結果は43 refs、260 raw Deck candidate、308 raw Agent candidateだった。これはO5 feature自体を含む作業中のref集合であり、canonical evidenceの38 refs／255／298を置換しない。明示team permission manifestは0、active Team Deck／Agentは0、runnable Agentは0、active exact Deckは0、Policy Pack／Opponent Instanceは0である。

Rulesは`UNVERIFIED_RULES_CONSTRAINT`のまま、`PUBLIC_OTHER`は`CAPTURE_ONLY`である。review packet、template、capability matrix、blocked benchmarkはリポジトリ外の`/home/bfe-lab-ono/kaggle-data/pokemon-tcg-ai-battle/o5-activation-opponent-factory-v1/`に保存した。PUBLIC_OTHER behavior training、team Agent execution、forced classification、Kaggle submission、Champion変更はいずれも0である。

## 検証

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/competition_intelligence/test_o5_activation.py \
  tests/competition_intelligence/test_o5_registry.py \
  tests/competition_intelligence/test_external_sources.py \
  tests/competition_intelligence/test_o3_transport_governance.py
# 45 passed
```

変更前baselineは`/tmp/o5-activation-opponent-factory-v1/baseline.xml`に保存した。`1510 passed, 1 failed`であり、失敗はworktree外部パスがsandboxでread-onlyとなりOffline Trainingの`dist/`作成に失敗した既存環境制約である。O5のfocused suiteはこの失敗を含まない。

## 残課題

人間が署名済みRules attestationとartifact単位のTeam permission manifestを提供した後に、許可されたexact Deckだけを分類し、Agent validation、Policy Pack実データ生成、Opponent population、96-game以上のseat-matched unseeded実cabt評価を再開する。Promotionは非自動であり、Champion/defaultはRule Agent v0のままとする。
