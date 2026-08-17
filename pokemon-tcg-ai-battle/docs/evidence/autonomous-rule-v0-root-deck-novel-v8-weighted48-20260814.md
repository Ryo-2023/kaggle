# Rule v0/root deck novel surface v8 weighted48（2026-08-14）

既存 multiset・negative surface を除外し、`1142 Fighting Gong` の役割を `Switch` または `Premium Power Pro` に置換する2候補を、META_TRAIN weighted48でscreenした。

| arm | mutation | W-D-L-F / 48 | weighted delta | 判定 |
|---|---|---:|---:|---|
| parent | Rule v0 + root deck | 5-0-43-0 | control | control |
| candidate | Fighting Gong → Switch | 3-0-45-0 | −4.765pt | candidate-only / STOP |
| candidate | Fighting Gong → Premium Power Pro | 0-0-0-48 | invalid | AGENT_INVALID / no score |

Switch armは全48局DONE/fault0、seat0/1各24、12 opponents、GID/seed uniqueness PASSだったが明確negative。Premium Power Pro armは全48局が `AGENT_INVALID; cabt terminal result unavailable` であり、勝率・weighted scoreへ変換していない。両候補ともcommon24・384・longrunへ進めない。

実験rootは `runs/final-sprint-autonomous/rule-v0-root-deck-novel-v8-weighted48-20260814/`。manifest SHA `e2afec222de1d66909171c862366639677777fdbfaba0208276b3f7a4695d51f`、summary JSON SHA `97b40ca777e5fe5e64ba18c705fff695caebf3129c10d2cb1de98fac74f338d4`、summary MD SHA `a3450f84bb34951f0cffbda5558d74aba9dc22e5fbd08e511ac9d10d105f69ae`、runner SHA `9002c75d1653dee760beae9d8c890b358080cb0b7fc9d74ccb4fa941abaa2b7a`。

全authorityは research-only / execution、training、promotion、submission、longrun false。既存production・既存root・Poke Pad/Dusk Ball invalid armは変更せず、invalid armは再実行しない。一次JSONに各armのmanifest/summary/ledger SHAとintegrityを固定した。
