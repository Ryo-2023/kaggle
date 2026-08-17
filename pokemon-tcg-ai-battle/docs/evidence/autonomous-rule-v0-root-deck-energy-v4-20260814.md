# Rule v0/root deck Energy surface v4（2026-08-14）

P0 `Rule v0 + root deck` の既存 multiset と既評価 Supporter/Item/Tool/Stadium 面を除外し、Basic Fighting Energy 1枚を特殊エネルギーへ置換する2件をscreenした。weighted48では両候補が正値だったが、broad common24で両方とも親を下回ったため、384・longrunへ進めず停止した。

| candidate | mutation | weighted48 | common24 | 判定 |
|---|---|---:|---:|---|
| parent | root deck | — | 11-0-85-0 (11.4583%) | control |
| Rock Fighting Energy | 6 Basic Fighting → 20 Rock Fighting | +2.6837pt | 8-0-88-0, −3.1250pt | candidate-only / STOP |
| Mist Energy | 6 Basic Fighting → 11 Mist Energy | +2.0186pt | 7-0-89-0, −4.1667pt | candidate-only / STOP |

weightedはbase `23490000`、common24はbase `23500000`。両段階ともworkers 12、recycle 16、24 opponents・両seat・unique game ID/seed、全288 common24局 DONE/fault0。held-out exposure、training/promotion/submission/longrun authorityはfalseである。weightedの短期正値を昇格根拠にせず、common24の負値でEnergy surfaceをhard-negativeとして閉じる。一次SHAは同名JSONに固定した。
