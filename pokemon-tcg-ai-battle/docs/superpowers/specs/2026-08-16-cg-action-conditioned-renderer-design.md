# CG action-conditioned renderer 設計（2026-08-16）

## 目的

v19でscreen改善がopponent-seat-safeへ転移しなかったため、P1の単純なparameter overlayやindependent rootの再派生とは異なる、公開状態だけを使う新しいpolicy lineageを作る。候補は研究専用packageとして生成し、実CABTの独立seed評価でBestKnown更新可否を判定する。

## 採用方式

P1の不変sourceをparentとして、`turn`、両seatの公開prize count、activeの公開HP／damage、公開energy count、合法optionの`OptionType`／`SelectContext`をbucket化する。`action family × state bucket`の交互作用をscore overlayとして加え、P1のoption legality、selection cardinality、fallback、`cg/` runtime、deck bindingは変更しない。

探索面は次の12係数に限定する。

- attack lethal時のbehind／ahead係数
- attack non-lethal時のbehind penalty／ahead bonus
- early／late attack係数
- damaged activeのretreat係数
- behind時のretreat係数
- powered benchへのretreat係数
- over-reserved attach penalty
- early／late evolve係数
- damaged activeへのSwitch係数

`current.players[*].prize`は長さだけを読み、相手のprize identity、hand、deck、discard、search結果、teacher label、native actionは読まない。例外時はbase P1 scoreへ戻し、agentの合法手契約を壊さない。

## 代替案と不採用理由

- action type単独の加算は既存public-state mixとの差が小さく、state条件を持たないため不採用。
- 既存public outcome tableを直接適用する方式は、過去診断でmixed-sign supportが不足し、未使用metaと性能探索の境界も弱いため不採用。

## 受入条件

- config／candidate／package manifestがcontent-addressedで、policy／canonical deck hash衝突をfail-closedできる。
- `parent_deck=null`、`public_parent_read=false`、authority全false、P1 deck fallback bindingを維持する。
- rendered sourceはcompile可能で、`agent`が末尾にあり、hidden opponent fieldsを参照しない。
- 新sourceはpromotion前に各source×seat 4局以上のruntime smokeを行い、全行`DONE`・fault0である。
- hash collision 0、未使用`META_DEV`／`META_FINAL`をCABT screenで読まない。
- smokeまたはCEMが失敗してもP1／root deck／BestKnown／Champion／production／submissionは不変。

## 次段階

source smoke通過後、action-conditioned configを扱うCEM bridgeを追加し、`META_TRAIN` screen → 独立re-evaluation → seat/opponent-seat gate → DEV → FINALの順に進める。positiveでもlower-tailまたはseat gate不成立ならcenterを保持する。
