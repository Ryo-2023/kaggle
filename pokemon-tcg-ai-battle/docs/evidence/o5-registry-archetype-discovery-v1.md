---
title: O5 Registry Foundation and Archetype Discovery v1
date: 2026-07-20
base_commit: 8b3bbada1be73d73e04d9280662e195eeeae35a1
status: implementation-and-smoke-complete
---

# O5 Registry Foundation and Archetype Discovery v1

## 結論

環境観測と team branch の Deck／Agent を同一の `deck_hash` Registry へ記録する O5 基盤を追加した。`PUBLIC_OTHER` は現行の `UNVERIFIED_RULES_CONSTRAINT` で `CAPTURE_ONLY` に固定され、分類・分析・評価・学習へ流れない。team branch は全件を git object 経由で inventory したが、明示許可がないため `TEAM_SHARED_PENDING_PERMISSION` として archive-only に留めた。

## 実装

- `o5_registry.py` は順序を捨てた 60 枚 multiset の `deck_hash`、Exact／Incomplete observation の分離、content／episode／lineage resume、source statistics、coverage report を提供する。
- `EnvironmentTopDeckCollector` は O3/O4 の既存 archive／typed transport が取得した leaderboard・submission・episode input を受ける。Kaggle client は追加していない。候補 score の内訳を manifest table へ残し、rules attestation がない限り archive-only である。
- `TeamBranchInventoryImporter` は `git ls-tree`、`git show`、blob SHA で local／`origin/*` refs を読み、worktree を checkout しない。fixture は実戦 Deck から除外し、Agent–Deck link は entrypoint の deck reference がある場合だけ `VERIFIED_LINK` にする。
- CLI は `scripts/run_competition_intelligence.py o5` の 5 command（environment acquisition、branch inventory/import、reconcile、coverage）を追加した。

## 実データ smoke

最終 branch inventory は local／`origin/*` の 38 refs（Deck あり 37、Agent あり 37）を読み、raw Deck candidate 255 件、raw Agent candidate 298 件を検出した。production exact Deck artifact 254 件、historical exact 1 件を 31 unique Deck identity へ統合した。Agent identity は 60 実装へ統合し、verified link 150、unresolved link 148、Agent without Deck 43 である。Agent の import／実行は O5-C 以降の選択的統合対象として残す。

既存 live capability probe は `PUBLIC_ARTIFACTS_ONLY`。認証、Leaderboard、own submission listing は利用可能だったが、own episode listing／own Replay は false、public submission／public episode／public Replay は未試験だった。既存 `ingest-public leaderboard` archive-only canary は schema `malformed_json` で quarantine され、manifest/raw archive は生成されなかった。したがって環境 candidate／Replay は 0 件で、typed blocker は `PARSER_BLOCKED`（Rules Gate も `UNVERIFIED_RULES_CONSTRAINT`）である。rules attestation は未検証なので environment collector は `CAPTURE_ONLY`、active classification は 0 件、O5-C candidate Archetype は空である。これは環境 Deck が存在しないという主張ではなく、取得 response の parser quarantine と Rules Gate の結果である。

team branch の明示的な training/evaluation permission は検出しなかった。branch artifact 680 件は `TEAM_SHARED_PENDING_PERMISSION` として保持し、training・evaluation・analysis は false のままである。O5-C 候補 0 件の理由は `NO_ACTIVE_EXACT_DECK`、`NO_ACTIVE_RUNNABLE_AGENT`、`PERMISSION_BLOCKED` であり、候補化には active exact Deck、runnable Agent、evaluation permission が必要である。

## 検証

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/competition_intelligence/test_external_sources.py \
  tests/competition_intelligence/test_o5_registry.py -q
# 30 passed

PYTHONPATH=src .venv/bin/python scripts/run_competition_intelligence.py o5 inventory-team-branches ...
# 38 branches, 255 raw deck candidates, 298 raw agent candidates; 31 unique Decks, 60 unique Agent implementations

PYTHONPATH=src .venv/bin/python scripts/run_competition_intelligence.py probe-external \
  --run-dir /tmp/o5-capability-smoke --target pokemon-tcg-ai-battle --mode live --timeout 20
# PUBLIC_ARTIFACTS_ONLY; leaderboard/own submissions/auth available, replay paths unavailable or untested
```

full regression は `/tmp/o5-registry-finalization/full.xml`、`full.log`、`full.exit` に保存し、`1511 passed, 5 warnings`、exit `0` を確認した。security/privacy/submission/package focused は `/tmp/o5-registry-finalization/security.xml` 等に保存し、`83 passed`、exit `0` だった。protected files は 20 件、差分 0、docs validation は 12/12、git diff check は pass である。run manifest は一時領域 `/tmp/o5-audit.Fl0xzu/deck_archetype_registry.json` にあり、competition/team data を Git へ追加していない。

## 統合・remote 再現性

O5 feature commit は `baa772d52aeb9370df14b364583bfdd94b0cdb26`、専用 integration branch の no-ff merge は `55b3ceb01f971475d4ea71dce1dc0c4004b8d79e`、canonical `feature/belief-guided-search` の no-ff merge は `22412f56ede22e353190a29b35414c3b694b6435` である。integration branch と canonical branch はそれぞれ origin へ push 後 `ahead/behind = 0/0` を確認した。

canonical merge 後も focused 34、全体 1511、security/privacy/submission/package 83 が exit `0` で完了し、docs 12/12 と protected tracked path の差分なしを確認した。canonical branch の clean clone（`/tmp/o5-registry-finalization/canonical-clean-clone-dir.txt`）では import、CLI help、branch inventory 25 refs／243 exact deck candidates／272 agent candidates、`CAPTURE_ONLY` environment acquisition、coverage report、focused 34、docs 12/12 を再現した。

## 制約と再開条件

- `CLASSIFY_AND_ANALYZE` は人間による Competition Rules attestation が `VERIFIED_RULES_CONSTRAINT` となった場合だけ使える。
- team branch の active analysis、evaluation、training は各 artifact の明示 permission manifest が必要である。
- O5-C の候補を出すには、許可済みの exact Deck source と、card metadata または根拠付き archetype label が必要である。未観測カードの補完や visualized deck order の利用は実装していない。
