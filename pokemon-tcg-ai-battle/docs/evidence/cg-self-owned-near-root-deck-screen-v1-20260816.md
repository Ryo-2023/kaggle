# self-owned near-root deck screen v1（2026-08-16）

## 結論

公式カードCSVと新規役割仕様だけから、現行root deckとcanonical hashが一致しないself-owned scratch deckを4件生成し、P1 policyを固定してmatched CABT screenを行った。4件とも合法性・package verifier・bounded runtimeを通過したが、独立seed確認で陽性を再現できなかった。BestKnown、P1、root deck、Champion、production、submissionは変更しない。

判定は `SELF_OWNED_DECK_GENERATION_PASS / SCREEN_SIGNAL_NOT_REPRODUCED / POLICY_DECK_NO_UPDATE / BESTKNOWN_UNCHANGED` である。

## 固定した比較対象

- policy source: `runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package`
- P1 policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- control root deck canonical/file SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- card database SHA: `a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- opponent config: `configs/meta_specialist/performance_first_broad_pool_v1.json`（24 refs）
- all candidate manifests: `parent_deck=null`、`public_parent_read=false`、authority全false、公開canonical collision 0

候補はrootのdeck.csvをコピーして作ったものではない。役割仕様は公式カードIDだけを入力とし、rootと同じ役割配分を基準に1スロットだけ別カードを許す新規recipeとして保存した。ただし性能上の近傍仮説を検証するため、結果のdeck multisetはrootとの差分1枚になっている。

## 生成物

| candidate | 1枚差分（root→candidate） | canonical deck SHA | package policy SHA | package manifest SHA |
|---|---|---|---|---|
| `ace_swap` | Maximum Belt `1158` ← Hero's Cape `1159` | `68e151c4c7875b2fb5aa941c1f94f1a03d11fe2171d25be08aef31fd536b0f2b` | `792e24996768a8430d4597e9b77337394db059d896b9b4a17eb33ac3ace27bde` | `2b9235d7ff0b7ba28cf95b03cafa2f1756522c4210f2e757c49140aeab860377` |
| `kieran_swap` | Kieran `1191` ← Carmine `1192` | `b43b50deb408057be474452dfd2e6a279abfa00521d61f47cc9b530ef4da7c85` | `2b267fc3774e7bcc8f1b5198af910d4144051a95ef02e2af5621d9e4d49154e2` | `5af7ecb3372de7c50a511f55fd350e6c17a34e2c3d3128829ce372d9a0ba156a` |
| `ultra_ball_swap` | Ultra Ball `1121` ← Dusk Ball `1102` | `762b632b686ab69211c8dda5ea586ed6d91a034cc51f5a143c488476e8a380e6` | `018754958b74e4f597752b25d52ed15f979218d6721799964d30ceb5afe3f892` | `04d107ea1e221358c6511e38de8af0f27697f3c12fe0a5464ad565b4c77af557` |
| `community_center_swap` | Community Center `1242` ← Gravity Mountain `1252` | `5267aab6d8b14c74fb0b3980d2bccce2885a315d781cedd9c28b31ed96897c72` | `cfc6bd136d83d635501104066bdf793ce1ce673f65a42bf8d363db9ca4584323` | `7db9f4bfae096322f8d986e0c825ac5523b917309fe3e362be928ad2f4ff3714` |

生成rootは `runs/cg-self-owned-near-root-deck-screen-v1-20260816/`。recipeは同rootの `recipes/`、各packageは各candidateの `package/` にある。

## CABT結果

### 低コストscreen

各候補について24 opponent × 2 seat × 1 game、candidate/control各48局、合計96局を別seedで実行した。全96局が `DONE`・fault 0・draw 0だった。

| candidate | seed | candidate W-L | control W-L | delta | 判定上の注意 |
|---|---:|---:|---:|---:|---|
| `ace_swap` | `2026089611` | 10-38 | 6-42 | `+8.3333pt` | candidate seat score 12.5% / 29.17%、seat gap 16.67% |
| `kieran_swap` | `2026089612` | 8-40 | 7-41 | `+2.0833pt` | 小差であり独立確認が必要 |
| `ultra_ball_swap` | `2026089613` | 11-37 | 14-34 | `−6.2500pt` | 初回から負差 |
| `community_center_swap` | `2026089614` | 5-43 | 8-40 | `−6.2500pt` | 初回から負差、candidate後攻4.17% |

### 独立seed確認

screen上位2件だけを、同じ24 refsの別seed・2 games/seat（candidate/control各96局、合計192局）で確認した。全192局が `DONE`・fault 0・draw 0だった。

| candidate | seed | candidate W-L | control W-L | delta | 結論 |
|---|---:|---:|---:|---:|---|
| `ace_swap` | `2026089711` | 14-82 | 19-77 | `−5.2083pt` | screen反転、昇格不可 |
| `kieran_swap` | `2026089712` | 15-81 | 20-76 | `−5.2083pt` | screen反転、昇格不可 |

screen summary／completion manifest SHA:

- `ace_swap/screen-1g`: `eefa97c8739a5963727d119cd2a4ea3475cd744c9f0adba85ec10832467cf0e9` / `87dc7c6d2dfd56be543a0db9220de3cf2c169c5e842e812927b772b4b3b4b130`
- `kieran_swap/screen-1g`: `0bb79c93b4ce8bfc5df275a59a8d9e24157b3d4343f251991ccf29a094f4ef3a` / `7887aa3da2559b000b41a9cf6e98175ce346686a8a3b8a91f4b3cc8b99e79a07`
- `ultra_ball_swap/screen-1g`: `cc34814918c237c7acb81a8270a2ee71d818d31f7d98366f1714e2f513140e0e` / `52f125f67ae0d750218753130a6fe4c748c86cc0bce9d9a69d5f276e52814951`
- `community_center_swap/screen-1g`: `9cfc8f24c8335c1f7adcfbd235cdbfcab5e629e323fbe108069de94b3ba3372a` / `48db701dce47b09f6bbf00bd259fbfd0fe188a4291f1e8822dccb21454968586`
- `ace_swap/screen-2g-confirm`: `66b7dd5f995b918998c488f1ac2291d9135045f42a1eb669da68cb048dc4fad6` / `f9b5508723e50d13e4c19493836f762d1512b462483d3abfbb451cb8e92000ee`
- `kieran_swap/screen-2g-confirm`: `cbdb4f374e60fb7fe1a3f59d1092578a6d85d41455a12b87f0531fc68e4aa1e5` / `91f059d603eb2a098fde0c44d2955d9f75656dfd061bb2a107c97be7c6afdd6a`

再現コマンドは以下の形式で固定した。

```bash
python scripts/generate_self_owned_cg_deck_v1.py --execute \
  --output runs/cg-self-owned-near-root-deck-screen-v1-20260816/<candidate> \
  --spec runs/cg-self-owned-near-root-deck-screen-v1-20260816/recipes/<candidate>.json \
  --source-package runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package \
  --public-scan-root opponents --seed <seed> --ordinal 0

python scripts/run_self_owned_cg_deck_screen_v1.py --execute \
  --candidate-package runs/cg-self-owned-near-root-deck-screen-v1-20260816/<candidate>/package \
  --control-package runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package \
  --config configs/meta_specialist/performance_first_broad_pool_v1.json \
  --output runs/cg-self-owned-near-root-deck-screen-v1-20260816/<candidate>/<screen-root> \
  --base-seed <seed> --games-per-opponent-seat <1-or-2> --workers 2 --worker-recycle-games 8
```

## 次の判断

このepochの候補・seed・参照poolは性能使用済みとしてblind retryしない。1枚だけのdeck近傍変更は、screenでは上振れしても独立CABTで反転した。従って次の主線は、同じ近傍deckを増やすことではなく、未使用metaを生成時に分離した別policy lineage／別runtime-safe rendererを作り、`fault0 → TRAIN-only → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を満たすこととする。

今回のartifactはself-owned deckの提出closure候補ではあるが、性能昇格候補ではない。BestKnown更新、`cg_bestknown_loop_v1.py`接続、Champion変更、production変更、submission、commit、pushは行っていない。
