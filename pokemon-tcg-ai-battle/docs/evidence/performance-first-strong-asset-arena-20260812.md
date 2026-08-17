---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-12
scope: performance-first-final-sprint-strong-asset-arena
authority: research-diagnostic-only
---

# Final Sprint broad arena 実測

## 結論

現行の共通 broad pool 24 pair、各 opponent の両 seat、各 seat 2 局（1 arm 96 局）を、同一の現行 evaluator identity で完走した。3 arm とも `96/96 DONE`、`fault=0` で、実行器・deck binding・worker recycle を含む短期 arena の健全性は確認できた。

ただし、これは Strong Asset の GlobalBestKnown を確定する試験ではない。engine seed setter が存在せず、candidate と baseline は game-level paired ではないため、結果は `independent_stratified_not_game_paired` として扱う。また各 cell は 4 局（opponent × seat）に過ぎず、meta-weighted promotion、BestKnown 更新、longrun、Kaggle 提出を許可する規模ではない。

| arm | deck / policy identity | games | wins | losses | draws | faults | score | seat 0 | seat 1 | result |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Rule v0 root | current root `deck.csv` + `main.py` default Rule v0 | 96 | 13 | 83 | 0 | 0 | 13.54% | 7/48 | 6/48 | safety baseline; performance weak |
| Wave6 seed0 | Archaludon V4 seed0 checkpoint | 96 | 49 | 47 | 0 | 0 | 51.04% | 24/48 | 25/48 | diagnostic candidate |
| Wave6 seed1 | Archaludon V4 seed1 checkpoint | 96 | 55 | 41 | 0 | 0 | 57.29% | 28/48 | 27/48 | diagnostic candidate |

この結果だけからは、Wave6 seed1 を Champion や BestKnown として選択しない。seed0 と seed1 の差は 6 勝（6.25 points）あり、engine RNG を共有していないため、seed robustness と pool generality を同時に証明していない。Rule v0 は「提出時の合法性・fault fallback の基準」であり、Strong Asset の性能 teacher ではない。

## 固定 identity

- evaluator implementation identity（manifestに記録）: `cb15090f41dcf54072c717621d65935a2747e0b529db38aa82fdca069b4081bc`
- broad pool config: `configs/meta_specialist/performance_first_broad_pool_v1.json`
- broad pool config SHA-256: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- opponent pool manifest SHA-256: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- evaluation pairing: `independent_stratified_not_game_paired`
- `engine_seed_supported`: `false`
- start method: `spawn`
- max workers: `8`
- worker recycle: `16 games`
- BLAS/OpenMP/NumExpr/VECLIB thread caps: `1`

共通 pool は 24 の policy ID を含む。各 ID は 2 seat × 2 repetition で 4 局となる。deck と policy は pair identity として保持し、同じ deck を使う別 policy を一つへ統合していない。現行 pool は local-eval-only の資産を含むため、評価のために使えることと、training-local / submission 用に許諾されていることを混同しない。

## Artifact と SHA

### Rule v0 root

- run directory: `runs/meta-specialist-performance-sprint-v1/root-arena-broad-96`
- manifest SHA-256: `cb3175e3f87215923fa561ea602bc286268d517186c3f15ea4216835d173a27b`
- summary SHA-256: `a1792efb517f6ffe1037af69b40577d7a5459b81f04de7f2c379ca1adf3d5a27`
- summary: 96 completed, 13W/83L/0D, fault 0, total runtime 34.118994 s
- current root deck raw SHA-256: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- package fallback archive (別 artifact): `runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck/submission.tar.gz`, SHA-256 `da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a`

### Wave6 seed0

- run directory: `runs/meta-specialist-performance-sprint-v1/wave6-seed0-broad-96`
- manifest SHA-256: `f4181637ae3e0a71eac25592b211c6179c06079d9f19f129ededb2122760120a`
- summary SHA-256: `e582fe7bb06849c9ff1ddd5fe63da92faf73bdebf9525d1af6e2ef84774180f0`
- summary: 96 completed, 49W/47L/0D, fault 0, total runtime 166.325955 s
- source checkpoint file SHA-256: `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de`
- source checkpoint tensor SHA-256: `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a`

### Wave6 seed1

- run directory: `runs/meta-specialist-performance-sprint-v1/wave6-seed1-broad-96`
- manifest SHA-256: `f4181637ae3e0a71eac25592b211c6179c06079d9f19f129ededb2122760120a`
- summary SHA-256: `96c8e9e21faf668f33e18a2db6f2c7953598b251290d8f9075c70a8875e91125`
- summary: 96 completed, 55W/41L/0D, fault 0, total runtime 180.413962 s
- source checkpoint file SHA-256: `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6`
- source checkpoint tensor SHA-256: `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a`

Wave6 seed0 と seed1 の manifest は、共通 pool・protocol・evaluator identity を示す目的で同じ内容になっている。ゲーム内の CABT RNG は共有されていないため、同じ `game_id` 文字列・同じ base seed 規則だけでは paired evaluation にはならない。

## 層別結果

各 opponent は両 seat 合計 4 局である。以下は `wins-losses` で、draw/fault は全 cell で 0 だった。

| opponent | Rule v0 | Wave6 s0 | Wave6 s1 |
|---|---:|---:|---:|
| aman_crustleaware_fighting | 1-3 | 0-4 | 3-1 |
| aristophanivan_multiply | 0-4 | 2-2 | 2-2 |
| aristophanivan_probabilistic | 0-4 | 2-2 | 3-1 |
| biohack44_crustlecounter2 | 0-4 | 2-2 | 4-0 |
| dashimaki360_crustlecounter | 0-4 | 3-1 | 3-1 |
| ferozahmedds_solution | 3-1 | 2-2 | 4-0 |
| harukiharada_crustle | 0-4 | 4-0 | 3-1 |
| itsuki9180_lucario_jp | 2-2 | 1-3 | 3-1 |
| kiyotah_abomasnow | 1-3 | 3-1 | 4-0 |
| kiyotah_dragapult | 0-4 | 2-2 | 1-3 |
| kiyotah_iono | 1-3 | 1-3 | 2-2 |
| kojimar_lucario | 0-4 | 0-4 | 2-2 |
| kokinnwakashuu_lucario_search | 0-4 | 1-3 | 2-2 |
| lucifer19_battlecore | 0-4 | 2-2 | 1-3 |
| masamikobayashi_garchomp | 0-4 | 2-2 | 0-4 |
| medal_0001_77a53ffc | 1-3 | 3-1 | 4-0 |
| naoto714_kangaskhan | 1-3 | 3-1 | 3-1 |
| naoto714_slowking | 1-3 | 4-0 | 3-1 |
| naoto714_ursaluna | 0-4 | 4-0 | 3-1 |
| official_random | 2-2 | 4-0 | 4-0 |
| pilkwang_lucario_alakazam | 0-4 | 1-3 | 0-4 |
| plamen06_steel | 0-4 | 0-4 | 0-4 |
| prvsiyan_grimmsnarl | 0-4 | 2-2 | 1-3 |
| rauffauzanrambe_advanced | 0-4 | 1-3 | 0-4 |

seat 別の合計は Rule v0 が seat0 `7/48`、seat1 `6/48`、Wave6 seed0 が seat0 `24/48`、seat1 `25/48`、Wave6 seed1 が seat0 `28/48`、seat1 `27/48` だった。seat の片側だけで改善が作られている状態ではないが、cell が小さいため seat non-degradation の promotion gate としては未達である。

## 解釈と次段条件

この実測から確定できることは次の範囲に限る。

1. 現行 parallel evaluator は 96 局規模を fault なく完走できる。
2. root Rule v0 は同一 broad pool で Wave6 V4 より明確に弱い。したがって Rule v0 を性能 teacher として fine-tune の主線にする判断は採らない。
3. Wave6 seed0/seed1 は同一 pool で正の性能を持つが、seed 間の差と 4 局/cell の標本誤差が残る。
4. current 24-pair pool は diagnostic arena であって、qualified strong-pair Census の代替ではない。pool 内には training-local 許可のない local-eval-only pair、generic policy、deck/policy duplicate が含まれる。

Strong Asset Fine-Tuning を開始する前に、次を満たす必要がある。

- `deck + agent` を一つの identity として qualified pair を凍結する。
- training-local permission と evaluation-only を分離し、既存 hard teacher collection の source/policy/deck SHA を再検証する。
- BestKnown 用には同一 common arena の 96 smoke 後、少なくとも 384 局、可能なら 768/1536 局へ拡張する。
- external hard-only teacher は behavior probability が無いため、AWR の behavior ratio や V4 logits DAggerへ直結しない。まず hard-label / outcome-weighted BC の bounded pilot として扱う。
- 学習候補を broad arena へ戻す場合は、同一 seed 対応 checkpoint、同一 deck、同一 pool、fault 0、seat/opponent 層別、非 paired 評価で比較する。
- Wave6 より改善しても、strong qualified pair / BestKnown を超えたと証明できるまで `LONGRUN_STARTED`、Champion変更、Kaggle 提出は行わない。

## 未完了 artifact の扱い

先行して作成した 384 局 manifest は、実対戦が 0 局または完走前に中断されたため、今回の勝率表へ含めていない。これらは削除せず、未完了 run として保持する。今回採用した証拠は、同一 evaluator identity で `DONE=96/96` と summary/ledger の整合が確認できる 3 run だけである。

本書は broad arena の diagnostic report であり、BestKnown の昇格記録、学習許可、package promotion、外部提出許可ではない。

## Strong Asset pair の追加共通arena

同じ24相手・両seat・各seat 2局の recipe を、登録済み pair を subject として実行した。これらは既存の `measure_opponent_strength.py` による serial subject evaluation であり、parallel evaluator の root/Wave6 ledger と schema は異なる。従って、以下の score は同じ opponent ID 集合を使った cross-arm の診断値であり、engine RNG が共有されない点、subject 自身を pool から除外した pair では代替 opponent を1件補った点を明記する。

| rank（今回測定） | pair | archetype | games | W-D-L | score（W+0.5D） | 95% Wilson | seat0 / seat1 | faults | report SHA |
|---:|---|---|---:|---:|---:|---:|---:|---:|---|
| 1 | `lucifer19_battlecore` | Metal/Psychic | 96 | 74-0-22 | 77.08% | [67.73, 84.35]% | 72.92 / 81.25% | 0 | `f05acf78b920cfa11dfeb3f08a0ba0f2a14848427ab53338468894334edc5db9` |
| 2 | `tomatomato_archaludon` | Archaludon/Cinderace | 96 | 63-1-32 | 66.15% | [56.22, 74.83]% | 64.58 / 66.67% | 0 | `ee7bc5755a5eb154816810c0721f578793f98cb2ea91184bb227ab7f54cb8ab8` |
| 3 | `ozawa_crustle_v2` | Crustle | 96 | 58-0-38 | 60.42% | [50.42, 69.62]% | 70.83 / 50.00% | 0 | `f13ec270760dea0cebf5cb8fe3a82b1dbf7bc8aa5b82ee2b49724d55332f9001` |
| 4 | Wave6 seed1 | Archaludon V4 | 96 | 55-0-41 | 57.29% | — | 58.33 / 56.25% | 0 | `96c8e9e21faf668f33e18a2db6f2c7953598b251290d8f9075c70a8875e91125` |
| 5 | Wave6 seed0 | Archaludon V4 | 96 | 49-0-47 | 51.04% | — | 50.00 / 52.08% | 0 | `e582fe7bb06849c9ff1ddd5fe63da92faf73bdebf9525d1af6e2ef84774180f0` |
| 6 | `ozawa_starmie_v3` | Water Box/Starmie | 96 | 43-0-53 | 44.79% | [35.24, 54.75]% | 45.83 / 43.75% | 0 | `285abb6793af204ab99bd1fe369a318f569c0d25960f80de649920e12b96a108` |
| 7 | `ozawa_rocket_v2` | Rocket/Mewtwo | 96 | 41-0-55 | 42.71% | [33.28, 52.70]% | 50.00 / 35.42% | 0 | `0ee05c775d1eca6fc4145e2cc70a33f189ad2fea3d752e5b26e182435cd38c13` |
| 8 | Rule v0 root | current submission safety baseline | 96 | 13-0-83 | 13.54% | — | 14.58 / 12.50% | 0 | `a1792efb517f6ffe1037af69b40577d7a5459b81f04de7f2c379ca1adf3d5a27` |

追加 report artifact は次の通りである。

- `runs/meta-specialist-performance-sprint-v1/lucifer19-strong-arena-96.json`
- `runs/meta-specialist-performance-sprint-v1/tomatomato-strong-arena-96.json`
- `runs/meta-specialist-performance-sprint-v1/ozawa-crustle-v2-strong-arena-96.json`
- `runs/meta-specialist-performance-sprint-v1/ozawa-starmie-v3-strong-arena-96.json`
- `runs/meta-specialist-performance-sprint-v1/ozawa-rocket-v2-strong-arena-96.json`

### BestKnown の暫定更新

この common-pool recipe に限れば、今回実測の provisional GlobalBestKnown は `lucifer19_battlecore` pair である。これは **promotion-ready の確定ではない**。理由は、(1) 96局で各 cell 4局に留まる、(2) CABT engine seed setterがなく game-level pairing不可、(3) policy/deck は現在の Census で training-local 許可があるが、同一 pair の強さ・teacher collection・提出利用許可は別証拠、(4) subject evaluator が serial legacy report で parallel ledger と同一schemaではない、ためである。

従って次の主線は `lucifer19_battlecore` の pair identity（policy SHA `c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c`、raw deck SHA `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`）を凍結し、同じ pair から新規 teacher/on-policy snapshot を collection して、短期 outcome/quality-weighted BC を比較することに置く。`tomatomato_archaludon` は第二候補・Archaludon archetype teacher source、`ozawa_crustle_v2` は evaluation-only の Crustle reference として保持する。

R7 (`public_archaludon_cinderace_r7`) の既存 62/96 fixed-six は今回の common-pool rankingに含めない。現 pool row が `smoke_ok=false`、`local_eval_only` であり、明示許可・smoke remediation・同一 subject recipe が揃うまで BestKnown/teacherへ昇格させない。外部 LB 789.4 の `waterbox_search_v3` も、提出時予算と local bench 予算が異なり、common arena勝率ではないためランキングへ混ぜない。

## Strong Asset Fine-Tuning bounded pilot（Lucifer pair、2026-08-12）

暫定 GlobalBestKnown として測定された `lucifer19_battlecore` pair（policy SHA `c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c`、raw deck SHA `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`）から、同じ subject deck・新しい24相手・両seatの96局を追加収集した。collection は96/96、fault0、records 5,102、seat 48/48、outcome 72W/24L。sealed snapshot は train 3,601 / development 748 / test 753、test partitionは学習から除外した。実体は `runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-96-strong-20260812/` であり、teacher manifest SHAは `d25d1d4f0cdc51207e9269d510310981039f3ebefd570f3c33ccc1c1a7023d84`、snapshot index SHAは `ea5275370d17bcc520d31aec3302ea0be054520eb92811cd5af2cdac54005ba4`、snapshot shard SHAは `2372381b53b659cc8262f4d98152a240cdde85a20048a891133255da57904135`、dataset JSONL SHAは `dd4aafe98838da2d43e493e555cc56c5c5c244bb4f1687e60c815c0219fe11b9` である。

同 snapshot を対応 Wave6 checkpoint seed0/1から固定1 epoch、learning rate `1e-4`、TBPTT 8、burn-in 1、outcome weight（win=1, draw=2/3, loss=1/3）、66 optimizer updates/seedで学習した。GPU実行はサンドボックス外のCUDA可視環境で行った。両seedのvalidation NLLは改善したが、これは性能証拠ではない。

| arm | seed | initial validation NLL | final validation NLL | delta | updates | checkpoint file SHA | tensor SHA |
|---|---:|---:|---:|---:|---:|---|---|
| Lucifer-derived V4 BC | 0 | 0.542242 | 0.480128 | -0.062115 | 66 | `2fa6e26d60608f9ad30a8e4484e10a055cef1e79e8025ff3e567251d1090f465` | `4be1d7dd7cf2d6c5000d008b464a304a262d70cd1c49726b17e9c51094b5bcc7` |
| Lucifer-derived V4 BC | 1 | 0.569239 | 0.517547 | -0.051692 | 66 | `88405f18bb195263390b9088581d97fc7429432f8c7db42d5db0b0826af89977` | `71f44759997dd68bf29062901edaa2a40c41cd213abed8855b5c1eacdfe0a9cb` |

同じLucifer subject deckでの24相手・両seat・各2局（96局）比較は、CABT engine seed setterがないため game-level paired ではなく独立層化である。自己対戦を避けるため、poolの `lucifer19_battlecore` 枠は `tomatomato_archaludon` に置換し、4 armsで同じ24相手集合を使った。

| arm | seed | W-D-L | score | fault | summary SHA | ledger SHA |
|---|---:|---:|---:|---:|---|---|
| Wave6 baseline、Lucifer deck | 0 | 54-0-42 | 56.25% | 0 | `e23f1a9b3c65978623b9b00e687e7aa288a38e76877f1ad5abf82eaed261a49d` | `59b4e6a9da50e647dada5169427748579321b1622c281dee62966c8eff994443` |
| Lucifer-derived BC、Lucifer deck | 0 | 59-0-37 | 61.46% | 0 | `bcad00e7a8e017ed341a2cbbcbcecff714ec60b7f434d910c624a5788413af8d` | `c2bdf67289630940589050e61e086845ef06ec92ec6f33716abe3af56b5783e5` |
| Wave6 baseline、Lucifer deck | 1 | 51-0-45 | 53.13% | 0 | `d0a9b88008fc78c08256f9de5f0e80d110d21db4142eac019107136da35ad582` | `f65f7412dbb69fbd20eee7282a53612ecbf3bf0d55e1e01b7655027c97fd8009` |
| Lucifer-derived BC、Lucifer deck | 1 | 54-0-42 | 56.25% | 0 | `811efd521cfcb9d4b62acf8a1f49a005c68721fd9f27c074dd2bf24286f6fda7` | `4d7455de6f4047a35b0446e320cbf21150f361e225bf45ddfa8d1c0196e096c6` |

候補は seed0 で Wave6 より +5勝（+5.21pt）、seed1 で +3勝（+3.13pt）となり、2 seedとも同方向だった。合算では candidate 113/192（58.85%）対 Wave6 105/192（54.69%）で +8勝、+4.17pt である。ただし、異なる engine RNG、各cell 4局、対戦相手ごとの大きなばらつきがあるため「再現可能な改善」や promotion の確定とは扱わない。teacher pair 自体の74/96（77.08%）との差は依然として大きく、hard external policyからV4への移植損失も残る。

このbounded pilotは `LONGRUN_NOT_STARTED`、Champion変更なし、Kaggle提出なしである。次は同じdeck/pool/seed層化 recipe を games-per-seat 8（192局/seed、384局/arm）へ拡大し、candidateと対応Wave6の差が維持されるかを確認する。384局で seed反転、seat悪化、fault、強い相手への崩壊が出た場合は、このhard-label/outcome-weighted BC系列を停止する。長時間学習へ進む条件は、384局確認、BestKnownとの差の再評価、提出package閉包、Promotion Gateをすべて満たすことであり、今回の96局結果だけでは満たさない。

## Strong Asset Fine-Tuning 384局確認（分割再実行、2026-08-12）

前節の96局結果を、同じLucifer subject deck、同じ24 opponent ID、両seat、同じV4 evaluator identityで各arm 384局へ拡大した。384局を一度に投入した初回試行は、親watchdogが未開始のqueued futureまで投入時刻からtimeout判定する評価器運用上の問題で、candidate seed0が128局完了後に256局を`parent_timeout`として記録した。この出力は性能証拠へ含めない。4並列ProcessPool試行もspawn競合で未完了のまま停止した。以後、`games-per-seat=2`の96局ブロックを4本へ分割し、1 armずつ順次実行した。各ブロックは`DONE=96/96`、`fault=0`で完了した。

評価runnerにはqueued gameを誤って親timeoutにしないための研究用CLI引数`--timeout-seconds`を追加した。変更対象は`run_performance_first_arena_v1.py`のみで、production evaluator、`main.py`、actor pool、CABT native binaryは変更していない。最終的なparallel evaluator identityは従来と同じ`cb15090f41dcf54072c717621d65935a2747e0b529db38aa82fdca069b4081bc`であり、CABT engine seed setterは引き続き`false`である。

### 384局 aggregate

| arm | deck | W-D-L | faults | score | seat 0 | seat 1 | 判定 |
|---|---|---:|---:|---:|---:|---:|---|
| Lucifer-derived BC seed0 | `opponents/lucifer19_battlecore/deck.csv` | 211-1-172 / 384 | 0 | 55.08% | 111/192 | 100/192 | Wave6 s0を下回り不合格 |
| Wave6 baseline seed0 | 同上 | 228-0-156 / 384 | 0 | 59.38% | 121/192 | 107/192 | control |
| Lucifer-derived BC seed1 | 同上 | 229-0-155 / 384 | 0 | 59.64% | 117/192 | 112/192 | Wave6 s1を下回り不合格 |
| Wave6 baseline seed1 | 同上 | 237-0-147 / 384 | 0 | 61.72% | 113/192 | 124/192 | control |

候補と対応baselineの差は、seed0が`-17勝 / -4.30pt`（candidate 55.08% vs Wave6 59.38%）、seed1が`-8勝 / -2.08pt`（59.64% vs Wave6 61.72%）だった。96局時点ではseed0 `+5勝`、seed1 `+3勝`だったが、384局へ増やすと両seedとも逆方向になった。従って、96局の正方向シグナルは評価noiseまたは局所的な相手・乱数相互作用で説明可能であり、再現可能なStrong Asset fine-tune改善とは認定しない。

### 96局 block別 artifact

全blockは同じ24 opponent、両seat、各seat 2局で、`engine_seed_supported=false`の独立層化評価である。各summary/ledger SHAは、未完了の一括384 runとは別の採用artifactである。

| arm / block | W-D-L | summary SHA | ledger SHA |
|---|---:|---|---|
| BC s0 b0 | 53-1-42 | `1a27e8b3a8688c787184aee9c2027c56ec76abad0bf6266e6a754c78a9647226` | `2e27bed14da930da27a9ec7af6b05fc3343c013522b7b85ce012b9a63011fbf1` |
| BC s0 b1 | 52-0-44 | `b337afc5e99064b0da6186af3ed077897e0507176d667afde31c3642100a1fd5` | `9d87e83807e3bed719b3d0f3f6f53a307171f0d4373600ea51d427db1dfce839` |
| BC s0 b2 | 53-0-43 | `c002a03399fd717884a0f87320c557d41f2ff766282afe35bea1f2d29e9ec369` | `36bfa1cd2be5f345f20005f3b63093a2baa9e1af768a44a8bd377b07e03162bc` |
| BC s0 b3 | 53-0-43 | `578077f642f68f4c43e5a44ffeb8536f155852fcaf60432dec0c50ff0ea8c7ce` | `6e8c3c7b25b6bca39cb804e1087df4cc43c9041117ba14239aae5018e72c0ac9` |
| Wave6 s0 b0 | 59-0-37 | `176dc2cd1cc9ec4a7cab965b97fb56883500018e5c3f48ee95db1cab60d13584` | `998f4c32f29754309633411e63dde86459b76db984a9961c271481d9c9e11187` |
| Wave6 s0 b1 | 65-0-31 | `ad898032b1469bf1963529034f0898cb649ece9170a7b8a9a6f010749ebadaff` | `0f9b54385214d222321ea4059bbdb892497734ee45cf055aeb4761005fa0720b` |
| Wave6 s0 b2 | 55-0-41 | `203a7087d0cf6e0d2e69c77ce0027b0fecb38ac1649cd9556a769724dd5a0179` | `819bc5ead671f0f995c576168059508a85f78148b52b93846a2ce9f9e20322d1` |
| Wave6 s0 b3 | 49-0-47 | `b3c07073a30975f7d07ecdaa67846ef6d6cb7ced7e6bf06f7db81c7ed19a0ecc` | `f96413b3e49e730d1e18833670b65cf71fe779dab128eae747051fcf25f09326` |
| BC s1 b0 | 57-0-39 | `996a6f5d3f0235d21509c4ab6bb6f75286a7acb17cb34caa86401c53f22c3577` | `d424d4c85eab8d83453e8c9e4333ea41ba4b4f0387b7ac66a265cb4e4ca44466` |
| BC s1 b1 | 54-0-42 | `47e7cd0b76ef0550a6d0eb3cb18c07badfbb4fa9fe6254ae9fd376f9e386825e` | `ccdf653a89ba4f602e6c6524bf5400fa9d0a520dec7c2e2fb6122cfbc4bd14ce` |
| BC s1 b2 | 60-0-36 | `2cc7f74e67e31d013a87b9329075ae40691b808792eed6d4058422c6a16dd0cd` | `d7317ccc62a6ea22fa06089365ed3815e78c06b95aadff842bd6558ed12bb164` |
| BC s1 b3 | 58-0-38 | `fde3a70f66e2153331991a38c17a57046c3620e01816b4070fcb9f19a952d1ac` | `e7ac6a52632edf94821da7490d69ea5da1d42c66ad6b307879739b2295769dd8` |
| Wave6 s1 b0 | 66-0-30 | `a895d0c5adb65d0bebf0629a21bb0f7b6e17bea86e8fc943738acd09283c7ce` | `9e821aaf733c78f9bdc193d3d3d5d1b33eea7624fb6124a6c54a71f42afdcb5e` |
| Wave6 s1 b1 | 58-0-38 | `ba8b4433dca9922f7da03f2e4f08c9661854fa06f5900fb462926ed8cfdc3d41` | `4123d027f174d059369a087708981b71d08c000b4bf614a3154c3e1370c7014f` |
| Wave6 s1 b2 | 57-0-39 | `1030f8794589067cbf0524125fb24777131e9a0e02a7e89e890bde947949d188` | `57e8feb3d4239bb422f282dfad5bc99d03aa586361306cabbc89af2a166bd374` |
| Wave6 s1 b3 | 56-0-40 | `d1e3f992b9384205ffc05c8dff45775cc5075149a08aa8b704f870b405af173c` | `d0f6ce925676998115e1da5eb245f26bddfb37002f60d8c1d1206b338f8e13ab` |

### 384局の層別所見

- BC seed0はseat0 `111/192`、seat1 `100/192`、Wave6 seed0はseat0 `121/192`、seat1 `107/192`。candidateは両seatでbaselineを下回った。
- BC seed1はseat0 `117/192`、seat1 `112/192`、Wave6 seed1はseat0 `113/192`、seat1 `124/192`。candidate seed1はseat0では+4勝だがseat1で-12勝となり、合計では-8勝。seat非悪化条件を満たさない。
- BC seed0の弱い相手は`pilkwang_lucario_alakazam` 2-14、`plamen06_steel` 2-14、`masamikobayashi_garchomp` 4-12、`tomatomato_archaludon` 4-12。Wave6 seed0でもこれらは弱いが、candidateの差はstrong-pair転送で全相手へ一般化していない。
- BC seed1は`tomatomato_archaludon` 2-14、`kiyotah_iono` 4-12、`masamikobayashi_garchomp` 6-10、`pilkwang_lucario_alakazam` 6-10、`plamen06_steel` 6-10。相手別の改善は局所的で、seat1の大幅悪化を相殺できない。
- 4ブロックすべてfault0であり、今回の不合格はruntime faultではなく勝率・seed/seat再現性の不合格である。

### 最終判定

この384局確認により、Lucifer strong-pairからの1 epoch outcome-weighted hard-label BCは、96局の一時的な正方向シグナルを維持できないことが確認された。したがって、同じsnapshot、fraction、threshold、epoch、action weightを細かく振る探索は打ち切る。`LONGRUN_NOT_STARTED`、Champion変更なし、BestKnown更新なし、Kaggle提出なしを維持する。次の性能作業へ進む場合は、同型BCの延長ではなく、qualified strong pairのsoft/action-probability資産、public-state value/advantage、または別の明示的な目的を一つだけ設計してから、事前登録した小規模対照へ進む。今回の結果を理由に現行Rule v0提出物を変更しない。
