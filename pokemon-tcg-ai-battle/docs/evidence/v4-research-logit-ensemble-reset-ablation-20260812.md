# V4 frozen-base research adapter: logit ensemble / recurrence reset preflight

## 結論

ChatGPT Proレビューの「Wave6を凍結したまま、複数モデルの独立hiddenを保ちつつsemantic logitsを合成する」案を、production runtimeを変更しない研究専用adapterとして実装した。最小TDDは `7 passed` で、実V4 policy 2個を既存のsemantic decoderへ通し、各memberのGRU hiddenを別々にcommitできることまで確認した。

これはproduction評価経路／学習／提出へ接続していない。後述のresearch-only小blockは診断結果であり、性能改善やlongrun readinessを意味しない。

## 実装境界

- 実装: `src/mage_ptcg/meta_specialist/research_logit_ensemble_v1.py`
- テスト: `tests/meta_specialist/test_research_logit_ensemble_v1.py`
- 実装SHA-256: `4f93716278215c2fbfcb079b800b0ba23bb04f09d121cbdf483877ce5fa296db`
- テストSHA-256: `a1b46d541c9326d51d655d7a680e9711d10f464ef6e9233661f77dfbd63ab7d3`

adapterは `make_agent` に渡すproduction実装ではない。`ResearchLogitEnsemblePolicyV1` は `reset` と `begin_decision` を実装する研究境界だが、runtime telemetry、checkpoint lineage、deck binding、CABT timeoutのproduction bindingを自動で付与しない。

## 保持する契約

1. 同じ `SpecialistModelInputV1` と `SpecialistStepInputV1` を全memberへ渡す。
2. 各memberが返す `SpecialistStepLogitsV1` の semantic domain 長を一致検査する。domainの並び替えやlocal aliasの再解釈は行わない。
3. STOPが合法なときは全memberの有限STOP logitを要求し、semantic logitsと同じ算術平均をdecoderへ渡す。STOPが非合法なときにSTOP logitを返すmemberは失敗させる。
4. 平均後の `SpecialistStepLogitsV1` を既存 `greedy_decode_runtime_action_v2` に入力する。adapter独自のphysical action／alias decoderは持たない。
5. 一つのcomplete actionの全prefixは各memberの一つのsessionを再利用する。prefixごとにGRUを進めず、runtimeがcommitした一回だけmember固有のnext hiddenを渡す。
6. `commit` は同じ semantic actionを全memberへ渡すが、`next_recurrent_state_token` は各member sessionから取得する。ensemble sessionが保持するstate tokenはmemberごとのtupleであり、共有・平均・上書きしない。

## reset mode

`reset_mode` は以下の3値だけを受け付ける。

| mode | 意味 | 実装上の境界 |
|---|---|---|
| `normal` | 通常carry | `begin_decision`ではresetせず、game-level `reset()`だけが全memberをresetする |
| `action` | actionごとreset | 各 `begin_decision` の直前に全memberをresetする |
| `turn` | turnごとreset | 最初のprefixで `state_scalars[2]` を読み、前actionのturnと変わった場合だけ全memberをresetする |

一つのaction内でmodel inputのturnが変わる場合はfail-closedにする。これは途中でresetしてhidden trajectoryを曖昧にしないためである。

## TDD検証

実行コマンド:

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=.tmp-test PYTHONPATH=.:src \
  .venv/bin/pytest -q -s tests/meta_specialist/test_research_logit_ensemble_v1.py
```

結果: `7 passed`（factory/telemetry追加後の全focused suiteは関連runner testを含め10 passed）。

検証した内容:

- semantic logitsとSTOP logitsが算術平均され、decoderが平均後の一つのdomainだけを見る。
- fake memberで、decoderの複数prefixが同一member sessionへ入り、commit時に左右それぞれのhidden tokenが保存される。
- `normal`／`action`／`turn` のreset境界を別々に確認。
- domain arity不一致をfail-closed。
- 実V4 policy 2個を既存decoderへ通し、empty STOP actionを含む合法なaction objectと、互いに異なるTensor hidden token 2個をcommitまで確認。
- adapter呼び出しがV4 model parameterを変更しないことを確認。

追加確認:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/python -m py_compile \
  src/mage_ptcg/meta_specialist/research_logit_ensemble_v1.py \
  tests/meta_specialist/test_research_logit_ensemble_v1.py
git diff --check -- \
  src/mage_ptcg/meta_specialist/research_logit_ensemble_v1.py \
  tests/meta_specialist/test_research_logit_ensemble_v1.py
```

両方とも成功した。CABTはresearch-onlyの24局/cell小blockだけ実行し、96-game block、training、longrunは実行していない。

## evaluator接続で固定したものと残課題

実測は研究runnerに限定して行い、次の条件を固定した。

1. 同一Wave6 checkpointを2つの独立policy objectとしてロードし、同一deck/opponent/protocolで `normal` をbaselineにする。
2. `action` と `turn` は同一checkpoint・同一game blockを別runで測る。CABT engineがseedを固定できずpaired evaluation不可であることが既存証拠にあるため、同一seed名だけでpairedと呼ばない。
3. 各blockで fault、seat、opponent、action domain、first divergence、member JS/KL、hidden cosine、reset countを保存する。
4. Wave6 `normal` の3×96-game repeat noise floorを先に測定し、その後reset ablationを24局/cellで接続した。差はnoise floor以下のため長時間評価へ進まない。
5. ensembleを候補扱いするには、Wave6単体・同一checkpoint ensemble・Rule v0を同じ外部poolで比較し、seed／seat悪化、fault 0、shadow-C、320–640外部game gateを満たす必要がある。

## 未解決リスク

- member policyのcheckpoint lineageをDeckLockへ束ねる研究runnerは実装済みだが、ensemble identityを正式production telemetryへ昇格する仕様は未定義。
- semantic logitsの平均は、log-probability平均、temperature校正、action valueを扱わない。これは意図的に最小実装であり、採用案では別途固定する必要がある。
- `turn` は公開state scalarのturnを使うが、CABTのゲーム境界フックではない。runnerがaction観測を正しくsession境界へ渡す必要がある。
- 現在のテストはfixtureと小型実V4だけで、CABT game-level legality／timeout／runtime package telemetryを証明しない。
- 現在のCABT engineには実seed setterがなく、同一checkpointの反復結果にも数ポイント〜十数ポイントのノイズが観測されている。ensemble/reset差の判定はnoise floor後に行う。

## CABT接続後のbounded診断

研究専用 `scripts/measure_v4_research_ensemble_strength.py`（SHA `8c3434f0a336bea2d84ed71a6d83624632e19646f0ad1a429ca2cd1a62018ffc`）を追加し、DeckLock lineageへ束縛したうえでfixed-six 24局/cellを実行した。初回接続ではlineage不一致24 fault、次にsessionの`abort`位置ミスを修正し、最終的に全cell fault0となった。最終結果は別evidence `docs/evidence/v4-research-ensemble-reset-results-20260812.md` と各JSON artifactを正とする。

- Wave6 seed0+seed1 uniform ensemble: 11/24（6/12, 5/12）, fault0
- seed0 duplicate-hidden normal/action/turn: 12/24, 12/24, 12/24, fault0
- seed1 duplicate-hidden normal/action/turn: 15/24, 14/24, 11/24, fault0
- engine seed setterなし、paired不可
- ensemble weight sweep、shadow-C評価、longrunは未実施

## 判定

研究adapterとしての実装可能性とresearch-only接続はGREEN。production変更・長時間学習を開始する根拠にはまだならない。小block結果は別evidenceへ保存し、次はfrozen residualまたはvalue-based residualの一つへ進む。
