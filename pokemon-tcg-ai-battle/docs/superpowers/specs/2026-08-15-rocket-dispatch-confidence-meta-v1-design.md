# Rocket Dispatch Confidence Meta v1 設計

## 目的

既存Rocket sourceのtheta、route token、classifier family valueを再配置するのではなく、公開観測からspecialistへcommitするタイミングと必要証拠量を変えた、runtime-safeなmeta source familyを生成する。早期の単一card誤commitが相手family判定を誤らせている可能性を検証し、P1固定CEMが未使用のpolicy variantへ転移するかを測る。

## 入力と不変条件

- base sourceは `runs/cg-fresh-internal-meta-intake-20260815-f/internal_ozawa-rocket-rule_de797c3646e9/` に固定する。
- `main.py` と `deck.csv` は同一sealed sourceから読む。deck、card data、action legality、observation extraction、theta table、import、environment、fallback、submission pathは変更しない。
- 追加する証拠は `current.players[opponent].active`、`bench`、`discard`、および公開 opponent log のcard IDだけとする。hidden hand、deck、future RNG、外部I/Oは参照しない。
- 生成物は全て `local_eval_only`、`research_only`、authority false。current poolを上書きせず、既存artifactをno-clobberで保護する。

## 変換単位

各variantは以下の3箇所だけを厳密に変換する。

1. dispatch stateへ `group_turns`（familyごとの公開証拠を観測したturn集合）を追加する。
2. 一つのfamilyを観測したturnを `group_turns` へ記録する。
3. 現在の単一commit条件へ、variant固有の `_dispatch_commit_allowed(...)` gateを追加する。

gateは既存のexclusive-group/conflict判定の後にだけ働き、conflict時は従来どおりgeneralへ戻す。variantは定数selectorだけが異なり、未知selectorは生成時にfail-closedする。

## variant recipe（12件）

| recipe | commit条件 |
|---|---|
| `GENERAL_ONLY` | specialistへcommitしない |
| `TURN1_DELAY` | turn 1以降 |
| `TURN2_DELAY` | turn 2以降 |
| `TWO_TURN_CONFIRM` | 同じfamilyを2 turn以上で観測 |
| `MULTI_CARD_CONFIRM` | 同一観測でrecognized cardを2枚以上観測 |
| `TURN1_OR_MULTI_CARD` | turn 1以降または2枚以上 |
| `TWO_TURN_OR_MULTI_CARD` | 2 turn以上または2枚以上 |
| `THREE_TURN_CONFIRM` | turn 3以降かつ2 turn以上 |
| `TWO_TURN_AND_MULTI_CARD` | 2 turn以上かつ2枚以上 |
| `TURN3_OR_MULTI_CARD` | turn 3以降または2枚以上 |
| `FOUR_TURN_CONFIRM` | turn 4以降かつ2 turn以上 |
| `TURN1_AND_MULTI_CARD` | turn 1以降かつ2枚以上 |

splitはTRAIN 8、DEV 2、FINAL 2。CEMはTRAINのみを読み、DEV／FINAL identityを探索・選抜に使わない。

## 検証とpromotion gate

1. source static finding 0、exact 60、compile、pool loader、split verification。
2. TRAIN-only両seat smokeで全局 `DONE`、fault 0、draw 0を要求する。
3. P1をcontrolに固定したCEMを実行し、独立seed・独立re-evaluationを使う。
4. candidateは平均deltaだけでなく、各独立blockのlower-tail、seat gap、opponent×seat gap、faultを同時に検査する。
5. robust positive、seat-safe、opponent×seat-safeを満たす候補がなければP1 centerを保持し、DEV／FINALを開かない。
6. 通過候補だけを `cg_bestknown_loop_v1.py` の次policy parentへ渡す。BestKnown、Champion、production、submission、commit、pushはこの実験から自動変更しない。

## 失敗時の扱い

同じRocket sourceのblind retryはしない。全variantがnegativeまたはseat-unsafeなら、このsource identityは「confidence gate familyで昇格根拠なし」と記録し、次はsource lineageが異なるpermission済みsnapshotまたはruntime-safe複数family compositionへ移る。
