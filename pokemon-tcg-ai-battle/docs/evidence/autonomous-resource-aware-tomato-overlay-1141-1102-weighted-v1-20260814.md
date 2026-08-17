# Tomato native parent overlay weighted48（2026-08-14）

## 結論

Tomato native親に対する未評価1-card置換を、同一META_TRAIN weighted48でscreenした。`1244→1102 Dusk Ball` は親比 **−2.1083pt**、`1244→1141 Premium Power Pro` は **−5.7652pt**。全144局がDONE/fault0でプロトコルgateは通過したが、両候補とも親を下回ったため `candidate_only / NO-GO` とし、common24・384・longrun・submissionは起動しない。

| arm | W-D-L / 48 | weighted meta score | parent差 |
|---|---:|---:|---:|
| Tomato native parent | 33-0-15 | 0.691191539 | — |
| `1244→1102` Dusk Ball | 32-0-16 | 0.670108308 | −2.1083pt |
| `1244→1141` Premium Power Pro | 30-0-18 | 0.633539837 | −5.7652pt |

## 固定条件・gate

- parent deck SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- Tomato policy SHA `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- `1244→1102` multiset SHA `268e04ecd32b01f49c25c9d50bcd7fa87f95a1f921ac2e1f7183d754771410db`
- `1244→1141` multiset SHA `c00cd61314d482c04c4f9aca3a948ad711a91bf500f9b1762ed829e57be7af19`
- weighted subset SHA `09176f164b0f7719de70c903195e6b11b00dc3895ee8a98a154263fd8cbd72ed`
- evaluator SHA `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- pool manifest SHA `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`

各armは12 opponent×2 seat×2 repetition=48局。candidate/parentのopponent・seat・repetition・seed scheduleを一致させ、各arm seat24/24、opponent各4、GID/seed unique、DONE144/fault0を確認した。authorityはresearch_onlyのみで、execution/training/promotion/longrun/submissionはfalse。

## ResourceGovernor

warmup ramp `[1,2,4,8,12]` は各4局・fault0。weighted本体はworkers12、recycle16、144局、wall約8.213秒、throughput約17.533 games/s、restart0、kill0。GPU compute processなし、production/evaluator/既存一次artifact不変。

## Artifact

Root: `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1141-1102-weighted-v1-20260814/`

- manifest `ac4049685d3fbbff6aaf6c1ca2f6b87715ecb1474e18cd3fd41b369ae97d9b36`
- weighted summary JSON `40d34f8ad7698685f41b44ca4c9841320f334366668eaa87bf21d7ed18d06e96`
- weighted summary MD `abaf43a99b15838b06dbf577058a68374ae7c0b7774b3ebccca7cb2c6dbdf24f`
- final summary `1ad5468edb0a588e5e86a8157eac8cee65bc7b36daadde236c74a40651429c62`
- warmup telemetry `5bb854372b825b773fa62f7ada654e193dd5e697b95529cd0de36cbb25c36ea6`
- runner `9b186b7df504650b5d58a19d5703328be9104cae787aa3829becdb0c8284d329`

このsurfaceはFull Metal Lab置換群のhard-negativeとして保持する。同じ置換を再試行せず、次はdeck-policy alternatingまたは別deck surfaceを expected gain×success probability÷time で1本だけ選ぶ。
