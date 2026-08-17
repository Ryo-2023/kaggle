# self-owned policy family v8 stability と CEM（2026-08-16）

## 結論

公式カード CSV と新規 stability role specification だけから、v7 broad-support とは別の `stability-v8` source epoch を生成した。8 件の self-owned deck＋P1 parameter overlay は合法性・静的安全性・full package smoke を通過した。P1 control と同一の balanced-v8 deckへ束ねた policy-only CEM は screen 216 局、独立再評価 144 局の計 360 局を全て `DONE`・fault 0 で完走した。

最有力 candidate `cg-p1-cem-g00-c06-5eaa501e4f94` は、screen で P1 control 比 `+8.3333pt`、独立再評価 2 block で `+33.3333pt / +12.5pt`（平均 `+22.9167pt`）だった。しかし `seat_safe=false`、`opponent_seat_safe=false` であり、厳格な promotion gateを満たさない。CEM後に候補を未使用 META_DEV／META_FINAL へ固定 holdout 検証したところ、両方で `+4.6875pt`（candidate 36/64 対 control 33/64、fault 0）へ転移したが、candidate seat gap は DEV `0.0625`、FINAL `0.125`で、`0.05` gateを超えた。

したがって、今回の正しい判定は `SOURCE_GENERATION_PASS / POLICY_IMPROVEMENT_REPRODUCED_BUT_STABILITY_GATE_FAIL / BESTKNOWN_UNCHANGED` である。P1、BestKnown、Champion、production、submission、deck phase、`cg_bestknown_loop_v1.py` 接続は変更していない。

## source generation

- plan: [`self_owned_cg_policy_family_v8_stability.json`](../../configs/meta_specialist/self_owned_cg_policy_family_v8_stability.json)（SHA-256 `cad29a9c58f8509e912a797c72a5ba56d7eedc8438c4bcab283b890a2a479a18`）
- deck spec: [`self_owned_cg_deck_spec_v6_stability.json`](../../configs/meta_specialist/self_owned_cg_deck_spec_v6_stability.json)（SHA-256 `27767729c5e095fd19d0c8798379e33d196e907798430a4a665f654d65c1e1f8`）
- source epoch: `self_owned_official_card_data_stability_v8_20260816`
- input boundary: `data/raw/EN_Card_Data.csv` と上記 deck spec。既存 artifact は canonical hash collision 監査だけに使用した。
- staged root: [`runs/cg-self-owned-cg-policy-family-v8-stability-20260816/`](../../runs/cg-self-owned-cg-policy-family-v8-stability-20260816/)
- factorial manifest SHA-256: `df19a6fb5a48874274e5448fc2571fdd131a3fa482bfcb61bdc44232f405be2a`
- 8 source は deck／policy identity とも相互に distinct、`parent_deck=null`、`public_parent_read=false`、authority 全 false。P1 parameter overlayを使うが、deckは公式カードデータから新規生成した self-owned deckである。

## smoke と seal

最初の staged intermediate rootを直接 evaluatorへ渡した試行は native `buffer full` となった。この rootは `cg` runtimeを含まないため、性能証拠へ算入しない。full `packages/` rootを渡す同一条件の再実行だけを採用した。

- full package smoke: [`runs/cg-self-owned-cg-policy-family-v8-stability-20260816-promoted/smoke_summary.json`](../../runs/cg-self-owned-cg-policy-family-v8-stability-20260816-promoted/smoke_summary.json)
- smoke summary SHA-256: `38bbf8524ac17b17c68588881e73c9416ebe644d946c476df19a38b3064b33c6`
- 16/16 `DONE`、fault 0、結果は 1W-15L（runtime gateの結果であり、性能昇格根拠ではない）
- promoted root: [`runs/cg-self-owned-cg-policy-family-v8-stability-20260816-promoted/`](../../runs/cg-self-owned-cg-policy-family-v8-stability-20260816-promoted/)
- pool manifest SHA-256: `deacc8f685e9d78ac2b196df2adb719c730e43c336d39901d1e1c19eae393245`
- fresh meta SHA-256: `f36ea1945b9d23c3b6a6cc2631e57300b523d14955bfaa788f9d02539bdd3d75`
- meta manifest SHA-256: `9e8a10747f9ae84992e36dd88960fad4070efa19ea854ca50d31360972d1a0e2`
- 通常 split: [`cg_self_owned_weekend_split.json`](../../runs/cg-self-owned-cg-policy-family-v8-stability-20260816-promoted/cg_self_owned_weekend_split.json)（SHA-256 `5c193e70dc0e0c57f73e7277ab3367f251b5de531dcb4645bf0849f11bc88058`）
- split は `META_TRAIN=6 / META_DEV=1 / META_FINAL=1`。smoke は全 sourceに対して行ったが、CEMの性能読み出しは META_TRAINだけに限定した。

## deck-bound policy CEM

policyとdeckの差を混ぜないため、candidate／controlとも balanced-v8-00 の deck file（SHA-256 `e4050c33a6e336d632bb4b837fc609a81d7f2e3ceba736b878831de82e1f3c2a`）へbindした。CEM splitは [`cg_self_owned_cem_split_v1.json`](../../runs/cg-self-owned-cg-policy-family-v8-stability-20260816-promoted/cg_self_owned_cem_split_v1.json)（SHA-256 `3cbfc30f4c72ed0c8f2dff0412de4683f0e5803a4a0830789ba1f619d249377a`）である。P1 policy source SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、evaluator SHAは `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`。

- campaign root: [`runs/cg-self-owned-cg-policy-family-v8-stability-20260816-cem/`](../../runs/cg-self-owned-cg-policy-family-v8-stability-20260816-cem/)
- campaign manifest SHA-256: `474a1245e4f61b2cf96a3052da49934585b5bfce0c748afa67f69e728a65e6d8`
- config: campaign seed `20261401`、1 generation、population／elite `8／2`、`META_TRAIN_ALL`、独立再評価 2 block、各 2 局／opponent／seat、`initial_scale_fraction=0.20`、risk-aware update
- screen summary: [`generation-0000/evaluation/summary.json`](../../runs/cg-self-owned-cg-policy-family-v8-stability-20260816-cem/generation-0000/evaluation/summary.json)（216/216 `DONE`・fault 0、SHA-256 `567554ca5730ba95f8c0c1802fad379c98aad17cfba76df78eb8d3834fde95d8`）
- independent summary: [`generation-0000/reevaluation/summary.json`](../../runs/cg-self-owned-cg-policy-family-v8-stability-20260816-cem/generation-0000/reevaluation/summary.json)（144/144 `DONE`・fault 0、SHA-256 `63fbdaa471b951d0f977a25440872be50d1154b6886b33d4268196f5268a9665`）
- generation results SHA-256: `b6aad058155fbfc24143e62d9472537f5cd82386357ed1079d98493600c0382a`

### c06 candidate

- candidate: `cg-p1-cem-g00-c06-5eaa501e4f94`
- parameter config SHA-256: `5eaa501e4f9478fbc1b250b178794644903aab6967461aaa5e6908dedec197f6`
- candidate policy SHA-256: `3680a58c9f63d0e8c2bee41bd4a7aef6bda6022a4c6aceb5a9f9a447eabdd8f8`
- screen: candidate `16/24` 対 control `14/24`、差 `+8.3333pt`
- independent repeats: `+33.3333pt`、`+12.5pt`、mean `+22.9167pt`、min `+12.5pt`
- independent seat rates: repeatごとに candidate `0.625/0.8333` 相当と大きく揺れ、`seat_safe=false`、`opponent_seat_safe=false`
- CEM new centerは c06／c00 eliteから計算された research-only centerであり、BestKnownやP1へ反映していない。

## 未使用 META_DEV／META_FINAL holdout

CEM選定後、splitの `META_DEV` と `META_FINAL` を初めて性能読み出しした。各 stage は同じ c06 candidate 対 P1 control、両 seat、各 arm 64局（stage全体128 evaluator rows）、fault-inclusiveである。holdout manifest complete SHA-256は `0458e9ed2900cd1f9c23db41c61c89b27e7b5e89580eca30242b103b60955668`。

| stage | 未使用 source | candidate | control | delta | candidate seat rates | 判定 |
|---|---|---:|---:|---:|---|---|
| META_DEV | `retreat-v8-05-73d1ae6ee11f` | 36/64 | 33/64 | `+4.6875pt` | `0.53125 / 0.59375` | seat gap `0.0625` で不通過 |
| META_FINAL | `setup-v8-02-a1ed39e882c2` | 36/64 | 33/64 | `+4.6875pt` | `0.625 / 0.5` | seat gap `0.125` で不通過 |

両 stage とも candidate／control fault 0。candidateの未知側スコア改善は再現したが、seat stability gateを満たさないため「P2昇格」ではない。holdout summary SHAは META_DEV `2b3df433ada6747b68037af717cec8a68702398efb72953ad07fcb2ce3687949`、META_FINAL `c36f0aaaad7ab4651db94a8529b5a51814017790fe276a5160461da6193b8f64`。

## 判定と次担当

今回の v8 は、self-owned sourceの生成方法と、CEM候補の性能改善が未使用 metaへ移ることを確認した。一方、改善幅は seat variance を伴うため、P2／P3研究 parent、BestKnown、Championへ昇格させない。v8 META_DEV／META_FINALは性能使用済みとして台帳へ固定し、同じ source／seed／c06 の blind retryは行わない。

次は c06近傍を狭い初期分布で探索する新しい source epochを別 seedで生成し、`legality → static safety → bounded fault0 → TRAIN-only CEM → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を再実行する。strict gateを通過するまでは deck alternating と `cg_bestknown_loop_v1.py`接続を開始しない。

