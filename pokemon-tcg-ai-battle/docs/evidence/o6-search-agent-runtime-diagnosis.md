---
title: O6 探索系2 Agent Runtime診断
date: 2026-07-21
base_commit: 5be120ceb0eb31aa6161dc8eab1cdf88180421cb
status: measured-not-static
---

# O6 探索系2 Agent Runtime診断

## 結論

`ozawa-metal-psychic-search@8472164` と `water-box-search@3759a983` の 20 秒 timeout は、静的推定（累積 JSON デシリアライズ）ではなく実測プロファイリングで原因を特定した。主因は両 agent が共有する native `cg` package（ctypes 経由で `cg/libcg.so` を呼ぶ `cg.api.search_step`）の単発呼び出しが 6〜16 秒以上ブロックすることであり、さらに再現試行でセグメンテーション違反（SIGSEGV）を確認した。machine-readable evidence は [o6-opponent-intelligence-v1/search_agent_runtime_diagnosis.json](o6-opponent-intelligence-v1/search_agent_runtime_diagnosis.json) を正とする。

## 測定方法

1. `faulthandler.dump_traceback_later(6-8秒, repeat=True)` を isolated subprocess 起動直後に仕込み、無応答時に全 thread の実行中 stack を強制出力させた。
2. `cg.utils.to_dataclass`／`json_to_dataclass`／`cg.api.search_step`／`search_begin`／`search_end`／`search_release`／`to_observation_class` を monkeypatch し、呼び出し回数・累積時間・最大時間をカウントした。
3. module import、agent module exec、deck 登録呼び出し（1・2回目）、`kaggle_environments.make()`、self-play `environment.run()` の各 phase 境界に timestamp を打った。
4. `cg/libcg.so` を `ldd`／`readelf --version-info` で静的検査し、`kaggle_environments` package 同梱の `libcg.so` と sha256 を比較した。

## 主要な実測結果

| 項目 | 実測値 |
|---|---|
| setup 中の `to_dataclass` 呼び出し | 426 回、合計 0.0004 秒、最大 1 回 0.000028 秒 |
| module import〜env make | 約 9.4 秒（`kaggle_environments` 自体の plugin discovery、agent 固有ではない） |
| 単発 `search_step` の観測ブロック時間 | 6〜16 秒以上（`faulthandler` の連続 dump で同一フレームを複数回捕捉） |
| クラッシュ再現 | ozawa-metal-psychic-search 2/2 run、water-box-search 1/1 run で SIGSEGV（exit 139） |
| `cg/libcg.so` sha256（両 agent 共通） | `ffd89bf9…` |
| `kaggle_environments` 同梱 `libcg.so` sha256 | `7acbfc7b…`（異なるバイナリ） |
| `ldd`／`readelf --version-info` | 未解決シンボルなし、glibc 2.39 で解決可能 |

## Gemini 独立レビューとの関係

`docs/evidence/o6-gemini-intelligence/timeout_agent_static_analysis.json`（Gemini worktree、未マージ）は「`to_dataclass` の累積コストが 10〜40 秒」という静的推定を示していたが、実測ではこの経路は無視できるほど高速であり、支持されなかった。一方、`claude_handoff.md` item 4（`SearchState`/`root_state` 解放漏れ疑い）は、今回観測した SIGSEGV と整合的である。詳細な対応は [o6-gemini-review-corrections.md](o6-gemini-review-corrections.md) を参照。

## 分類

- 運用分類: `UNSUPPORTED_RUNTIME`（両 agent とも）
- root cause: native `cg` library の memory-safety defect の疑い（owner 側 fix 相当）。ただし、このサンドボックス実行環境固有の増幅要因を完全には排除できないため、運用分類は `NEEDS_OWNER_FIX` へ確定変更せず、両方の可能性を記録した。
- Agent ロジック・探索 budget は変更していない。O6 の per-game isolated-subprocess adapter がこの crash の影響範囲を使い捨て subprocess 内に閉じ込めることを確認した（host process はクラッシュしなかった）。

## 再現コマンド

```text
# 診断スクリプトは ad hoc 投資的スクリプトであり、mage_ptcg.opponents には含めていない。
python3 <diagnosis-scratch>/diagnose_search_agent.py <materialized-source-root> main.py agent <timeout-seconds> '{}'
python3 <diagnosis-scratch>/profile_search_step.py <materialized-source-root> main.py <isolated-home> agent
```
