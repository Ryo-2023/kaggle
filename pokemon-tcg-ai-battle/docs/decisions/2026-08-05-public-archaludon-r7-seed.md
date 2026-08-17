# 公開 Archaludon R7 pilot の取り込み判断

- 日付: 2026-08-05
- 対象: `opponents/public_archaludon_cinderace_r7`
- 状態: 取り込み済み。**採用可否は実測待ち**（後述）

## 結論

公開されている Archaludon ex / Cinderace の R7 版 pilot を、**新規の相手 ID として**
プールへ追加した。既存の `opponents/tomatomato_archaludon` は変更していない。

## 出所

| 項目 | 値 |
|---|---|
| repository | `https://github.com/TomBombadyl/kaggle_pokemon` |
| commit | `39545440b0cf4ab6175a45742e525d0628ca5e68` |
| 上流ファイル | `agent/archaludon_agent.py`, `agent/archaludon_bench_guard.py`, `agent/empty_bench_guard.py`, `agent_decks/archaludon_ex_cinderace.csv` |
| deck sha256 | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` |
| usage boundary | `local_eval_only` |

commit を固定する。上流の R8〜R12 系は公開ラダーで R7 を下回ったと報告されている
ため、最新版へ追従しない。

## 既存 `tomatomato_archaludon` との関係（確認済み）

**デッキは同一、エージェントは別物。**

- `opponents/tomatomato_archaludon/deck.csv` の sha256 は上流 deck と完全一致した。
  60 枚は同じである。
- 一方、R7 の識別子 `ARCHALUDON_BENCH_GUARD` / `apply_bench_guard` /
  `_legal_fallback` / `_is_legal` は、既存 `tomatomato_archaludon` の
  ファイル群に **1 つも存在しない**（0/5）。既存側は公開ノートブック原版である。

したがって、既存 ID の実測値（16 相手・300 局で 5.0%）を R7 の成績として読み替えて
はならない。両者は別 ID として別々に測る。

## 単一ファイルへ畳んだ理由

`opponent_pool_v1` の `policy_hash` は `main.py` のバイト列だけを検証する。上流の
配布形（`main.py` shim + 3 module）のまま登録すると、方策本体
`archaludon_agent.py` を書き換えても hash 検査を通ってしまい、**この相手だけ整合性
検査が飾りになる**。既存 65 体と同じ「自己完結した 1 つの `main.py`」へ畳んだ。

畳む際の機械的変更は 3 点のみで、判断ロジックには触れていない。詳細と再生成手順は
`scripts/vendor_archaludon_r7.py` の docstring を正とする。

1. `empty_bench_guard.apply_bench_guard` が archetype 側の同名関数と衝突するため
   `_generic_apply_bench_guard` へ改名（呼び出し側も同時に変更）
2. module 間 import の削除（同一 namespace になるため）
3. 上流 `main.py` の `os.getcwd()` ベースの sibling 解決は、畳んだ結果不要になった
   （本リポジトリの harness では cwd が repo root であり、そのままでは成立しない）

## 公開ラダー値の扱い

上流で報告されている μ = 1196.1 / peak 1224.2 は、**採用根拠として使わない**。
Kaggle の rating は新規提出が μ=600 から始まり近い rating の相手と継続対戦して
更新されるため、時期の異なる提出間で公平な絶対値ではない（正典 §2.5）。
公開値は「この個体を候補として調べる理由」までとし、採用可否は
`scripts/measure_opponent_strength.py` による自プールでの座席均等実測で決める。

## 未確定

- 自プール（archaludon-teacher-300 と同じ 16 相手）での実測値。
- 実測が既存個体を有意に上回る場合、archaludon レーンの teacher を
  `tomatomato_archaludon`（5.0%）から差し替えるかどうか。差し替える場合は
  `docs/decisions/2026-08-05-archaludon-teacher-derivation.md` を更新する。

---

## 追記: プール全体の deck 解決バグ (2026-08-05)

R7 を測ろうとして、**プールの相手が自分のデッキを読んでいなかった**ことが判明した。

多くの公開エージェントは `open("deck.csv")` と cwd 相対で読む。Kaggle では cwd が
提出ディレクトリなので正しいが、本リポジトリの harness では cwd が repo root で
あり、そこの `deck.csv` は**提出用デッキ**である。合法手は cabt が hard truth なので
非合法手は出ないが、相手は自分のものではない 60 枚を前提に**判断**していた。

実測（同一 16 相手・座席均等 160 局）:

| agent | 修正前 | 修正後 |
|---|---:|---:|
| `tomatomato_archaludon` | 5.6% | **75.0%** |
| `public_archaludon_cinderace_r7` | 2.5% | **76.3%** |

**「ブリジュラスは 5% で教師にならない」という判断は、このバグの産物だった。**
`archaludon-teacher-300`（15勝285敗）もこの状態で収集されているため、教師データ
として使えない。再収集が必要である。

`scripts/patch_opponent_deck_paths.py` で 21 体を修正した（既に修正済み 14 体、
cwd 参照なし 34 体）。判定は静的解析ではなく `builtins.open` を観測する probe で
行い、自分の deck.csv を読んだ個体だけ採用、それ以外は元へ戻している。

残る `meta_*` 7 体の repo-root 読みは `agents.generic_agent` 側のもので、これらは
`make_agent(_read_deck())` に正しいデッキを明示的に渡しているため実害はない。

### 上流公開値との関係

R7 の μ=1196.1 は、本プールでの 76.3% と整合的な方向ではある。ただし依然として
別物の指標であり、採用根拠は自プールの実測とする。なお R7 と原版の差は
160 局では有意でない（76.3% vs 75.0%、CI が大きく重なる）。R7 の bench guard が
効く局面は限定的である可能性が高く、**どちらを教師にするかは追加測定で決める**。
