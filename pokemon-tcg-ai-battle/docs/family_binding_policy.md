# Family binding policy

binding は専用 deck-agent の組を最優先し、次に Family hard constraint、静的 dependency の順で構築します。証拠のない組は `CANDIDATE_BINDING` または `RULE_V0_ONLY` であり、Family opponent として数えず active 化しません。
