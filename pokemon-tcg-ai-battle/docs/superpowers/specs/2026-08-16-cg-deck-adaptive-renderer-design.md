# CG deck-adaptive self-owned renderer — design

## 目的

P1/Lucario固定の係数違いではなく、公式カードCSVから生成した各deck自身の公開カード構成を基に、相関の低いself-owned meta sourceを生成する。sourceはlocal-eval-onlyの研究用であり、BestKnown・Champion・production・submissionを直接変更しない。

## 設計

`cg_deck_adaptive_renderer_v1.py` が、deckの60枚IDと有限の整数設定から独立した`main.py`をレンダーする。レンダー後のpolicyは、`cg.api`が公開する合法option、公開場、公開カードデータだけを使う。相手のhand/deck/prize内容、native action label、teacher label、search APIは読まない。

policyはカードIDをLucario固有の固定表へ写像せず、`CardData`の`cardType`、`basic/stage1/stage2`、`ex/megaEx`、HP、retreat cost、attack damageを使って次を決める。

- setup/search: deck内のbasic／進化系列の役割と不足状況を優先
- play/evolve/attach: 公開stage、HP、energy、カード種別に基づくbounded score
- attack: lethalを第一条件、次にdamage・energy効率・target HPを使用
- retreat/switch/discard: damage、energy reserve、benchの公開状態を使用
- malformed observation: deterministic legal fallbackへfail-closed

source packageは生成policy、生成deck、既存の公式`cg/` runtimeだけを含む。package manifestはpolicy SHA、deck file SHA、canonical deck SHA、renderer template SHAを束縛し、authorityは全falseとする。既存のbatch staging/promotion機構を再利用するが、source kindはdeck-adaptive専用名を付ける。

## 生成と検証

新しいseed namespaceで6〜8件を生成し、policy SHAとcanonical deck SHAの重複を拒否する。公開canonical deck hashとの衝突も拒否する。candidate package（`cg/`同梱）で4 games/seatのruntime smokeを行い、全source `DONE`・fault0でないbatchはpromoteしない。

promotion後、未使用`META_TRAIN/DEV/FINAL`を固定し、P1固定policyのraw same-deck controlに対するpaired screenへ渡す。独立再評価でpositive、fault0、candidate seat gap≤5%、opponent×seat-safeを満たさない限りBestKnown loopへ接続しない。

## kill条件

- sourceがP1 `main.py`や既存public policyを親として再利用している
- deck/policy identity衝突、`cg/`欠落、authority不一致、合法性失敗
- smoke fault、STEP_LIMIT、AGENT_INVALID、buffer full
- screenの符号反転、seat gap超過、独立positive不成立

これらは候補を破棄または未promoteとし、同じseed／recipeのblind retryを行わない。

