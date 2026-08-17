# Manual drop guide

artifact root の `incoming/decks`、`agents`、`submissions`、`notebooks`、`metadata` にファイルを置きます。次回 incremental run が digest を記録し、同一入力は無害に skip します。処理済みファイルを自動削除しません。
