# FINAL-SPRINT Rule v0 固定ポリシー × deck interaction（2026-08-14）

## 結論

Rule v0 を固定し、既存の Archaludon 系 deck を submission-compatible 候補として screen した。12 opponent の weighted 48 では `ae3075` が親を +14.583pt 上回ったが、同じ seed 系列で broad common24 96へ拡張すると **親20/96（20.833%）に対し ae3075 は8/96（8.333%、−12.500pt）**へ反転した。したがって候補は昇格せず、384/longrun は起動しない。48局の点推定を性能改善と呼ばない、という Final-Sprint 契約を満たす結果である。

| arm | 条件 | W-D-L-F | score | 判定 |
|---|---|---:|---:|---|
| parent | Rule v0 × root deck、12 opponents、48 | 3-0-45-0 | 6.25% | control |
| ae3075 | Rule v0 × Archaludon mutation、12 opponents、48 | 10-0-38-0 | 20.833% | 48局ではpositive、guardrail待ち |
| parent | Rule v0 × root deck、common24、96 | 20-0-76-0 | 20.833% | control |
| ae3075 | Rule v0 × Archaludon mutation、common24、96 | 8-0-88-0 | 8.333% | **candidate-only / STOP** |
| b92a3b55 | Rule v0 × Archaludon mutation、12 opponents、48 | 0-0-0-48 | — | AGENT_INVALID、性能値不採用 |

## 実験条件と identity

- policy: Rule v0、policy SHA `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- broad config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- opponent pool SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- 48局: 12 opponents × 両seat × repetition 2、base seed `23200000`
- common24: 24 opponents × 両seat × repetition 2、base seed `23210000`
- workers=1 / recycle=16（先行並列rootで1 faultが出たため、同一条件の再測定は一時的に直列化）
- 全採用armで DONE/fault0、draw0、seat supportあり。native action/teacher label/private fieldは保存・学習利用していない。authorityは training/promotion/submission 全false。

### 48局 root

`runs/final-sprint-autonomous/final-sprint-rule-v0-submission-deck-screen-v1-20260814/parent-root-48-serial-v2/`

- deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- summary `b8146fee7603f445c60ff85da75aac423491447198dbca60cc15fd49581f4d58`
- manifest `be4f1dd9915ac60d0cd051dd0592c9a3a660d8bd026cfcb7c7cb827bf851a2fb`
- ledger `b4df7ac54d342803e3b07b01b8df471c9ffaeef718a72e42d6b7f15d09169d11`

### 48局 ae3075

`runs/final-sprint-autonomous/final-sprint-rule-v0-submission-deck-screen-v1-20260814/ae3075-archaludon-48-serial-v1/`

- deck SHA `95fecbb5041f1781b197ff5ba5e81adff5bec4605301b6d82bca7beb0fe919ef`
- summary `5146501ad29655bfcdc509320046eed1282732c2ec4b810fc649343a3d38d908`
- manifest `be4f1dd9915ac60d0cd051dd0592c9a3a660d8bd026cfcb7c7cb827bf851a2fb`
- ledger `bad4bf8598f482f3387484ff1f306592e2da973743c0c1d9519aca4a1fd74438`
- 48局差: `+7 wins / +14.583pt`（親との差）。これは discovery のみであり、promotion根拠ではない。

### common24 96親 / ae3075

親 root は `runs/final-sprint-autonomous/final-sprint-rule-v0-submission-deck-screen-v1-20260814/parent-common24-96-serial-v1/`、ae root は `runs/final-sprint-autonomous/final-sprint-rule-v0-submission-deck-screen-v1-20260814/ae3075-common24-96-serial-v1/`。

- parent summary `02ae029be0584e07df2df929a67be4acddbea7eae55d7f96db36fa0bb9f726b1` / ledger `b3b15fd8f654308f0648a9fcc7a6d89ce9e5915807df26a47bf3d7e1374a47c4`
- ae summary `dcf38e305e65fd165b1dbcb43a92ae2df247a3e17c7d95682434c7fdef23790e` / ledger `db321c14e68e28c3ba068f785de255dd4314489998d5feeac3b4884104cfe39d`
- 同一 broad 24、同一 seed schedule、同一 evaluator。ae は seat0 6勝/48、seat1 2勝/48で、親の seat0/seat1 各10勝/48を下回った。

## 無効armと fault 診断

先行並列 parent root `runs/final-sprint-autonomous/final-sprint-rule-v0-submission-deck-screen-v1-20260814/parent-root-48/` は47 DONE・1 FAULTだった。faultは `aristophanivan_probabilistic` seat0 repetition1 seed `23200041` の `DeckValidationError: deck must contain exactly 60 cards, got 0`。該当deck fileは実体60行で root deck と同一 SHA `2a541d7b…`、fresh workers=1の同一 seed probeは DONE だったため、初回rootは性能値として不採用にした。faultを勝ち・負けへ変換せず、別rootで再測定した。

`b92a3b55…` は deck 構造を `main.validate_deck` で通過するが、Rule v0との48局は workers=1でも全48 `AGENT_INVALID; cabt terminal result unavailable`。これは Rule v0/runtime と当該 deck の互換性未成立として閉じ、0%の性能結果には変換しない。native policy側で得た b92a の結果を Rule v0 の結果へ流用しない。

## 2×2 / promotionへの影響

- `SUBMISSION_PROMOTION` の BestKnown は Rule v0 × root deck 11/96 = 11.4583%。
- `PERFORMANCE_TARGET` の V4 seed1 × Archaludon は 54/96 = 56.25%。
- Rule v0 × Archaludon は別 seed の24-opponent 96で15/96 = 15.625%という診断値もあるが、今回の同一 screen では root deck親が20/96、ae候補が8/96だった。seed/pool差があるため、これらを一つの厳密なpaired推定へ混ぜない。
- V4 × root は Archaludon strict qualification が core `[169,190]` を要求するため `CLOSED`。資格迂回や偽core追加はしていない。

従って現時点の提出主線は **P0=Rule v0、D0=root deck** のまま。ae3075、b92a、native-only deck mutation は submission candidateへ昇格しない。次の候補は既存 hard-negative と multiset を除外した新しい bundle-compatible surfaceを、必要なら workers=12（ResourceGovernorの正常時上限）で 48→96 の順に1本だけ screenする。今回の ae surface は common24 で明確に崩壊したため再試行しない。

## 再現・検証

- runner subject-deck配線TDD: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src TMPDIR=/tmp pytest -q tests/test_performance_first_arena_v1.py` → 5 passed（現runner SHA `99cbc5f062e053aa07ea40fab1751f1a66e793defb4c9fb167bb5016d0e4d6cf`、test SHA `47a3e23dfbab405f77a178412869f511d9aac1e249e5ceda2b4c968cc7f7f7a2`）。
- 採用した5 rootは各 summary/ledger を再読し、DONE/fault0/seed/seat/opponent identityを確認済み。
- この evidence JSON: `docs/evidence/autonomous-final-sprint-rule-v0-deck-interaction-20260814.json`。
- commit / push / Kaggle submission / Champion変更 / permission変更なし。
