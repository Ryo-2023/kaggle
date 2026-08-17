---
title: Strong Asset BestKnown classification v2
date: 2026-08-13
status: research-only
promotion_authority: false
---

# 結論

top3 nativeの pooled1536、plamen deck mutationの pooled1472、deck-fixed policy race、permission/package監査を一つの分類へ統合した。結論は、**局所評価の暫定首位はplamen native policyを固定した2-swap deck候補、現行training起点のBestKnownはtomato、現行提出可能なBestKnownはRule v0 + root deck、GlobalBestKnownは未確定**である。

機械可読な一次artifactは [`autonomous-bestknown-classification-v2-20260813.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/evidence/autonomous-bestknown-classification-v2-20260813.json)、SHA-256は `e9be2b72f9296bced60f46c3ff676bdf0b957dfaa1e0cc4ad4093c27f4ae0fd3` である。本分類は権限、Champion、提出経路を変更しない。

## 5区分の一次判定

| 区分 | 現時点の判定 | pair / artifact | 性能・根拠 | 権限 |
|---|---|---|---|---|
| `EvaluationBestKnown` | **暫定: plamen 2-swap deck mutation candidate** | candidate `aab824462a561b8a459fc71e1a780dc46487f8ab9ed27514a2dfff17fb40b6d9`; policy `plamen06_steel` | 1,472局で `1101W/1D/370L/0F = 74.8302%`。23 opponent × 2 seat。親native `1072W/0D/400L = 72.8261%`より +2.0041pt。4 blockすべて候補優位 | training/promotion/submission/longrun 全て不可 |
| `TrainingEligibleBestKnown` | **tomato primary、Lucifer control** | `tomatomato_archaludon` | native common arena pooled1536 `1107/1536 = 72.0703%`。現行permission-filtered META_TRAINで使用可能、現行sourceと一致するsealed snapshot 96局/5,146 records。Luciferもsealed controlだがnative pooled1536は71.8099% | teacher-derived researchのみ。`behavior_allowed=false`、元agent/package不可 |
| `SubmissionEligibleBestKnown` | **Rule v0 + root deck** | `runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck/submission.tar.gz` | archive SHA `da4bbe...`; clean-room 2局 `DONE=2`, fault0, illegal0。pool native assetは全てlocal_eval_only | 現行production package anchor |
| `BestKnownArchaludon` | **暫定: plamen mutation candidate** | 上記candidate | Archaludon系のbounded mutation評価で4独立blockの全てが親nativeを上回った。policy raceではnative/defaultと`USE_SEARCH=0`がともに271/368で、改善信号はdeck側 | promotion/submission不可 |
| `GlobalBestKnown` | **未確定** | current common-arena native leaderはtomato | slow5未完了、R7 smoke=false、mutationはnative top3と23/24 reference protocolが非同一、pool全件local_eval_only | 未確定 |

## 1. native top3 pooled1536

同一native rankingの4独立384局block（各asset 1,536局、両seat、fault0）では次の通りだった。

| rank | pair | policy SHA prefix | raw deck SHA prefix | W/D/L/F | score |
|---:|---|---|---|---:|---:|
| 1 | `tomatomato_archaludon` | `8908af5caad2` | `42165967b565` | 1107/0/429/0 | 72.0703% |
| 2 | `lucifer19_battlecore` | `c4acf505565a` | `fbe6ab599922` | 1103/0/433/0 | 71.8099% |
| 3 | `plamen06_steel` | `8a40be682561` | `fbe6ab599922` | 1102/0/434/0 | 71.7448% |

artifact SHAは、block1 `58df60b5c3ace39fb827ede3adf229c2d3d626e14b9dd685dda0d18506f5690b`、block2 `776f499598d771af10bfcdec0b10e8578aa347d114b122099725c5ce38dc163e`、block3 `e8ea484359d9085cdd2003c2877672f5245f9bb0fc8b1945148f141ab031acc7`、block4 `27d665871f2bad82dc9877a9dbd5fea51767caf9c5b28ad9b4804138fec01cc5` である。tomato首位は点推定であり、block首位はtomato/Lucifer間で変動した。

## 2. plamen deck mutationの評価

候補manifest [`candidates.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json) のSHAは `67f63c1caadf1b478cd3865dda8193f778e4c1fc4e5d0903776b23a0e982690b`。screen首位の候補は次の2-swapである。

- base policy: `plamen06_steel`, policy SHA `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3`
- parent raw deck SHA: `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`
- added cards: `1115`, `345`
- removed cards: `57`, `1185`
- candidate raw deck SHA: `9f413dd4423c2a90f40fa25753f01a610607fa1e0be8c54a9aee50b1285639e7`
- candidate multiset SHA: `a9b45c1d90672bf46ad67bc61e4f8a7382a44e5745d27f1b823495655909f227`
- deck size: 60

確認4 blockの結果は次の通りである。

| block | candidate | parent native | delta |
|---:|---:|---:|---:|
| 1 | 269/368 = 73.0978% | 255/368 = 69.2935% | +3.8043pt |
| 2 | 271/368 = 73.6413% | 270/368 = 73.3696% | +0.2717pt |
| 3 | 278/368 = 75.5435% | 270/368 = 73.3696% | +2.1739pt |
| 4 | 283W/1D/84L/368 = 77.0380% | 277/368 = 75.2717% | +1.7663pt |
| pooled | 1101W/1D/370L/1472 = 74.8302% | 1072/1472 = 72.8261% | +2.0041pt |

全block `DONE`、fault0。これはbounded positive confirmationであり、native BestKnown超過の有力候補だが、top3 nativeの24-reference protocolと完全同一ではないため、直ちにGlobalBestKnownへ昇格させない。

## 3. policy raceの解釈

deckを候補へ固定して、native/defaultと`USE_SEARCH=0`を各368局で比較した。race summary SHAは `e941429da0252c9dd79f95ba294c7ba68d3eb3e8e9acbe12c71a3a1426a93f65`、両armとも `271W/0D/97L/0F = 73.6413%`だった。

従って、今回のpositive signalはpolicy knobの効果ではなく、**deck mutationに帰属する候補信号**として扱う。`USE_SEARCH=0`を独立policy BestKnownとして採用しない。

## 4. TrainingEligibleBestKnownの根拠

現行immutable meta manifest SHAは `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae`、schedule SHAは `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a`。poolのusage boundaryは102/102件が`local_eval_only`である。

この境界の中で、tomatoは現行META_TRAIN permission-filtered scheduleに入り、現行policy SHAと一致する96局sealed snapshot（manifest SHA `b5a5bd30d0e0807c90ea65307e9665c01921842bfedc9abd4557ea02775b53ff`）を持つ。Luciferにも96局sealed control（SHA `d25d1d4f0cdc51207e9269d510310981039f3ebefd570f3c33ccc1c1a7023d84`）がある。一方plamenは旧decision上のderivation-qualified候補だが、現行manifestでは`training_allowed=false`で、現行sealed teacher snapshotもない。

この区分は「元external `main.py`を学習・提出へコピーできる」という意味ではない。現在の`behavior_allowed`はfalseであり、AWR/on-policy collectionを開始するには明示的なbehavior permissionが別途必要である。

## 5. SubmissionEligibleBestKnownの根拠

現在のpackage builderが接続しているのはroot `main.py` + root `deck.csv` + `agents/`のRule v0 routeだけである。pool native 102件とmutation candidateは全て`local_eval_only`で、as-is再配布・提出禁止である。したがって、評価性能の暫定首位と提出可能性を混同せず、現時点の提出anchorはRule v0 + root deckのままとする。

archive SHAは `da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a`。clean-room smokeは2局、両局`DONE`、fault0、illegal action0だった。これはnative assetのcommon-arena勝率ではなく、package closureの証拠である。

## 6. GlobalBestKnownを未確定とする理由

- slow5（`kinoshita_pimc_search`, `ozawa_metal_psychic_search`, `water_box_search`, `waterbox_search_v3`, `tientrum_alakazam_search`）は通常rankingで完走しておらず、性能順位がない。
- R7は96局診断を持つが、`smoke_ok=false`かつ`local_eval_only`で、top3の1,536局と局数・protocolが揃わない。
- mutation candidateは23 non-self opponent protocol、native top3 pooled1536は24-reference protocolであり、74.8302%を72.0703%へ単純比較してはならない。
- pool全体のlocal evaluation権限は、training、promotion、submission権限を意味しない。

従って、次の昇格条件は「mutation candidate、parent native、tomato/Lucifer controlsを同一sealed common protocolへ載せ、96→384→768→1536を再実行し、未測定資産を完了または明示quarantineしたうえで、package closureとpermissionを別ゲートで確認すること」である。

## 参照artifact

- top3 pooled evidence: [`strong-asset-top3-pooled1536-20260812.md`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/evidence/strong-asset-top3-pooled1536-20260812.md), SHA `e3299aac3a666cca3d19ab80a8feb0d7dddc861be155c2479345933eb22df863`
- mutation confirmation: [`autonomous-deck-mutation-plamen-top-confirm-20260813.md`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/evidence/autonomous-deck-mutation-plamen-top-confirm-20260813.md), SHA `c4c925ebb0b3e70a071f05f39c20300b6c70213067dba54b48dd76aaf6d78963`
- policy race: [`autonomous-deck-mutation-plamen-policy-race-20260813.md`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/evidence/autonomous-deck-mutation-plamen-policy-race-20260813.md), SHA `656799dded9250a207527ae3260595c66cdc48bbb9169ec12abd2d55211ef01c`
- eligibility/package boundary: [`strong-asset-eligibility-audit-20260812.md`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/evidence/strong-asset-eligibility-audit-20260812.md), SHA `a70afb3d995c7643e27b26aa1a43a048cf27c6b25770453af71a1c2ae9a625a4`; [`performance-first-submission-bundle-20260812.md`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/evidence/performance-first-submission-bundle-20260812.md), SHA `0937538aaa8e940cdb86a3f5597588ec3a2bf15d9e5db9c5406535f33ff8a330`

