# V4 公開 on-policy outcome ledger の事前診断（2026-08-12）

## 結論

次の性能主線は、public-only search/Q の実 target 生成ではなく、まず既存の V4 actor-pool 遷移から weak matchup の公開状態・候補集合・選択 action・最終 outcome を結び付ける residual/OOD preflight とする。これは production runtime を変更せず、相手 ID をモデル入力へ漏らさず、既存の sealed trajectory を再利用できるためである。

本資料は学習・promotion・Champion変更の根拠ではない。勝敗と action の同時出現を記録した記述統計であり、因果的な action value を意味しない。

## 入力 identity

対象は Wave6 V4 checkpoint から収集済みの fixed-six actor-pool screen である。両 screen とも同一の Archaludon subject deck（`opponents/tomatomato_archaludon/deck.csv`、SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`）、6 opponents、両 seat、96 games、fault 0 である。CABT engine seed setter はなく、game-level paired evaluation ではない。

| screen | checkpoint file SHA | games / transitions | screen JSON SHA | transitions JSONL SHA |
|---|---|---:|---|---|
| Wave6 seed0 | `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de` | 96 / 4,763 | `9ad78bf31f41d307916b25d238544ea5060e0df9a8e16b5ca72a8e3977fc00e3` | `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce` |
| Wave6 seed1 | `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6` | 96 / 5,590 | `aed7438628ffdcb4a4d0c11a844c6ddea4a75017be44912180e3f6dc90abf1f1` | `2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26` |

各 transition は actor-visible `model_input`、semantic prefix、合法候補集合、chosen semantic complete action、behavior log probability、episode/outcome join key を持つ。RuntimeDecisionTrace の redacted/duplicate 行は候補 action type を保持しないため、この診断では使わず、sealed actor-pool transition を正とした。

## weak opponent の記述統計

weak cell として fixed-six の `ozawa_crustle_v2`、`skarin_dragapult`、`sue124_alakazam` を抽出した。2 seed 合算で 96 games のうち 58 losses / 38 wins、遷移は loss 2,698、win 2,149 だった。ゲーム長が outcome と相関するため、下表はまず 1 game あたりへ正規化している。

| outcome | games | transitions | transitions / game | PLAY(7) / game | ATTACH(8) / game | EVOLVE(9) / game | RETREAT(12) / game | ATTACK(13) / game | END(14) / game |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| loss | 58 | 2,698 | 46.52 | 13.43 | 4.52 | 1.33 | 0.31 | 5.16 | 2.21 |
| win | 38 | 2,149 | 56.55 | 15.32 | 5.11 | 1.58 | 0.79 | 7.16 | 1.29 |

遷移割合へ直すと、loss は END が約 4.7%、win は約 2.3%で、ATTACK は loss 約 11.1%、win 約 12.7%、RETREAT は loss 約 0.7%、win 約 1.4%だった。ただし win の方が平均ゲーム長も長いため、これは「ENDを抑えれば勝てる」「ATTACK/RETREATを増やせば勝てる」という結論ではない。早期敗戦、局面の違い、seat/opponent interaction、action legality の違いが混ざっている。

## 何が分かり、何が分からないか

分かることは、weak cell で outcome と一緒に記録可能な公開 action type の分布が seed・seat・opponent で変わること、そして `screen.transitions.jsonl` が今後の OOD/confidence 計算に使える十分な actor-visible payload を持つことである。特に RuntimeDecisionTrace の privacy redaction だけでは空になる action type を、actor-pool transition から復元できる。

分からないことは、どの action が敗因だったか、同一 public state で別 action を選んだ場合の反実仮想 outcome、opponent IDを入力にしない runtime residual の有効性である。現行 V4 は value/Q/uncertainty headを持たず、既存 `search_teacher_v1.py` も Q/visit生成ではなく soft target blenderだけである。

## 次の bounded preflight

1. `screen.transitions.jsonl` の public `model_input` / `step_input` / logits だけから、candidate-domain size、top1-top2 margin、entropy、prefix長、STOP可否、normalized surprisal を再計算する。
2. train側の公開特徴分布を固定し、OOD基準と confidence threshold を勝率を見ずに一度だけ決める。未知・malformed・privacy欠落は V4 unchanged へ fail-closed とする。
3. weak opponent ID・seat は training sample/component の選択と集計にだけ使い、checkpoint/runtime payloadには保存しない。`supervision_weight=0` の context-only 行は GRUを通すが loss denominatorへ入れない。
4. zero-init residual または loss-only public overlay を、Wave6対応 seed0/1・同一 snapshot・fixed-six 24 games/seedで alpha=0 control と比較する。
5. 両 seed・両 seat・fault0・対応 baseline以上を満たした場合だけ shadow-B へ進む。aggregateの正方向だけ、seed反転、seat崩壊、OOD特徴欠落のいずれかなら residual 系列を打ち切る。

## public-only search を後回しにする理由

現状は公開状態からの determinization、合法 action後の状態遷移、bounded rollout、Q/visit provenance が未実装である。CABT native search は hidden state と opaque `search_begin_input` を要求し、過去監査で 6–16 秒 block、SIGSEGV、binary identity 不一致が確認されている。従って search は契約テストだけでも数時間、実 target 生成は数日以上かかる別プロジェクトであり、現在の GPU pilot の次に置くには不確実性が高い。

## 判定

この ledger は「次に使える公開データ境界」を確認した段階であり、性能改善・teacher quality・causal action value の証明ではない。次の実装は residual/OOD eligibility の RED 契約から開始し、Rule v0 action-type alpha=1（shadow-B 合計 43/96、alpha=0 51/96）を再利用・再sweepしない。

`promotion_authority=false`、Champion変更なし、長時間学習なし、Kaggle提出なし。
