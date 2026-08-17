# Family opponent candidate review v1

666 agent record のうち、静的 quarantine を除いた 466 件を決定論的に分類した。340件は exact deck と同一 source に結び付く根拠がなく `NO_SUPPORTED_DECK`、126件は test/documentation path で callable entrypoint を確定できず `ENTRYPOINT_UNRESOLVED` である。public または usage evidence が不足する候補は実行していない。

既存 team-approved evidence を持つ Lucario、Abomasnow、Alakazam の3 bindingのみを review 通過とした。これは新規外部コードの auto-approval ではなく、既存の exact deck/runtime identity、isolated loader、CABT smoke を照合した再登録である。
