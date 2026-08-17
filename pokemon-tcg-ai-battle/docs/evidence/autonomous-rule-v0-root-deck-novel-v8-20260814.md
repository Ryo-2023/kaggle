# Rule v0/root deck novel v8（invalid arm; STOP）

既存155 multiset、negative面、v7 invalid候補を除外し、`1142 Fighting Gong→1123 Switch` と `1142 Fighting Gong→1141 Premium Power Pro` をweighted48でscreenした。

| arm | W-D-L-F | weighted delta | 判定 |
|---|---:|---:|---|
| parent | 5-0-43-0 | control | control |
| Gong→Switch | 3-0-45-0 | −4.7650pt | candidate-only / STOP |
| Gong→Power Pro | 0-0-0-48 | 未集計 | `AGENT_INVALID` 全48局 |

全armのseed/GID/seat identityは検証済みだが、invalid armがあるためaggregateは`all_faults_zero=false`。Power Proの0勝は性能値に変換しない。common24/384/longrunは起動していない。

Run root: `runs/final-sprint-autonomous/rule-v0-root-deck-novel-v8-weighted48-20260814/`。manifest SHA `e2afec222de1d66909171c862366639677777fdbfaba0208276b3f7a4695d51f`、summary SHA `97b40ca777e5fe5e64ba18c705fff695caebf3129c10d2cb1de98fac74f338d4`、MD SHA `a3450f84bb34951f0cffbda5558d74aba9dc22e5fbd08e511ac9d10d105f69ae`。全authority false。
