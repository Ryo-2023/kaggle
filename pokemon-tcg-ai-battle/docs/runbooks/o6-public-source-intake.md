---
title: O6 Public Opponent Source Intake — Quick Start
date: 2026-07-21
base_commit: 5be120ceb0eb31aa6161dc8eab1cdf88180421cb
---

# O6 Public Opponent Source Intake — Quick Start

本書は `mage_ptcg.opponents public-source` サブコマンドで Public Source Corpus（Repository Snapshot 経路）のメタデータのみを O6 側へ取り込む最短手順を示す。設計・実測 Evidence は [../evidence/o6-opponent-intelligence-v1.md](../evidence/o6-opponent-intelligence-v1.md) の Phase B 節を正とする。

## スコープ（重要）

- 取り込むのは分類 JSON（`source_manifest`／`code`／`deck`／`behavior`／`provenance`／`permissions`／`technical_validation`、任意で `classification`／`deck_validation`／`hashes`）のみ。**Public Agent の raw／extracted コードは一切取り込まない**（Full Corpus はリポジトリ外に留め置く）。
- この経路には Public Agent を実行するコマンドが存在しない。`technical_validation` は輸入時に全項目 `NOT_RUN` であることを検証し、それ以外の値を持つソースは import 自体を拒否する。
- Candidate state は `NATIVE_OPPONENT_CANDIDATE`／`DECK_STANDARD_PILOT_CANDIDATE`／`SURROGATE_CANDIDATE`／`REVIEW_REQUIRED`／`BLOCKED` の5値のみ。`NATIVE_OPPONENT`／`VALIDATED`／`APPROVED`／`PUBLISHED`（Team pipeline専用の状態）へは絶対に進めない。
- Live Kaggle 取得（自動発見・ダウンロード・増分同期）は未実装。現在動作するのはコーパス内 Repository Snapshot のオフライン import のみ。

## 手順

```bash
# 1. Corpus のメタデータを import（raw/extracted コードは読み込まない）
python -m mage_ptcg.opponents public-source import \
  --corpus <public-source-corpus-root> --output-dir <public-source-registry-dir>

# 2. 取り込み済み Source を一覧
python -m mage_ptcg.opponents public-source list --output-dir <public-source-registry-dir>

# 3. 個別 Source の詳細（permission scopes, candidate state, classification 等）
python -m mage_ptcg.opponents public-source inspect <source-id> --output-dir <public-source-registry-dir>

# 4. 保存済みレコードの改ざん検知（semantic hash 再計算）
python -m mage_ptcg.opponents public-source verify-metadata --output-dir <public-source-registry-dir>

# 5. Permission gate（REVIEW_REQUIRED が1件でもあれば exit code 6）
python -m mage_ptcg.opponents public-source check-permissions --output-dir <public-source-registry-dir>
```

現行 7 Source はいずれも `explicit_license: UNKNOWN` であるため、`check-permissions` は必ず exit code `6` で終了する。これは失敗ではなく「未レビューの公開ソースが残っている」ことを示す意図された fail-closed 挙動である。

## Permission Scope

Public Source の permission は Team Source と同一の 6 scope 語彙（`evaluation`／`training_data_generation`／`strategy_analysis`／`team_redistribution`／`public_redistribution`／`submission_bundle`）を再利用する。`explicit_license: UNKNOWN` の場合、以下の固定表と一致することを import 時に検証する（一致しない場合は import を拒否する）。

| scope | UNKNOWN license の値 |
|---|---|
| evaluation | REVIEW_REQUIRED |
| training_data_generation | REVIEW_REQUIRED |
| strategy_analysis | ALLOWED_METADATA_ONLY |
| team_redistribution | REVIEW_REQUIRED |
| public_redistribution | DENIED |
| submission_bundle | DENIED |

## Candidate State の導出

`derive_candidate_state()`（`mage_ptcg.opponents.public_source`）は `source_id` を一切参照せず、`code_availability`／`deck_fidelity`／permission scopes／`blocked_sources.json` のみから rule-based に導出する。Corpus 側の `review_override.json` は適用されるが、`review_override_applied` フィールドへ必ず記録され、`BLOCKED` は override で上書きできない。`VALIDATED`／`APPROVED`／`PUBLISHED`／`NATIVE_OPPONENT`（末尾に `_CANDIDATE` を伴わない語）を override 先として要求した場合は import 自体を拒否する。

## やってはいけないこと

- `sources/<id>/raw/`／`extracted/` を読み込む、または Team Population のビルドへ混入させる。
- Public Source を `mage_ptcg.opponents.core` の `build_population()`／`OpponentRegistry` へ接続する（本 Phase では未接続、意図的）。
- `check-permissions` が exit 6 の状態のまま、当該 Source を評価・学習・再配布・提出物へ利用する。
- 未監査の Live Acquisition 経路を実装・有効化する。
