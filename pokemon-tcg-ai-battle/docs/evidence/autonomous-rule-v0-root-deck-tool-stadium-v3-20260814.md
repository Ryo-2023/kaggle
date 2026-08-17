# Rule v0/root deck Tool/Stadium surface v3（2026-08-14）

P0 `Rule v0 + root deck` の既存 multiset と既評価 Supporter/Item 面を除外し、Tool/Stadium の1-card mutationを2件 screenした。weighted48では両候補が正値だったが、broad common24で両方とも親を下回ったため、384・longrunへ進めず停止した。

| candidate | mutation | weighted48 | common24 | 判定 |
|---|---|---:|---:|---|
| parent | root deck | 1-0-47-0 (weighted 0.02531) | 14-0-82-0 (14.5833%) | control |
| Maximum Belt | 1159 Hero Cape → 1158 Maximum Belt | +6.2839pt | 7-0-89-0, −7.2917pt | candidate-only / STOP |
| Festival Grounds | 1252 Gravity Mountain → 1245 Festival Grounds | +8.5496pt | 10-0-86-0, −4.1667pt | candidate-only / STOP |

weightedはbase `23470000`、common24はbase `23480000`。両段階ともworkers 12、recycle 16、24 opponents・両seat・unique game ID/seed、全288 common24局 DONE/fault0。held-out exposure、training/promotion/submission/longrun authorityはfalseである。weightedの短期正値を昇格根拠にせず、common24の負値でこのTool/Stadium surfaceをhard-negativeとして閉じる。一次SHAは同名JSONに固定した。
