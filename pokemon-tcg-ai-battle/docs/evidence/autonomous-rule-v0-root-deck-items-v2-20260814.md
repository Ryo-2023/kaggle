# Rule v0/root deck Item surface v2（2026-08-14）

P0 `Rule v0 + root deck` の既存 multiset と Supporter/既評価面を除外し、Item 1-card mutationを2件 screenした。weighted48では両候補が正値だったが、broad common24で両方とも親を下回ったため、384・longrunへ進めず停止した。

| candidate | mutation | weighted48 | common24 | 判定 |
|---|---|---:|---:|---|
| parent | root deck | 3-0-45-0 (weighted 0.06805) | 17-0-79-0 (17.7083%) | control |
| Pokegear | 1141 Premium Power Pro → 1122 Pokégear 3.0 | +4.7211pt | 10-1-85-0, −6.7708pt | candidate-only / STOP |
| Ultra Ball | 1123 Switch → 1121 Ultra Ball | +6.6071pt | 12-0-84-0, −5.2083pt | candidate-only / STOP |

weighted は base `23450000`、common24 は base `23460000`。両段階とも workers 12、recycle 16、同一 arm 内で24 opponents・両seat・unique game ID/seed、全288 common24局 DONE/fault0。held-out exposure、training/promotion/submission/longrun authority は false。weighted の短期正値を昇格根拠にせず、common24 の明確な負値でこの2 surfaceを hard-negative として閉じる。一次 SHA は同名 JSON に固定した。
