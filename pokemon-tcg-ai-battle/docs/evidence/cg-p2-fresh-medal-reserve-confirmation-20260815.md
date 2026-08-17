# P2 fresh reserve medal confirmation — 2026-08-15

## 結論

24件のfresh medal holdoutで負差だったP2 research parentを、同じ公開 medal sourceの未使用 reserve 10件へ別base seedで確認した。candidate/control各160局、合計320局はすべて `DONE` / fault 0 だったが、P2はP1より `−0.9375pt`、candidate seat gapは `14.375%` でgate外となった。判定は `NOT_PROMOTABLE` であり、BestKnown更新ループの親は変更しない。

P1 `cg-lethal-target-v1`＋root deck、BestKnown、Champion、production、submission authorityは不変。CEM update、P3昇格、deck phase、training、longrun、commit、push、Kaggle提出は行っていない。

## 対象と契約

reserveは、meta manifestの公開 medal 36件から、既使用の2件と先行確認した24件を除いた次の10件である。

```text
medal_2849_bd32b8f7  medal_2850_952f9507  medal_2851_8543bee4
medal_2852_b31a602e  medal_2855_fba1f87c  medal_2856_458f87a5
medal_2857_0c1054dc  medal_2858_6644aa14  medal_2859_02ea57ae
medal_2862_65040fb4
```

- base seed: `50200000`
- repetitions: `8`（各 opponent × seat）
- workers / recycle: `12 / 64`
- candidate/control: 各160局、同一 opponent・seat・repetition・CABT seed
- meta provenance: `fresh_unused`
- artifact root: `runs/final-sprint-autonomous/cg-p2-fresh-medal-reserve-confirmation-20260815-v3/`

candidate/control の `(pair_key, seed)` 集合は各160件で完全一致した。全320行は `DONE` であり、faultは0件だった。

## 結果

| arm | W-D-L-F | score rate | seat 0 | seat 1 | seat gap |
|---|---:|---:|---:|---:|---:|
| P2 candidate | `76-1-83-0` | `47.8125%` | `32-1-47-0` (`40.6250%`) | `44-0-36-0` (`55.0000%`) | `14.3750%` |
| P1 control | `78-0-82-0` | `48.7500%` | `37-0-43-0` (`46.2500%`) | `41-0-39-0` (`51.2500%`) | `5.0000%` |

P2 − P1 は **`−0.9375pt`** で、candidate seat gapは5% gateを大きく超過した。fault0でも正差でもseat-safeでもないため、reserve確認からの昇格条件を満たさない。

summary SHAは `a0bd80db256b4a45775439f8d9a70ee485873161a2385b4925ef9ee3bacfebf5`、complete manifest SHAは `67b57c5724e4c5b2b3e008f8e97e818691ef852e6767500e2f5fc218d9228698`、ledger SHAは `92f9e9a9749a930345c7de64abd720fa68abfa6f9831760f11027461acdfac08` である。evaluator implementation SHAは `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`、runtimeは `171.412095s` だった。

## 解釈と次の条件

先行24件の `medal_0019_df6f7443` 共通STEP_LIMITとは独立に、このreserve 10件ではfault0だった。それでもP2の差は負で、seat imbalanceも大きい。したがって「先行runのfaultだけがP2を隠した」という解釈は支持されない。P2をCEMの次centerへ流したり、deck探索へ移したりせず、fresh transfer gateを閉じる。

次の再開には、P1またはP2の別policy surfaceを明示的に固定し、未使用metaで screen→独立複数block→fresh DEV/FINAL を通す。今回のreserve結果だけを根拠に同じP2をblind retryしない。

## 再現コマンド

```bash
TMPDIR=/tmp PYTHONPATH=.:src python scripts/run_cg_fresh_meta_confirmation_v1.py \
  --output runs/final-sprint-autonomous/cg-p2-fresh-medal-reserve-confirmation-20260815-v3 \
  --base-seed 50200000 --repetitions 8 --workers 12 \
  --worker-recycle-games 64 --execute \
  --refs medal_2849_bd32b8f7 medal_2850_952f9507 medal_2851_8543bee4 \
    medal_2852_b31a602e medal_2855_fba1f87c medal_2856_458f87a5 \
    medal_2857_0c1054dc medal_2858_6644aa14 medal_2859_02ea57ae \
    medal_2862_65040fb4
```

実行時runner SHAは `8343a82fa4dcfe1eaf164fad9ced56d10b9de078345f66a09e4f766025ef8cfe`、契約テスト SHAは `4c66d2ee8d2c87a438406bfd2c879b1a935e847d7477fd25e37a78cbff7883a0`。authorityは全てfalseであり、外部送信は行っていない。
