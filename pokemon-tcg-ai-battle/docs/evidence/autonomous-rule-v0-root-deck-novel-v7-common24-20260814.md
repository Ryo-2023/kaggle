# Rule v0/root deck Hariyama common24 guardrail（2026-08-14）

weighted48で唯一positiveだった `674 Hariyama → 673 Makuhita` を、同一Rule v0/root policy・fresh seed・common24で96局確認した。親は **10-0-86-0（10.4167%）**、候補は **9-0-87-0（9.3750%）** で、候補は **−1.042pt** に反転した。全192局がDONE/fault0/draw0で、両armとも24 opponent、seat0/1各48、GID/seed uniqueness PASSだったため、これは性能値を有効にしたうえでのnegative gateである。

| arm | mutation | W-D-L-F / 96 | score | 判定 |
|---|---|---:|---:|---|
| parent | Rule v0 + root deck | 10-0-86-0 | 10.4167% | control |
| candidate | Hariyama x1 → Makuhita | 9-0-87-0 | 9.3750% | **candidate-only / STOP** |

実験rootは `runs/final-sprint-autonomous/rule-v0-root-deck-novel-v7-common24-96-20260814/`。manifest SHA `44cc62bcf1f4305443c8f0338601ec74f02c7db43243ff9d8e03fe1b49a5ab84`、summary JSON SHA `e02d9f045de6422bb5463e369f4dd051301ed3c35cec7dd335df33525fff2bcd`、summary MD SHA `ebfb4cf1838d412f0bd0d3c7b62d412a67e1d2eaaebc1ac5dedfee6e5a2b2dc3`。runner SHA `9b91c39751a07440aa63f0938fdc6b194eac1f382430b3d31874c79f07e42f62`。384、768、longrun、submission、Champion変更は行わない。

v7 weightedで同時に試した `1152 Poké Pad → 1102 Dusk Ball` は全48局 `AGENT_INVALID`（fault 48、性能値なし）だった。該当root・manifest・ledgerは保全し、common24へ再投入していない。

全authorityは research-only / execution、training、promotion、submission、longrun false。既存production runner、既存artifact、permission、commit/pushは変更していない。一次JSONにprotocol・seat/opponent/GID/seed・invalid armのSHAを固定した。
