# Agent security model

発見済み Python は repository process に import しません。secret、network、subprocess、filesystem write、environment mutation、dynamic execution を静的検査し、疑いがあれば `QUARANTINED` とします。安全な実行は後続の明示承認、quarantine 展開、clean-environment isolated subprocess、timeout/resource limit、CABT gate の後だけです。
