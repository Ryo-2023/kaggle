---
title: "v11 未使用 holdout と公開 multi-author intake の再監査"
date: 2026-08-16
status: evidence
---

# 結論

v8 c06 は v11 の未使用 `META_DEV`／`META_FINAL` へ転移したが、candidate の seat 差が大きく、BestKnown の昇格条件を満たさなかった。次の性能実験は同じ c06、同じ source family、同じ route の blind retry を行わず、未性能使用の新しい source を先に獲得・生成する。

## v8 c06 × v11 未使用 holdout

評価 root は `runs/cg-v8-c06-v11-unused-holdout-20260816/`。v8 c06 candidate と v8 control は同一 P1 deck に固定し、v11 promoted root の `META_DEV`／`META_FINAL` だけを参照した。各 stage は candidate／control を同じ paired seed 設計で 32 局ずつ評価し、candidate と control の性能選択には v11 の holdout 結果を使用していない。全 128 局は `DONE`、fault 0 だった。

| stage | candidate | control | delta | candidate seat rates | seat gap | 判定 |
|---|---:|---:|---:|---|---:|---|
| `META_DEV` | 18W-14L / 56.25% | 17W-15L / 53.125% | +3.125pt | 62.5% / 50.0% | 12.5pt | seat-safe 不成立 |
| `META_FINAL` | 22W-10L / 68.75% | 14W-18L / 43.75% | +25.0pt | 56.25% / 81.25% | 25.0pt | seat-safe 不成立 |

根拠ファイルは `META_DEV/summary.json`（SHA-256 `e77c29ae8fa448913f24d9e6ca79fc0562a9f5d41b0823f794922b90f20d18fc`）、`META_FINAL/summary.json`（SHA-256 `5666039c74e3f163b24fb0a860c5cecc65409ad7e2263c3856cfbab9d98e4335`）、complete manifest（SHA-256 `f0fea443ad027cb564e305c3131f74948ad9a95cf3d318a4f45b41a482fabfd6`）である。平均 delta は正方向だが、`seat_gap <= 5pt` と opponent×seat-safe を満たさないため、P2／P3、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py` 接続は不変とする。

## 新しい source 獲得方法の実行結果

公開 discovery 資産から、既存 CEM identity に無い作者を優先する `MULTIAUTHOR_EXPOSURE_FIRST_INTAKE_V1` を構成した。candidate は Emanuellcs、Nursrijan、Res1235 の 3 系譜で、tar SHA と source URL を config に固定した。config は `configs/meta_specialist/cg_kaggle_kernel_meta_public_fresh_epoch8_multiauthor_20260816.json`（SHA-256 `f24672fefccb97a84170b8c9558205b0d7bf01c4309cbcef307daa2aad29edea`）である。

intake は `scripts/generate_kaggle_kernel_meta_v1.py` で実行し、ネットワーク、Python import、Git mutation、CABT は発生させていない。report は `runs/cg-kaggle-kernel-meta-intake-public-fresh-epoch8-multiauthor-20260816/intake_report.json`（SHA-256 `94fdd656e29cdf805cb207d3f054463a3381f9d781c051855f53697762a5295e`）。結果は `accepted_count=0` であり、理由は次の通り。

| source | reject reason |
|---|---|
| `kaggle_emanuellcs_deck_aware_20260816` | tar root に `deck.csv` が無い |
| `kaggle_nursrijan_mega_lucario_20260816` | official catalog に対する deck 合法性不成立 |
| `kaggle_res1235_mega_lucario_20260816` | `agent` entrypoint 不在 |

この結果は source generation の性能失敗ではなく、性能投入前の intake gate による fail-closed である。したがってこの epoch を smoke、CEM、DEV／FINAL、BestKnown loopへ接続しない。ローカル discovery に残る候補は既存 source identity、違法 deck、dynamic execution、entrypoint／filesystem／network 境界のいずれかで止まるものが多く、次に必要なのは同じ資産の変形ではなく、合法・静的安全・実行可能な新しい permission 済み source snapshot の獲得である。

## 次の source protocol

1. source intake 前に author／source branch／source policy SHA／canonical deck SHA を exposure ledger へ登録する。
2. `META_TRAIN`、`META_DEV`、`META_FINAL` を source reservation 時点で分離し、DEV／FINAL は smoke を含めて CABT 性能探索へ渡さない。
3. TRAIN 候補は作者系譜と canonical deck を分散させ、同一 policy SHA の再包装を一件も許可しない。
4. 3 件以上の新規 source が legality・static safety・bounded fault0 を通過した後でのみ P1 smoke を開始する。
5. `TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` の全 gate 通過後だけ `cg_bestknown_loop_v1.py` へ接続する。

現在は protocol の intake 段階で停止しており、heavy CABT の起動権を使用していない。新規 permission source が得られるまで、既評価 source の再試行や deck blind sweep は行わない。

## `ono-` の provenance

`ono-` は公開 kernel 作者名ではない。local Git identity `bfe-lab-ono <ono.ryosuke.36t@st.kyoto-u.ac.jp>`、branch `agents/ono-cg-lethal-v1`、commit `1965b42b028f10960d08ccb4980be5b76946f98b`（`feat(submission): cg lethal提出候補を封印`）に由来するローカル識別子である。現行 P1 policy SHA は `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA は `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`。root deck bytes と同一の local snapshot は複数あるため、repo 証拠だけで単一の公開元 kernel を特定することはできない。

commit、push、Champion 変更、Kaggle 提出は行っていない。
