# Rule v0/root deck tech surface v6（2026-08-14）

既存151 multisetおよびSupporter/Item/Tool/Stadium/Energy/Pokémon-lineの既評価面を除外し、`6→16 Prism Energy` と `677→682 Stonjourner` をweighted48で比較した。両候補とも親を下回ったため、common24・384・longrunへ進めず停止した。

| candidate | mutation | W-D-L-F | weighted delta | 判定 |
|---|---|---:|---:|---|
| parent | root deck | 5-0-43-0 | control | control |
| Prism Energy | 6 Basic Fighting → 16 Prism Energy | 4-0-44-0 | −3.661pt | candidate-only / STOP |
| Stonjourner | 677 Riolu → 682 Stonjourner | 3-0-45-0 | −4.389pt | candidate-only / STOP |

48局/arm、12 opponents、両seat各24、workers12/recycle16、同一seed schedule、全144局DONE/fault0/draw0、game ID/seed uniqueness PASS、held-out exposure=0、authority false。両候補は明確negativeのためcommon24を起動しない。一次SHAは同名JSONに固定した。
