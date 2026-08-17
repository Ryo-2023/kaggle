# Family Opponent Population Expansion and Scheduled Ingestion v1

実装は候補資産の read-only inventory、deck 正規化、agent 静的監査、fail-closed binding、incremental watermark と scheduler template を提供します。候補 Registry は active training Population ではなく、外部コードの自動実行・自動昇格を行いません。

初回の local/ref scan は candidate registry を生成し、同一入力の二回目は digest watermark により解析を skip します。外部公開 source は設定で明示されたものだけを扱い、未設定・認証不可は local/manual-drop の処理を止めません。
