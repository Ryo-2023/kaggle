# Rule Agent v0 の判断メモ

## 決定

`rule` は、合法 option のインデックスだけを返す stateless な規則エージェントとして実装する。主選択では `EVOLVE`、`ATTACH`、`PLAY`、`ABILITY`、`ATTACK`、`END` の順に優先し、補助選択では必要最小数を安定順で選ぶ。これにより、`END` より明確に生産的な合法手を優先しながら、未知の選択形式でも範囲外や重複を返さない。

## 前提と観測

- 確実: cabt は deck 登録時に `select` を持たない観測を渡し、選択時は整数 option index のリストを受け取った。
- 確実: 一時的な 3 試合以上の観測で selection type は `0`, `1`, `8`, `9`、context は `0`, `1`, `2`, `4`, `7`, `22`, `38`, `41` を確認した。
- 確実: 主選択（type/context `0`）で option type `7`, `8`, `9`, `12`, `13`, `14`、補助選択で `0`, `1`, `2`, `3` を確認した。既存実装と実測の対応から `7=PLAY`、`8=ATTACH`、`9=EVOLVE`、`13=ATTACK`、`14=END` を利用する。`12` は公開 enum 根拠を確認できなかったため、名前や優先度を付けず未知値の安定 fallback として扱う。
- 確実: option の安全な scalar 構造として `type`, `index`, `area`, `inPlayArea`, `inPlayIndex`, `playerIndex`, `attackId`, `number` を観測した。`damage`, `hp`, `energyAttached` は将来の公開 option に備えた allowlist であり、この観測では未使用だった。
- 未検証: context の全 enum 名。未知 context は意味づけせず安定 fallback にする。

## 情報境界

読むのは `select`、`current.yourIndex`、上記 allowlist の option scalar だけである。相手手札、山札順、`search_begin_input`、ログ、opaque payload、ファイル、過去対局データは読まず、保存もしない。

## 反証と残リスク

最強の反論は、`PLAY` が常に盤面展開に寄与するとは限らない点である。v0 は card payload を解釈せず、合法 option と公開型だけで安全性を優先する。反証には card 種別を公開 observation の allowlist から安定して取得できる証拠が必要であり、v1 の最小改善候補とする。
