---
title: O6 Gemini Independent Review — Claude 検証結果
date: 2026-07-21
base_commit: 5be120ceb0eb31aa6161dc8eab1cdf88180421cb
status: review-input-not-canonical
---

# O6 Gemini Independent Review — Claude 検証結果

## 結論

`pokemon-tcg-ai-battle-o6-research-gemini` worktree（branch `feature/o6-intelligence-research-gemini`、`origin/main`起点）の成果物 6 件を review input として検証した。同 branch はO6へ **merge していない**。`docs/evidence/o6-gemini-intelligence/claude_handoff.md` の指示に基づき、timeout 原因は実測で裏付けを取った（[o6-search-agent-runtime-diagnosis.md](o6-search-agent-runtime-diagnosis.md) を参照）。一方、`public_source_inventory.json` と `team_agent_strategy_profiles.json` には未検証・不整合な値が含まれ、そのままでは正典へ取り込めない。

## 1. timeout 原因（実測により概ね支持、ただし主要因は異なる）

Gemini の静的推定は「`to_dataclass` の累積オーバーヘッドが 2400 ステップで 10〜40 秒」というものだった。実測（monkeypatch カウンタ＋`faulthandler.dump_traceback_later`）の結果：

- setup 中の `to_dataclass` 呼び出し 426 回は合計 0.0004 秒（1 回最大 0.000028 秒）であり、単体呼び出しは高速。Gemini の「累積オーバーヘッド」仮説は反証された。
- 実際の停止点は、単一の `search_step`（`lib.SearchStep` を ctypes 経由で呼ぶ native call）が 6〜16 秒以上戻らないことに起因する。
- 2/2（ozawa-metal-psychic-search）および 1/1（water-box-search）の再現試行で、native library 呼び出し後に **セグメンテーション違反（SIGSEGV, exit 139）** を確認した。これは Gemini が claude_handoff.md item 4 で指摘した `SearchState`/`root_state` の解放漏れ疑惑と整合する、より重大な発見である。
- 両 agent が bundle する `cg/libcg.so`（sha256 `ffd89bf9…`）は、`kaggle_environments` パッケージ自体が同梱する `libcg.so`（sha256 `7acbfc7b…`）と **異なるバイナリ**である。`cg/api.py`／`cg/utils.py` は kaggle_environments 配布物に含まれておらず、team 側で書かれた ctypes binding である。
- `ldd`／`readelf --version-info` では glibc/libstdc++ の未解決シンボルは無く、単純なバイナリ互換性欠如が原因ではない。

詳細と生ログパスは [o6-search-agent-runtime-diagnosis.md](o6-search-agent-runtime-diagnosis.md) を正とする。`SEARCH_NUM_WORLDS`/`SEARCH_MAX_CANDIDATES`/`ROLLOUT_MAX_SELECTS` の削減提案は適用していない（agent ロジック変更は範囲外であり、かつ finding 1 により core bottleneck への効果が薄いため）。

## 2. Public Source Inventory の修正指示への対応

`docs/evidence/o6-gemini-intelligence/public_source_inventory.json`（Gemini worktree側、未マージ）について：

- **`content_hash_candidate` は正式 SHA-256 として採用しない。** 全 7 件が 8 桁 16 進数（32bit 相当）であり、SHA-256（64 桁）ではない。Gemini worktree 内に実体アーティファクト（notebook／main.py の実バイト列）はキャッシュされておらず（`data/` は `.gitkeep` のみ）、再計算するための取得済みファイルが存在しない。本セッションでは live network 取得を行っていない（タスク指示上も必須ではない）ため、正式 SHA-256 は **UNVERIFIED** のまま据え置く。
- **`romanrozen_strongstart` の `title` に含まれる "LB 950" と `relevant_rank: "~950"` は分離されていない。** "LB 950" は notebook 作者自身の自己申告タイトル文字列であり、score なのか rank なのか本文からは確定できない。これを rank として断定する記載は避けるべきである。
- **license は明示的な出典確認なしに `"Kaggle Competition Rules / Apache 2.0"` 等と記載されている。** 本セッションでは各 notebook の license badge を直接確認していないため、`UNKNOWN` として扱うべきである。
- **`itsuki9180_lucario_jp` と `tomatomato_archaludon` は `fidelity: DECK_FAITHFUL`** で、deck が別ソースからの模写または notebook セルからの再構成である旨が自由記述で言及されているが、構造化フィールドとして `deck_provenance: RECONSTRUCTED` 等の形で `EXACT` と明確に区別されていない。

これらは Gemini 側 JSON を書き換えず、review 所見としてここに記録する。正典へ取り込む場合は、上記を是正した新しい machine-readable ファイルを別途作成する。

## 3. Strategy Profile のカード ID 不整合

`docs/evidence/o6-gemini-intelligence/team_agent_strategy_profiles.json` 内で、カード ID `1122` が以下の 2 箇所で異なる名称に対応付けられている（矛盾）：

- 353 行目（自由記述）: `"Nest Ball (ID: 1122)"`
- 410 行目（構造化カードリスト）: `{"id": 1122, "name": "Pokegear 3.0", ...}`

本リポジトリには canonical card registry（ID→名称のマッピングファイル）が存在せず（`data/` はカードデータを含まず gitignore 対象、`kaggle_environments` の `cabt` 同梱物にも card レジストリ CSV/JSON は見つからなかった）、どちらが正しいか本セッションでは断定できない。**この ID 1122 の名称は `UNVERIFIED — CONFLICTING` として扱い**、canonical card registry が入手可能になった時点で再検証すること。

## 4. 扱い

- Gemini worktree（`feature/o6-intelligence-research-gemini`）は merge していない。
- 上記の是正が完了するまで、`public_source_inventory.json` と `team_agent_strategy_profiles.json` の数値（rank、license、content hash、card 対応表）は O6 の Population identity や Public Evidence Inbox 分類の根拠として使用しない。
- `claude_handoff.md` の timeout 分析は、実測により方向性は支持されたが、根本原因（native library crash）はより重大であるため、そのまま採用せず本ドキュメントおよび [o6-search-agent-runtime-diagnosis.md](o6-search-agent-runtime-diagnosis.md) を正とする。
