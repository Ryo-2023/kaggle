# self-owned runtime-safe cross-lineage basket v19 / P1 CEM

## 結論

v18のGrass/Venusaur `STEP_LIMIT` を受け、Fire/Charizard、Dark/Gengar、Lightning/Manectricの3 archetypeだけを、P1 parameterized lineage 3件と independent-root lineage 3件に分けて生成した。各source 4 games/seatの48局 runtime smoke は fault0で通過した。一方、P1固定CEMでは c00/c06に小さな正差分が出たものの、risk-aware lower-tailと opponent-seat-safe gateを満たさず、BestKnownは更新しなかった。

## source生成とgate

- P1 plan: [`self_owned_cg_policy_family_v19_runtime_safe.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/configs/meta_specialist/self_owned_cg_policy_family_v19_runtime_safe.json)、SHA `1c4afea8b1f4465cef6da73573c811f6b03ac27aa95ea9746562d21b9f074bbd`
- independent plan: [`self_owned_cg_independent_policy_family_v19_runtime_safe.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/configs/meta_specialist/self_owned_cg_independent_policy_family_v19_runtime_safe.json)、SHA `665bb0d629ece2928577cb922073e024b642dc22b0b1d4636065661246e66d51`
- staged pool SHA `b066a13e0c08f550e63b812e90fb9b329b2d585c7e61ce4d8e89c5e733867a11`
- existing pool policy/deck hash collision: 0
- promoted pool SHA `1855ffb53e2a3fe389d430a6741a7de859410174e6b31c6a011b0fc54db28a72`
- `fresh_meta.json` SHA `ec3614665bf4251fe2268b173732cf04a1e10d7a1a0ef3d9f1520ed8e741e8a6`
- split SHA `19a6b2362baa3c18aea70fbe453587d8679684de6b680cebec74c594cd7a852a`

6 sourceの強化runtime smokeは `48/48 DONE`・fault0、29W-19Lだった。これは性能採用ではなく、non-terminal sourceをCEMへ流さないための bounded gateである。

- smoke summary SHA `a6e943aacab9db5c65d2826e1e4a100e26a154ea1d1b6b4fec0a35199254960e`

## split とCEM

splitは `META_TRAIN=4`（P1 fire/dark、independent fire/dark）、`META_DEV=1`（P1 lightning）、`META_FINAL=1`（independent lightning）で、DEV/FINALはCEM中未読である。

[`runs/cg-p1-cem-self-owned-runtime-safe-v19-20260816`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/cg-p1-cem-self-owned-runtime-safe-v19-20260816) は population／elite `8／2`、1世代、screen144局、独立再評価192局（各source 4 games/seat×2 repeat）をすべて `DONE`・fault0で完走した。

- campaign manifest SHA `f5fde55634c0de58195860cba131f951c439c288fe4bc7628f0e32f7b67982d`
- generation manifest SHA `5483e00235de44c5e0c4de08d50e27385b395e69cd47d53ee1217bb49a269f68`
- results SHA `676538ca1845eacb2c0f3fa542cabcd4098724b006a29714d199cbf00d2da03f`
- c00 screen `14W-2L` vs control `12W-4L`（delta `+12.5pt`）、独立 repeat `−12.5pt / +18.75pt`、mean `+3.125pt`、minimum `−12.5pt`、`seat_safe=true`だが`opponent_seat_safe=false`
- c06 screen `13W-3L`（delta `+6.25pt`）、独立 repeat `0.0pt / +3.125pt`、mean `+1.5625pt`、`seat_safe=false`、`opponent_seat_safe=false`
- selection `risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`
- campaign `champion_changed=false`、elites `incumbent-center`×2、P1 center保持

## 判定と次の条件

判定は `SOURCE_GENERATION_PASS / RUNTIME_SMOKE_4X_PASS / CEM_FAULT0 / CEM_POSITIVE_BUT_RISK_OR_OPPONENT_SEAT_UNSAFE / BESTKNOWN_UNCHANGED`。v19 pool／seed／候補は性能使用済みとしてblind retryしない。DEV／FINALは strict gateを通る候補が無かったため未読のままとした。

次は sourceを増やすだけでなく、policy rendererが生成する opponent-seat varianceを抑える別のlineage（P1/independent root以外の明示的な action-conditioned renderer）を設計し、同じ4 games/seat runtime gateとCEM lower-tail gateへ接続する。
