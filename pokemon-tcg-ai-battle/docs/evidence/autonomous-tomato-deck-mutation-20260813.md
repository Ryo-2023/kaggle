# Autonomous Meta-Fine-Tuning: tomato native deck mutation

作成日: 2026-08-13 (JST)

## 結論

`tomatomato_archaludon` native pair を固定policyとして、1/2-card deck mutation
8候補を共通24-opponent arenaで評価した。96局screenで唯一正だった candidate 7
（`Jumbo Ice Cream` 1枚と `Full Metal Lab` 1枚を `Boss's Orders` と
`Pokégear 3.0` へ置換）は親を `77/96` 対 `72/96`（+5勝）と上回ったため、最新方針
どおり384局/arm確認へ拡張した。

確認では candidate 7 が `267/384 = 69.5313%`、親nativeが
`268/384 = 69.7917%` で **-1勝 (-0.2604pt)** となった。全768局は `DONE`、fault 0
である。したがってこのmutationは `candidate_only` のままとし、
`EvaluationBestKnown` / `BestKnownArchaludon` / training / promotion / longrun /
submission には昇格させない。現行のnative tomato BestKnownを超えたという証拠はない。

## 固定した親asset・権限・探索境界

| 項目 | 値 |
|---|---|
| parent asset | `tomatomato_archaludon` |
| native policy SHA-256 | `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e` |
| parent deck file SHA-256 | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` |
| parent deck multiset SHA-256 | `54525a5079619af3f8c4b10c81a0aaf03b144fb2693ac7c6035c932aa375479f` |
| core signature | Relicanth 57×1、Duraludon 169×4、Archaludon ex 190×4、Cinderace 666×4 |
| replacement pool | native policyが明示的に扱う `8,1097,1121,1122,1147,1152,1159,1182,1185,1213,1227,1244` のみ |
| card vocabulary | `data/raw/EN_Card_Data.csv`（SHAは候補manifestに固定） |
| asset usage boundary | `local_eval_only`。bounded local teacher collectionの記録はあるが `behavior_allowed=false`、submission不可 |
| authority | candidate、manifest、実行結果の promotion/training/submission/execute はすべて false |

`main.py`、root `deck.csv`、Champion、submission package、training loopは変更・実行していない。

## 候補生成と整合性

`generate_deck_mutation_candidates_v1` を、seed `20260813`、swap count `(1, 2)`、各4候補で
用いた。候補は60枚、known card ID、core signature、親/候補間multiset重複なし、deck file
SHAとmultiset SHAのround-tripを検証してからmanifest化した。

候補manifest: `runs/final-sprint-autonomous/deck-mutation-tomato-v1/candidates.json`

```text
e34e1e36dd0254cf895fe447f1ec6d88e8c25db8c7d20ae09f1ac8987471a252  candidates.json
```

## CABT legality gate

最初のrule-vs-rule probeは、親が通る一方で全候補が `AGENT_INVALID` になった。これはdeckの
構造的不正ではなく、rule agentがtomato native policyと同じruntime挙動を保証しないことを
実測したものだった。gateを緩めず、**native tomato policy + official_random、両seat**の
実CABT cellへ変更した。

親＋8候補の18局は全て `DONE`、fault 0。これはscreenと同じ
`scripts.run_native_policy_candidate_pilot_v1:run_native_candidate_game_v1` のimport/policy/deck
境界で測ったoperational legalityである。

```text
3268734aee69afa3c275dd6f53cd496b08768872df2f608d0a10480ab4e8f06b  cabt_legality_probes.json
```

このgateはperformance evidenceではない。

## 96局screen

protocolは sealed broad-pool 24 opponentすべて、両seat、各opponent-seat 2回、各arm 96局。
親nativeを同block controlとして含めた。全864局は `DONE`、fault 0、runtime合計372.05秒。

| arm | swap | removed → added | W/L | score | 親差 |
|---|---:|---|---:|---:|---:|
| parent native | — | — | 72/24 | 75.0000% | — |
| candidate 0 | 1 | 1097 → 1152 | 59/37 | 61.4583% | -13.5417pt |
| candidate 1 | 1 | 1182 → 1185 | 62/34 | 64.5833% | -10.4167pt |
| candidate 2 | 1 | 8 → 1122 | 72/24 | 75.0000% | 0.0000pt |
| candidate 3 | 1 | 1097 → 1121 | 68/28 | 70.8333% | -4.1667pt |
| candidate 4 | 2 | 1185,1227 → 1159,1185 | 61/35 | 63.5417% | -11.4583pt |
| candidate 5 | 2 | 1097,1227 → 1244,1147 | 72/24 | 75.0000% | 0.0000pt |
| candidate 6 | 2 | 8,1185 → 1147,1097 | 63/33 | 65.6250% | -9.3750pt |
| candidate 7 | 2 | 1147,1244 → 1182,1122 | 77/19 | 80.2083% | +5.2083pt |

candidate 7のseat別は candidate `(seat0 36, seat1 41)`、親 `(seat0 40, seat1 32)`。
screen差は単一96局blockなので候補選別だけに用い、promotion根拠にはしない。

```text
dcc8fd9c5a5b1c1d463b41801b5b7783b6fc0239d4446ccf9077dd8b1a080e10  screen/ledger.jsonl
96d92531ec03b48086679394cec6c2820003339927b6cde3987c3d761c248b25  screen/summary.json
24fd91dfc96a1d2df379211da166f758336c2a6e1d9793053ccf960075a4e035  screen_summary.json
```

## candidate 7の384局/arm確認

candidate ID: `0a2fe5578f2d37ffa36ab65535cd1f9976427b806a1e8eef5f4e347b2545b57a`

| 項目 | candidate 7 | parent native | 差 |
|---|---:|---:|---:|
| W/L | 267/117 | 268/116 | -1勝 |
| score | 69.5313% | 69.7917% | -0.2604pt |
| seat 0 wins /192 | 135 | 127 | +8 |
| seat 1 wins /192 | 132 | 141 | -9 |
| fault | 0 | 0 | 0 |

candidate deck file SHA: `dc719d4b8c7224904562cbc40f896c2b24d6bfb6d0acf81ce034b80675538bb5`

candidate deck multiset SHA:
`7b4c4f0188224d86c749d8a182f9927487322facf2394be368206e569cc9f7eb`

24 opponentの差は `-3` から `+3` 勝に分散し、pooled差を一方向に支える証拠はない。
最大の正差は `kokinnwakashuu_lucario_search` と `lucifer19_battlecore` の各+3、最大の負差は
`naoto714_kangaskhan` と `pilkwang_lucario_alakazam` の各-3だった。

```text
c2750d7d09b46b54b6a4fa59b164fe4564e6f74afa8c849e4cb1122c441ebe8b  confirmation-candidate-7/ledger.jsonl
1a8ed4e86894c6e4f6e8c6caef2669e6b2613b8184e058f8e2b2083131c4ae09  confirmation-candidate-7/summary.json
489b46c0e0caae4977fcafe3ceaa8eb7df10fd6b7883dd484b55c847a648669b  confirmation-candidate-7/confirmation_summary.json
```

## 検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_run_tomato_deck_mutation_v1.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m py_compile \
  scripts/run_tomato_deck_mutation_v1.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/run_tomato_deck_mutation_v1.py \
  --output runs/final-sprint-autonomous/deck-mutation-tomato-v1 \
  --workers 12 --games-per-opponent-seat 2
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/run_tomato_deck_mutation_v1.py \
  --output runs/final-sprint-autonomous/deck-mutation-tomato-v1 \
  --workers 12 --confirm-candidate-index 7
```

focused testは4件PASS。screenとconfirmationの実CABTは合計1,632局、さらにnative-policy
legality 18局を実行した（すべてfault 0）。

## 残リスクと次の扱い

- 384局確認はscreenの+5勝を再現しなかった。candidate 7の追加768/1536局、policy変更、
  training、longrun、package、submissionへは進まない。
- 基準の `tomatomato_archaludon` native common-arena pooled1536は72.0703%であり、
  candidate 7のこの確認69.5313%はそれにも届かない。
- これは1種類のagent-aware support-count mutationを否定した結果であり、tomato native pair、
  Archaludon全体、public-state value/AWR/search、または別のdeck search proposal全般の否定ではない。
