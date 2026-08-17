# Rule v0/root deck Pokémon line surface v5（2026-08-14）

P0 `Rule v0 + root deck` の既存 multiset と既評価 Supporter/Item/Tool/Stadium/Energy 面を除外し、Solrock/Lunatone の1枚比率変更を2件 screenした。weighted48では両候補が正値だったが、broad common24で両方とも親を下回ったため、384・longrunへ進めず停止した。

| candidate | mutation | weighted48 | common24 | 判定 |
|---|---|---:|---:|---|
| parent | root deck | — | 11-0-85-0 (11.4583%) | control |
| Lunatone +1 | 676 Solrock → 675 Lunatone | +2.4331pt | 4-0-92-0, −11.4583pt | candidate-only / STOP |
| Solrock +1 | 675 Lunatone → 676 Solrock | +4.5705pt | 12-0-84-0, −3.1250pt | candidate-only / STOP |

weightedはbase `23510000`、common24はbase `23520000`。両段階ともworkers 12、recycle 16、24 opponents・両seat・unique game ID/seed、全288 common24局 DONE/fault0。held-out exposure、training/promotion/submission/longrun authorityはfalseである。weightedの短期正値を昇格根拠にせず、common24の負値でPokémon line surfaceをhard-negativeとして閉じる。一次SHAは同名JSONに固定した。
