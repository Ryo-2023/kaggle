# self-owned deck-adaptive source v2 / P1 CEM evidence（2026-08-16）

## 結論

公式カード CSV と repo 内の self-owned deck spec から、deck と policy を同時に生成する新しい source generation 経路を実装し、6 source の staged batch、promote、独立 runtime smoke を完了した。runtime gate は `192/192 DONE`、`fault=0` だった。一方、同 pool の P1 固定 CEM は 2 世代を完走したが、独立 positive／seat-safe gateを通過する候補はなく、center は P1 のまま保持した。したがって判定は次のとおりである。

`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`

P1 policy、common/public root deck、BestKnown、Champion、production、submission、commit、push は変更していない。今回の source pool は CEM に使用済みであり、同じ pool／seed／候補の blind retry は行わない。

## provenance と「ono-」の扱い

今回の `self-owned` は、生成器が外部 kernel の policy/deck を親としてコピーしたという意味ではない。`scripts/generate_self_owned_cg_deck_adaptive_meta_v1.py` は、公式 `data/raw/EN_Card_Data.csv` と指定された self-owned deck spec から deck を生成し、`cg_deck_adaptive_renderer_v1.py` が公開状態だけを読む generic policy をレンダーする。既存 runtime の `cg/` は実行依存として verified package からコピーするが、policy source は過去 policy を import しない。plan の `public_scan_roots` は既存 canonical deck hash の衝突除外に使うだけで、公開 policy/deck を親にするための入力ではない。

`ono-` は外部作者名ではない。local Git identity `bfe-lab-ono`、branch `agents/ono-cg-lethal-v1`、および commit `1965b42b028f10960d08ccb4980be5b76946f98b` を repo 内で短く参照するための識別子である。なお現行 BestKnown の root deck SHA `2a541d7b...` は common/public root deck であり、BestKnown 全体を self-owned deck＋policy と呼んではならない。

## 実装

- renderer: `src/mage_ptcg/meta_specialist/cg_deck_adaptive_renderer_v1.py`
- generator: `scripts/generate_self_owned_cg_deck_adaptive_meta_v1.py`
- plan: `configs/meta_specialist/self_owned_cg_deck_adaptive_family_v2.json`
- design: `docs/superpowers/specs/2026-08-16-cg-deck-adaptive-renderer-design.md`
- plan: `docs/superpowers/plans/2026-08-16-cg-deck-adaptive-renderer.md`

plan は `self_owned_cg_deck_adaptive_family_v2_20260816`、seed namespace は `self-owned-cg-deck-adaptive-v2-fresh-20260816`。fire／dark／lightning／fighting／water／psychic の6 deck recipeと、各 recipeへ束ねた bounded policy variantを生成した。plan SHA は `ac65f796f66bf34284a439d74eed9fa187922284e2dcee2e81e6af9c6d263a5c` である。

## v1 hard-negative

v1 は grass variant を含む6 sourceを生成したが、staged sourceをそのまま smoke runnerへ渡すと `cg/` runtime欠落による `buffer full` となる入力境界を発見した。candidate package rootへ再束縛した後も、grass sourceで別 seed の `STEP_LIMIT` fault が再現したため、v1 pool は promoteしなかった。v1 grass faultは source generation全体の成功根拠に算入していない。grass診断は `runs/cg-self-owned-deck-adaptive-grass-diagnostic-v1-20260816/` と v1 runtime-smoke artifact に保存した。

## v2 generation / promotion / smoke

生成 root は `runs/cg-self-owned-deck-adaptive-v2-20260816/`、promoted root は同 root の `promoted/`。batch manifest SHA は `a7107ea1f952ee866bafd5e90ce6fa5f431d280861011ef091ec2e3aac70b1dd`、promoted `meta_manifest.json` SHA は `dc38ce1266ceed5148eba6884539ab2c6dba336ccbc8612b58d1e80d31f81465`、pool manifest SHA は `96525ece441063ad37c3236f275ea2d66c00949dd977bf3ad33f6f2008f7e568` である。fresh meta は `4a5256a120c763acb8cbf172dc26a0f50803b4bab813fee7efa9d4a8acab8259`、historical split は `a8768957b28dc382cf00d63f68a843f6cb1fc2d53d2e8b5c0b371001ca2abd8e` である。

promoted poolの source smoke は worker 1、worker recycle 1、4 games/opponent/seatで実施した。

- requested / completed: `192 / 192`
- status: `DONE=192`
- faults / draws: `0 / 0`
- result: `10W-0D-182L`
- score rate: `5.2083%`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`

この score は新 source を subject policy として評価した値ではなく、source poolを opponent として P1 と対戦させた runtime smoke の集計である。性能改善の根拠として解釈しない。

## P1 fixed CEM

CEM root は `runs/cg-p1-cem-deck-adaptive-v2-20260816/`。P1 control policy SHA は `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA は `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`。campaign manifest SHA は `ecbbe84754fd330a9e5a628fbd6f00ee04b7b7a77f37aa37c8e05c962ddb4ad1`、evaluator SHA は `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08` である。

設定は campaign seed `2030862901`、population／elite `8／3`、2 generation、META_TRAIN_ALL、独立 re-evaluation 2 回、positive-delta gate、research-only。各 generation の screen は `108/108 DONE`・fault0だった。

- generation 0: initial screenは上位候補を得たが、independent train re-evaluationでpositive gateを満たさず、`incumbent-center`を保持。
- generation 1: screen 108局、独立 train 96局、DEV 64局をすべて fault0で完了。center自身は unpaired DEVで候補 `25/32`、control `24/32`（`+3.125pt`）だったが、candidateとcontrolは同じP1 policyであり、policy改善の証拠ではない。独立 train gateでは center を更新しなかった。
- generation 1 の DEV は `META_DEV` 2 source、candidate `25/32`、control `24/32`（参考値）で、再現性・seat safetyを示す採用証拠ではない。

generation results SHAは g00 `87c58155dacbab077aee01f3e988c11a700f305da29c347b53537624d3652d4b`、g01 `a3ec95a0229b53d4afee1c70aacd2d458c5af439d529a71b0816c06c6f9ac063`。最終 campaign manifest は `champion_changed=false`、`submission_sent=false`、`status=COMPLETE`、`research_only=true` である。

## 判定と次の扱い

この epoch は「self-owned deck＋policyを生成し、runtime-safeな未使用 meta batchとして CEMへ接続できる」ことを示した。しかし性能上の昇格候補は得られていない。したがって `cg_bestknown_loop_v1.py` の heavy policy→deck→policy loopには接続せず、P1／root deckを BestKnown として維持する。次の研究は、今回と相関が低い新しい policy lineageまたは deck recipeを新 seed namespaceで生成し、smokeと性能 holdoutを分離したうえで、`fault0 → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を再実行する。

