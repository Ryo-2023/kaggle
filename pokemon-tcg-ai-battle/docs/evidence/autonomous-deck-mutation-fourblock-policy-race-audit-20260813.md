---
title: Autonomous deck mutation four-block and policy-race audit
date: 2026-08-13
status: research-only
---

# 結論

plamen06_steel の native policy を固定して評価した deck mutation 候補は、
8候補の一次 screen 後に選んだ 2-swap 候補を、4つの独立 368局 block すべてで
parent native deck より上回った。pooled 1,472局でも candidate は
`1101W/1D/370L = 74.8302%`、parent native は `1072W/0D/400L = 72.8261%`
で、点推定差は `+2.0041pt` だった。ただし、すべて local evaluation 用の
research artifact であり、promotion・training・submission authority は false の
ままである。

同じ candidate deck について実施した native vs `USE_SEARCH=0` policy race は、
両 arm とも `271W/97L = 73.6413%` となった。しかしこれは環境変数が効かなかった
ことを示さない。loader は module import 前に環境変数を設定し、plamen の import
時定数 `USE_SEARCH` は実際に `True` から `False` へ切り替わる。race は異なる seed
範囲を使った独立評価であり、engine の seed setter も false である。opp/seat/repetition
で対応付けても 368セル中126セルで最終 outcome が異なるため、同じ集計値は「policyが
同一」「search knobが無効」とは解釈せず、今回の独立標本で aggregate delta が0だった
とだけ解釈する。

## 1. 検証した一次 artifact と SHA

| artifact | SHA-256 |
|---|---|
| candidate manifest `runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json` | `67f63c1caadf1b478cd3865dda8193f778e4c1fc4e5d0903776b23a0e982690b` |
| screen summary `runs/final-sprint-autonomous/deck-mutation-plamen-v1/screen-736/candidate_summaries.json` | `8bb22edf47ddffc70f763aa1969124adb0c30204a389958118906dafbc3deb37` |
| 4-block block1 `runs/final-sprint-autonomous/deck-mutation-plamen-v1/top-confirm-736/arm_summaries.json` | `b347542057453a78c420fba0ed70a2b3c7d6ddbcd215248cc47093959a4ec7d1` |
| 4-block block2 `runs/final-sprint-autonomous/deck-mutation-plamen-v1/top-confirm-736-block2/arm_summaries.json` | `5aeac755dfa9d069dc44f6f0e6cf8dda833022bf35557063593bb9ad96420b43` |
| 4-block block3 `runs/final-sprint-autonomous/deck-mutation-plamen-v1/top-confirm-736-block3/arm_summaries.json` | `7f17835b96625a3d5dad66058aee90e28ed8d655680e4a7543bd76c42db21c1e` |
| 4-block block4 `runs/final-sprint-autonomous/deck-mutation-plamen-v1/top-confirm-736-block4/arm_summaries.json` | `708a8884548eac424fd68617f2d90b12b28f6e20b62f1b1c55a4dbae1cbd0f79` |
| policy race summary `runs/final-sprint-autonomous/deck-mutation-plamen-v1/policy-race-736/policy_race_summary.json` | `e941429da0252c9dd79f95ba294c7ba68d3eb3e8e9acbe12c71a3a1426a93f65` |
| policy race evaluator summary `runs/final-sprint-autonomous/deck-mutation-plamen-v1/policy-race-736/summary.json` | `4e602bb5ec3e4b14d99f3f30056a465a12033850c882c90766f94e55da657e76` |
| policy race manifest `runs/final-sprint-autonomous/deck-mutation-plamen-v1/policy-race-736/manifest.json` | `f7fe3bb46d310f9eaa2763553168a8481e5986c154b5a1be7208ce330dd331b9` |
| policy race ledger `runs/final-sprint-autonomous/deck-mutation-plamen-v1/policy-race-736/ledger.jsonl` | `9dd25ee1fbf13c3a314c83b51015c4e2f32ac6254d4806f8d20291bbfb725bf7` |
| policy race script `scripts/run_deck_mutation_policy_race_v1.py` | `71bd0c06608c756e2c911a7e6b3ff1e2388acd89c3339abbe9262ed447101926` |
| native policy source `opponents/plamen06_steel/main.py` | `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3` |
| parallel evaluator implementation | `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84` |

トップ候補は `aab824462a561b8a459fc71e1a780dc46487f8ab9ed27514a2dfff17fb40b6d9`。
manifest上の正確な deck identity は次の通り。

- swap count: `2`
- removed: card `57`, card `1185`
- added: card `1115`, card `345`
- candidate deck CSV SHA: `9f413dd4423c2a90f40fa25753f01a610607fa1e0be8c54a9aee50b1285639e7`
- candidate multiset SHA: `a9b45c1d90672bf46ad67bc61e4f8a7382a44e5745d27f1b823495655909f227`
- parent raw deck SHA: `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`
- parent multiset SHA: `d0b36a40a383c262723a60b14a0785f99074cd7816f187a39214f0ec12cc5ae0`

## 2. deck mutation screen と four-block confirmation

### 一次 screen

8候補を共通 broad pool（24 reference IDs から subject 自身を除いた23 opponent）へ
投入し、両 seat、各2 repetition、合計736局を実行した。

- requested/completed: `736/736`
- status: `DONE=736`
- faults: `0`
- screen首位: top candidate の `76/92 = 82.6087%`
- screen全体: `543W/193L = 73.7772%`

screenの点推定だけで確定せず、top candidate と parent native を同じ protocol の
独立 block で再評価した。

### 4つの独立 block

| block | candidate | parent native | delta | faults |
|---|---:|---:|---:|---:|
| block1 | `269/368 = 73.0978%` | `255/368 = 69.2935%` | `+3.8043pt` | 0 / 0 |
| block2 | `271/368 = 73.6413%` | `270/368 = 73.3696%` | `+0.2717pt` | 0 / 0 |
| block3 | `278/368 = 75.5435%` | `270/368 = 73.3696%` | `+2.1739pt` | 0 / 0 |
| block4 | `283W/1D/84L = 77.0380%` | `277/368 = 75.2717%` | `+1.7663pt` | 0 / 0 |
| pooled | `1101W/1D/370L = 74.8302%` | `1072W/0D/400L = 72.8261%` | `+2.0041pt` | 0 / 0 |

各 block は candidate/native とも368局（23 opponents × 2 seat × 8 repetition）で、
`engine_seed_supported=false` のため block間は independent stratified block である。
game-level paired comparison や、単一 seed の確定的勝率とは呼ばない。

## 3. policy race の構成と結果

raceは上記 top candidate deckを固定し、同じ plamen `main.py` を次の2 armでロードした。

| arm | module import環境 | seed範囲 | result |
|---|---|---:|---:|
| `native` | 追加envなし（`USE_SEARCH` default=`1`） | 11,200,000–11,200,367 | `271W/97L = 73.6413%` |
| `use-search-0` | `USE_SEARCH=0` | 11,300,000–11,300,367 | `271W/97L = 73.6413%` |

race全体は `736/736 DONE`、fault `0`、draw `0`。両armとも368局で、summaryの
aggregateは完全一致した。

### 環境変数が実際に効いたか

plamen sourceは import時に次を評価する。

```python
USE_SEARCH = ENABLE_SEARCH and _os.environ.get("USE_SEARCH", "1") == "1"
```

candidate loader (`scripts/run_native_policy_candidate_pilot_v1.py`) は、moduleを
`exec_module`する前に candidate env を設定し、source/config SHAを含む隔離module名で
cacheする。read-only import auditで、raceと同じ設定SHAを指定して確認した結果は次の通り。

| config | module `USE_SEARCH` | `_SEARCH_OK` | `CAND` | `BUDGET` |
|---|---:|---:|---:|---:|
| race native `4f7daeb1c690…` | `True` | `True` | 6 | 2.0 |
| race `USE_SEARCH=0` `cdbc1c8d40db…` | `False` | `True` | 6 | 2.0 |

従って `USE_SEARCH=0` は環境変数の受け渡し失敗ではなく、module import時の検索
gateを実際に無効化している。ただし `_SEARCH_OK=True` はAPIが利用可能という意味で
あり、個々のCABT turnで `_search_choose` が呼ばれた回数や、searchがheuristicと
異なる actionを返した回数は、今回のledgerへ記録されていない。

### 271勝同値の正しい解釈

同値は「searchを切っても同じ policy」「search knobに効果がない」という証明ではない。
理由は次の通り。

1. race builderは native と no-search に異なる seed namespaceを割り当てている。
   nativeは `11,200,000`台、no-searchは `11,300,000`台で、同一ゲームを再生していない。
2. `run_match`/evaluatorの `engine_seed_supported` は falseで、CABTへ seed setterを
   注入していない。external opponent factoryも受け取った seedをpolicyへ渡さない。
3. `opponent_id`, `seat`, `repetition` をキーに対応付けても、最終 outcomeは
   `126/368` セルで異なる。従って、同じ `271W/97L` は単なる同一trajectoryの
   再出力ではない。
4. 対応付けは分析上の bucket alignment に過ぎず、異なる seed と独立 engine state
   のため paired statistical testの根拠にはしない。

このraceから言えるのは、今回の独立368局標本では `USE_SEARCH=0` の aggregate
score delta が `0.0000pt` だったことだけである。action-level effectの有無を確定する
には、同一条件の実行で search telemetry（呼出し回数、候補数、search action採用率、
fallback理由）を別途保存し、seed制御可能な評価器または十分な独立 blockで再検証する
必要がある。現時点で追加CABTを起動する判断はしていない。

## 4. authority と次の判断

policy-race summary、deck confirmation summariesとも research-only であり、
promotion/training/submission authority はすべて false。deck candidateが4 blockで
parent nativeを上回ったことは `bounded_confirmation_positive` の証拠だが、これだけで
GlobalBestKnown昇格、提出、長時間学習を自動実行しない。

次の安全な判断は、top candidate deckを固定したまま、search budget/candidate/depthなど
別の policy knobを同一 successive-halving protocolで評価すること。ただし、各 armを
別の seed blockとして記録し、aggregate同値・小差を「同じ policy」の証拠にしない。

## 5. 再現コマンドと監査範囲

read-only環境監査（CABT再実行なし）:

```bash
PYTHONPATH=. python - <<'PY'
from pathlib import Path
from scripts.run_native_policy_candidate_pilot_v1 import _candidate_module_v1

p = Path("opponents/plamen06_steel/main.py").resolve()
for label, env, cfg in [
    ("race-native", {}, "4f7daeb1c6908371000a860628db91dfbea21d473779353ca8a11116ca561eb0"),
    ("race-search0", {"USE_SEARCH": "0"}, "cdbc1c8d40db0e026c7679626be74e0401cfb37a9b0ef45fb44f765915defa84"),
]:
    module, _ = _candidate_module_v1(p, env, cfg)
    print(label, module.USE_SEARCH, module._SEARCH_OK, module.CAND, module.BUDGET)
PY
```

この文書は上記一次 artifactのハッシュ、ledger、source、loaderを読み取って作成した
監査記録であり、ChatGPT context pack、production `main.py`、Champion、submission
packageを変更していない。
