---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-16
---

# action-conditioned self-owned source v1/v2 と固定deck CEM

## 結論

公式カードCSVから生成した新しいmeta source経路と、P1の公開状態だけを使うaction-family × state-bucket rendererを実装した。v2のP1互換Lucario deck束はsource-opponent smoke、候補席runtime、TRAIN/DEV/FINAL screenをすべてfault 0で完走したが、候補の符号はsplit間で揺れ、seat-safeを満たすBestKnown更新は無かった。

同じv2 source poolを固定Lucario deckへ接続したaction-conditioned CEMも1世代完走し、候補c05は拡張DEVで`+18.75pt`、FINALで`−6.25pt`、candidate seat gapはDEV `0.625`だった。したがってこれは「CEM bridgeが実CABTへ接続できた」証拠であり、採用・promotion・BestKnown更新の証拠ではない。

## 生成物とprovenance

- renderer: `src/mage_ptcg/meta_specialist/cg_p1_action_conditioned_renderer_v1.py`
- CEM core: `src/mage_ptcg/meta_specialist/cg_action_conditioned_cem_v1.py`
- source generator: `scripts/generate_self_owned_cg_action_conditioned_meta_v1.py`
- paired factorial screen: `scripts/run_self_owned_cg_action_conditioned_factorial_screen_v1.py`
- CEM bridge: `scripts/run_self_owned_cg_action_conditioned_cem_v1.py`
- plan SHA: `configs/meta_specialist/self_owned_cg_action_conditioned_family_v2_lucario.json` = `39a79dd45e4084c6439769cdfc972763c654685daf13266a7362b4682563fbf6`
- immutable P1 parent SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- v2 staged batch SHA: `37d6bb08fd2fdd74cc739564066ec7f92facf5be9a2f7e80be032d1690f65cd8`
- v2 promoted pool SHA: `1f925cdda22e20e84234f4186686535991f0cf69440cf0bb7f72cba37b2154a5`
- v2 fresh meta SHA: `f7512c5b2f46466418c4937401991e40eafd9331af00ea504eee16a267a8c378`
- v2 split SHA: `007121171a07829a94b0926b1a137992b254f411f2f38e66a26d92bf54d94d9b`
- source smoke summary SHA: `9559a3bd7e703356606cb2b91851340bb07020e160589d27a6d1443e99de5280`

v2はv5/v6/v7/v8/v9のself-owned Lucario deck recipe 6件へP1 policyを再束縛した。source smokeは48/48 `DONE`、fault 0、31W-17L。source poolはopponent用のため、promoted directoryにはnative `cg/`を同梱しない。

## runtime境界の診断

最初にpromoted/staged source directoryを候補席へ直接渡すと、native `buffer full. capacity:7`／`BrokenProcessPool`になった。これはsource ingestがopponent poolを軽量化するため`cg/`を省略する既知のruntime bundle欠落であり、policy/deckの性能失敗ではない。

候補席はgeneratorが出す `runs/cg-self-owned-action-conditioned-v2-lucario-20260816/packages/*`（`cg/`同梱）に限定し、screen runnerへ`--candidate-root`を追加した。runtime同梱のv2 aggressive candidateはP1 referenceに2/2 `DONE`・fault 0である。

さらにscreen/CEM controlを、P1 parameterized overlayではなく、raw P1 sourceを同じself-owned deckへ束縛したpackageへ修正した。旧parameterized controlを使った先行screen/CEMの差分はpair invariantを満たさないため、性能証拠から除外する。raw controlでゼロ係数候補を32局/arm再確認した差は`−3.125pt`で、wrapperが恒等であることと整合する。

## raw-control factorial screen

すべてworker 1、candidate/control同一seed strata、各候補のcandidate/control各32局相当で実行した。各summaryは以下。

| split | evaluator | 主なdelta（candidate−raw P1 control） |
|---|---:|---|
| META_TRAIN | 96局、fault 0 | aggressive `+25.0pt`、reserve `+37.5pt`、retreat `+12.5pt`。他は負値 |
| META_DEV | 96局、fault 0 | conservative `+25.0pt`、他は`0`または負値 |
| META_FINAL | 96局、fault 0 | conservative `+12.5pt`、setup `+12.5pt`、他は`0`または負値 |

一次summary SHAは順に `ccc6d2749828140d92e6c84e65a6301bfd66449c7ccca2e5bc88f6182f5fafe4`、`84f438e258a67a7de7eb0b9f31e4356398d388a5269a61cd6d985127fe30dfd4`、`e5ea2d23928698291b8d9829048dc5549522509c7f325103071734fac9901ec1`。同一候補が3 splitすべてpositiveではなく、seat gapも`0.00–0.75`で揺れた。

## action-conditioned CEM 1世代

`runs/cg-self-owned-action-conditioned-v2-lucario-20260816/cem-g00-v5-rawcontrol-w1/`で、v5 self-owned deckを固定し、population/elite `6/2`、META_TRAIN 4 source、各candidate/control 1局/seat、worker 1を実行した。train全96局は`DONE`・fault 0。

- c00 all-zero overlay: `0.0pt`
- best c05: train `+25.0pt`（8局/候補の小標本）
- c05 config SHA: `9eb638d43319ecc2920e5b03b8dff068bdf88a4ce529956ffb6e200e18e1bb27`（candidate id `cg-action-conditioned-g00-c05-9eb638d43319`）
- c05 policy SHA: `436a36db992c577c8157b32757c0b3fa4b81c66664f8d8d95cc85b5f708f05b5`
- fixed v5 deck SHA: `410f62a6c6ef80e2dabad17508e8cd7977a4f53e2d28eaaf40a0e4622a4bd4bf`

CEM内のDEV/FINAL（各8局）はともに`+25.0pt`だったが、候補seat gapは`0.50`。独立拡張（各16 candidate局＋16 control局）ではDEV `+18.75pt`、FINAL `−6.25pt`、DEV seat gap `0.625`となった。拡張summary SHAはDEV `f717c5fe5bf8efdf3c8c7ee1f059acab916a714cff8216cdacf026c4bf3c73bc`、FINAL `08441afa5bae51af003666d9f598f7300a1d11a6aa78b715d5a509df5714a575`。CEM campaign summary SHAは`251f82279f0584c569a9f12d10a12b4e072a05d4354c651e7f6b6dd336f4c3a9`。

判定は `SOURCE_GENERATION_PASS / RUNTIME_SMOKE_PASS / CEM_BRIDGE_CONNECTED / POSITIVE_NOT_REPRODUCED_WITH_SEAT_SAFE / BESTKNOWN_UNCHANGED`。v2 fresh sourceはこのCEMで使用済みとして扱い、同じpool/seedのblind retryはしない。

## 次の再開条件

1. v2と相関しない新しいseed namespace・deck recipe・source rendererでsource poolを生成する。
2. 候補席は常にruntime同梱 `packages/`、opponent席は軽量 `promoted/`に分離する。
3. raw P1 same-deck control、fault 0、独立DEV/FINAL、seat gapおよびopponent×seat gateを同時に満たす候補だけを`cg_bestknown_loop_v1.py`へ渡す。
4. 現BestKnown（self-authored P1 policy＋common/public root deck）、Champion、production、submissionは不変。
