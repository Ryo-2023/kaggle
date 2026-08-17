# Source and license policy

source registry は commit/blob digest、path、source ref、visibility、trust class、license/usage evidence を記録します。license または利用根拠が不明な public code は inventory と quarantine には残せますが、実行 Population には自動追加しません。

remote ref は `git for-each-ref`、`git ls-tree`、`git show` の読み取りだけで扱い、checkout、pull、upstream 設定、remote branch 作成は行いません。
