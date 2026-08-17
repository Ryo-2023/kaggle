# 判断記録: crustle レーンは ozawa デッキをそのまま採用 (2026-08-05)

## 判断

`crustle_mega_kangaskhan` レーンのデッキは **`ozawa_crustle_v2` の構築をそのまま採用**する。
正典 `configs/meta_specialist/archetypes_v1.json` の core 定義には合わせない。

判断者: ユーザー (2026-08-05)。

## 観測された不一致

正典の crustle レーン core は `[344, 345, 756]` だが、`opponents/ozawa_crustle_v2/deck.csv`
はこの 3 枚をすべては含まない。プール 65 体のうち core を満たすのは
`meta_2_2a7de279` (Kaggle 実ログ由来のスクレイプデッキ) のみだった。

```
crustle_mega_kangaskhan core: [344, 345, 756]
core を満たすプール内デッキ: meta_2_2a7de279
ozawa_crustle_v2: 満たさない
```

## TODO: 後で比較する

**どちらが正しいかは未確定である。** 次の 2 つの可能性があり、切り分けていない。

1. 正典の core 定義が、実際の上位 crustle 構築と乖離している (core 定義側の誤り)
2. `ozawa_crustle_v2` が正典の想定する crustle 亜種とは別の構築である (デッキ側の相違)

**後日、両者を同一条件で対戦させて比較すること。** 具体的には、同じ相手プール・同じ
局数・座席均衡で `ozawa_crustle_v2` と `meta_2_2a7de279` の勝率を測り、正典 §2.2 の
census 比率と突き合わせる。core 定義を変更する場合は正典側の decision record を要する
(正典 §5: registry の自動変更は行わない。変更は別 decision record と明示承認を必要とする)。

それまでは本判断を「実務上の採用」であって「core 定義の是認」ではないものとして扱う。

---

## 追記: 不一致の正体と、別レーンとしての登録 (2026-08-05)

### 実測

`ozawa_crustle_v2` のデッキ内訳:

| Card ID | 枚数 |
|---|---:|
| 344 (Crustle) | 4 |
| 345 (Dwebble) | 4 |
| **756 (Mega Kangaskhan ex)** | **0** |

正典の `crustle_mega_kangaskhan` core は `[344, 345, 756]` である。ozawa のデッキは
メガガルーラ ex を含まない**純イワパレス**であり、ozawa 自身の分類でいう A02a に当たる
(bench_roster v3: イワパレス亜種 A02a-e は統合していない。亜種別 CEM で 4 亜種すべてが
独立に正の勝率ゲインを示したため)。

したがって**正典の core 定義が誤っていたのではなく、別の亜種だった**。§13 の TODO で
想定していた「core 定義側の誤り」ではなかったので、その仮説は棄却する。

### 対応: 新レーン追加を試みて撤回した

当初 `crustle_pure` (core `[344, 345]`) を 6 番目のレーンとして追加した。しかし
`seed_registry` は「seed registry requires exactly five registered lanes」を
**不変条件として強制**しており (正典 §5 の 5 runtime ID に由来)、54 件のテストが
落ちた。この不変条件は正典が守るべきものなので、**追加を差し戻した**。

`crustle_mega_kangaskhan` の core は無変更のままである。したがって
`ozawa_crustle_v2` はこのレーンの subject デッキとして使えない
(`deck is missing core card IDs: [756]` で fail-closed する)。

### 当面の扱い

4 レーン並列は正典の 5 系統の内側で組む: `grimmsnarl_froslass_munkidori` /
`rocket_mewtwo_spidops` / `alakazam` / `archaludon`。crustle は当面外す。

crustle を入れる場合の選択肢は 2 つあり、**どちらも未検証**である。

1. `meta_2_2a7de279` (プール内で唯一 core `[344, 345, 756]` を満たす、Kaggle 実ログ
   由来のデッキ) を `crustle_mega_kangaskhan` の subject にする。操縦は
   `agents.generic_agent` で、強度は未測定。
2. 5 レーン制約そのものを見直す。正典 §5 の変更に当たるため、別の decision record と
   明示承認が要る。

### 残る TODO (当初のものを更新)

`crustle_pure` (ozawa) と `crustle_mega_kangaskhan` (`meta_2_2a7de279`) を同一条件で
比較する。どちらを提出候補にすべきかは未検証である。ozawa の CEM 記録では
純イワパレス A02a が +6.00pp [+3.04, +8.97] だが、これは θ_v2 rocket 側の専門家化の
ゲインであり、デッキ同士の比較ではない。
