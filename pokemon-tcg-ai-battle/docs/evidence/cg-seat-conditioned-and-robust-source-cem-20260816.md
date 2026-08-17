---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-16
---

# seat-conditioned self-owned source と robust-source pool の CEM

## 結論

新しい source 生成方法として、P1 policy を公開 `yourIndex` と action family の組合せで調整する seat-conditioned renderer を実装した。公式カードCSVと repo 内の self-owned deck spec から6 sourceを生成し、bounded runtime smoke 192/192 `DONE`・fault 0で promotion した。しかし固定 self-owned deck の seat-conditioned CEM は screen 上位が独立反復で再現せず、BestKnown は変更しなかった。

同じ pool の blind retryはせず、未 downstream 使用の robust-source epoch 9/11/12/13 から4 sourceを新規ポートフォリオとして封印した。P1固定 CEM は全 row fault 0で完走したが、screen 候補は独立 lower-tail／seat／opponent×seat gateを満たさず、P1 centerを保持した。P1、root deck、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py` の昇格状態は不変である。

## 実装と provenance

- renderer: `src/mage_ptcg/meta_specialist/cg_p1_seat_conditioned_renderer_v1.py`
- CEM core: `src/mage_ptcg/meta_specialist/cg_seat_conditioned_cem_v1.py`
- source generator: `scripts/generate_self_owned_cg_seat_conditioned_meta_v1.py`
- CEM runner: `scripts/run_self_owned_cg_seat_conditioned_cem_v1.py`
- plan: `configs/meta_specialist/self_owned_cg_seat_conditioned_family_v1.json`（SHA `58a8728ee20e46d8f3ffd1f473b194b088f6d945b94ec8bd86ac204d98f54f9d`）
- immutable P1 parent policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- source epoch manifest SHA: `d431da2f74f38250734800a7f14bec53421792d7ea26753db3470a55d94dd5f7`
- staged batch SHA: `d165d3a492b3062a14eb062af51f81c95dce67e8b8fa70ae372b8709b65d7bfa`
- promoted pool SHA: `065e1e64d4551305b1ec4ce472f2248a51fdb8c571d3ad86a5453252f6c9d5df`
- fresh meta SHA: `4cd12f028d10bc99d4ed8a394bcb074d5d45b30194f17683ebc7ddba6a76e808`
- split SHA: `6dced5d2b9178fd8b6bcb6e27fe2adf63236f2a6112396bc6f0f4e7d71ce1b37`

source policy は P1 を親にするが、deck は公式 CSV＋self-owned role spec から生成した。各 package は `parent_deck=null`、`public_parent_read=false`、authority 全 false、hidden opponent zone 未使用である。

## seat-conditioned source smoke

最初に既定の native 24 opponentへ展開すると1,152局になったため、実験の性能証拠に算入せず停止した。固定4 opponent（`official_random`、`itsuki9180_lucario_jp`、`kiyotah_abomasnow`、`plamen06_steel`）へ source ごとに両 seat・4 repetitionを割り当てた bounded smoke は192/192 `DONE`・fault 0・29W-163Lである。これは runtime/legal gate であり、sourceの性能採用指標ではない。summary SHAは `f045748a2f2ac38904067de97157da7c9d9147f4073670437fa860a9dcfcf0c6`。

## seat-conditioned CEM

`runs/cg-seat-conditioned-cem-v1-20260816/` は population/elite `8/2`、META_TRAIN 4 source、screen 256局、独立再評価2反復（各64局）、DEV/FINAL各16局を worker 1 で実行した。全 row は `DONE`・fault 0である。

- screen best: `cg-seat-conditioned-g00-c06-d11a954226cb`、P1 control差 `+12.5pt`。
- independent repeats: `−6.25pt / +15.625pt`。
- candidate seat gaps: `31.25% / 6.25%`。
- opponent×seat gapsは最大 `50%`。DEVは `+12.5pt`・seat gap `25%`、FINALは `+37.5pt`・seat gap `50%`。
- research gate: `false`、BestKnown update: `false`。

campaign summary SHAは `a5baf87ebb5b73a951afa2f19b58986c300ccc64dd5db6345334fa981500c4c4`。小標本の絶対勝率やDEV/FINALの正差分は、strict gate外なので昇格根拠にしない。pool、seed、candidateは性能使用済みとして blind retryしない。

## robust-source epoch 9/11/12/13 の新規 downstream pool

既存 robust-source CEM で別 seed により independent validation を通過した未 downstream 使用 candidate 4件を、別 root `runs/cg-robust-source-weekend-pool-v2-20260816/` に再封印した。

- `META_TRAIN`: `e11-c07`、`e12-c01`
- `META_DEV`: `e13-c06`
- `META_FINAL`: `e09-c02`
- pool SHA: `a9b9f724b32ffa4c2aa91d28abc60f6fe10c0b4861d18f760d623783c513bd0f`
- fresh meta SHA: `0afca0d2fea1fbaf6dc09f383ff095736aedb968f3d252ca20f661e3784ed592`
- meta manifest SHA: `e69bf30c96455e9f9eca5e0b721d6e23d15ef088cb25153e6ec37a12f4fca2d4`
- split SHA: `070d97736e9af155fdb8247583f49e4a01fb0a12bcb632bc4f5af1c2c29b4adc`
- P1 source smoke: 8/8 `DONE`・fault 0、summary SHA `c98cc0c2594fd158b5215e6495d3c00eb04135f15531895ceb0de658a40c9099`

この pool は `unused_before_downstream_policy_run=true` を維持して封印した。

## P1 fixed CEM on robust-source pool

`runs/cg-p1-cem-robust-source-v2-20260816/` は seed `2026089702`、population/elite `8/2`、META_TRAIN 2 source、screen 72局、independent re-evaluation 96局（2 repeat、各4 games/opponent/seat）を実行した。全 row は `DONE`・fault 0。

- screen candidate deltas: `−12.5pt`〜`−25.0pt`。
- initial top `c00` independent repeats: `+25.0pt / 0.0pt`、risk-aware min `0.0pt`。
- c00 seat gaps: `12.5% / 25.0%`、opponent×seat gaps `25%〜50%`。
- second elite c05 independent mean `−4.6875pt`、min `−21.875pt`。
- selection: `incumbent-center`×2、P1 center保持。
- DEV/FINAL: CEM中未読のまま保全。

campaign manifest SHAは `05c7b3c0bd86c2f2a3f96706ebe31d3948e83aa42180d67ed46c759ce9b1b6e1`、generation manifest SHAは `1771e672fc8b9bfedfefa62b04c4077710b91c6c4e8f9eb699e91f8ff4a49b00`、results SHAは `054919d5ccc75aa7983728462c2344e527cb39cbcc0accae4e29657fb6e1bcda`。

判定は `SOURCE_GENERATION_PASS / RUNTIME_SMOKE_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。次はこの pool の blind retryではなく、未使用 meta を保った別 policy/deck lineage を生成する。strict gateを通った候補だけが `cg_bestknown_loop_v1.py` の candidate runnerへ進む。
