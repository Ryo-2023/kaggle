---
project: MAGE-PTCG
evidence_type: c1-new-implementation
as_of: 2026-07-15
starting_commit: c164bbe6faa7b142616c57049199e81e6fa15025
---

# C1 Public Belief Decision Loop v0 新規実装Evidence

## 結論

C1の最小vertical sliceを、回収不能な旧patchへ依存せず新規実装した。privacy audit、reset／state leakage、Rule v0回帰、全repository suite、clean import、実cabt smoke、400試合batchはすべてpassした。Rule Agent v0をChampionとして維持し、Rule Agent v1はChallengerのままとする。

runtime既定にはpermitted public priorがないため、Rule v1はpublic beliefを明示的な`unknown`へdegradeし、Rule v0と同じ合法手へfallbackする。これは安全なC1 loopの完成を示すが、beliefによる性能改善を示さない。

## 再実装の理由と回収調査

旧報告が示したC1 patchは事前の網羅的な回収調査で発見されず、starting commitにも実装が存在しなかった。このrunは`feature/belief-guided-search`の`c164bbe6faa7b142616c57049199e81e6fa15025`から開始し、既存相当型を検索してから新規実装した。

旧報告のfocused 99 passed、repository 345 passed、cabt 400 matches、Rule v1対Rule v0 105–95は、すべて **UNVERIFIED HISTORICAL REPORT — ORIGINAL ARTIFACT NOT RECOVERED** である。本Evidenceの数値へ流用していない。

## 実装した範囲

- `ActorInformationView`：actorが合法に観測できる公開状態、自身の手札、空のlimited knowledge、公開履歴digest、合法option由来ActionKey snapshotを分離
- `ActionKey`：selection type／context、option type、公開scalar allowlist、source／target keyからcanonical JSONとSHA-256 identityを構成
- `DecisionState`：actor view、元option indexを保持する合法手、公開belief summary、schema／public state／action set digestを保持
- `PublicBelief`：60枚のpermitted deck hypothesis、公開カード包含制約、impossible hypothesisのzero mass、正規化、`unknown` fallback、64件上限の公開履歴、game reset
- `RuleAgentV1`：instance-local C1 loop、DecisionStateから再構成した同一actor viewをRule v0 authorityへ渡し、選択後の合法性再検査、25 ms timeout、deterministic first-legal／Rule v0 fallbackを実行
- lifecycle：`main.py` factory、single match、batch evaluation、trace CLIへ`rule_v1`を登録
- trace：raw observation全体の再帰コピーを廃止し、公開allowlist fieldだけを直接射影

C2a／C2b／C3／C4／C5、SMC、ES-MCCFR、bounded search、Champion変更は実装していない。

## ActorInformationView contractとvisibility basis

| field | 内容 | visibility basis |
|---|---|---|
| `actor` | `current.yourIndex` | acting seatへ明示された公開metadata |
| `public_state_json` | turn、公開board、selection bounds、両者の公開zone、hand/deck/prizeの件数 | cabt actor observationの公開field allowlist |
| `own_private_state_json` | actor自身のhand card ID multiset | actor自身の観測にのみ存在する合法なprivate input |
| `limited_knowledge_json` | 現在は空object | verified reveal contractがないため推測で埋めない |
| `visible_history` | 過去のpublic-state digestだけ | actor private hand／ActionKeyを含まない公開履歴 |
| `action_snapshot` | legal optionの公開scalarから作ったStable ActionKey | cabtがactorへ提示した合法option |
| `remaining_time_ms` | 現在は`null` | `remainingOverageTime`は既存trace方針に従い入力から除外 |
| `digest` | 上記actor-visible contractのcanonical digest | object identity、raw observation、hidden fieldに非依存 |

`public_state_json`内のzone cardは`id`、`serial`、`playerIndex`、`hp`、`maxHp`、`appearThisTurn`だけを保持し、energy／tool／pre-evolutionは件数だけへ縮約する。相手手札は`handCount`、山札は`deckCount`、sideはlist長だけを保持する。自身のsideも内容を保持しない。

## Privacy model

明示的に読まず、保持せず、serializeしないもの：

- 相手hidden hand contents
- 相手deck orderおよびdeck contents
- unrevealed prize contents（両seat）
- `search_begin_input`、`logs`、opaque simulator-private state
- hidden random choice、actorへ提示されないprivate decision
- allowlist外のoption／card payload value

paired negative testでは、相手手札、side内容、engine token、logだけが異なる二状態から完全に同一の`DecisionState`とtrace recordが得られることを確認した。相手handの`get`とside内容のiterationを例外化したguard objectでも構築がpassした。persisted `DecisionState` traceからはactor自身のhand IDも除外する。

## 発見・修正したdefect／integration issue

1. 既存traceは最終出力をallowlist化していたが、その前にraw observation全体を再帰コピーしていた。出力leakはなかったもののhidden payloadへ不要なdata pathがあったため、known keyだけを直接読むprojectionへ修正した。
2. `agents.__init__`からRule v1をeager importすると、Rule v0だけを使うminimal runtimeまで`src/mage_ptcg`へ依存する。Rule v1はfactory内lazy importとし、Rule v0のimport契約を維持した。
3. actor view全体のdigestは自身のprivate handへ依存するため、公開履歴に使用できない。public-state-only digestを分離し、belief historyには後者だけを保存した。
4. repository `.venv`にはPython 3.12.3はあるが、`pip`、`pytest`、`kaggle_environments`は未導入だった。unit/full testはsystem Python、実cabtは固定版`kaggle-environments==1.32.0`を`/tmp/c1-cabt-deps`へ一時導入して実行した。repository依存fileは変更していない。
5. hand sourceの`index`をActionKey identityにすると、同じcardがhand内で移動しただけでkeyが変わる。actor-visible card IDへ正規化し、persisted traceではcard IDとprivate由来digestをredactした。
6. Rule v0本体を変更せずC1共通viewを使わせるため、DecisionStateのselection metadataとActionKeyからRule v0用の最小observationを再構成した。最終選択はこのshared-view adapterを通り、raw observationはdeck registration判定とmalformed fallbackだけに使う。

## テスト結果

### Focused C1

```text
python -m pytest -q tests/test_public_belief_decision_loop.py
24 passed in 0.07s
```

24件にはpaired hidden state、ActionKey決定性／区別／hand reorder安定性、belief正規化／impossible zero mass、malformed fallback、公開履歴、連続game reset、reused／independent instance、alternating seed、trace privacy、illegal-action guard、Rule v0 shared-view adapter／timeout fallback、Rule v0回帰、clean subprocess import、batch registry、exception resetを含む。

### Full repository

```text
env PYTHONPATH=/tmp/c1-cabt-deps python -m pytest -q
366 passed, 3 warnings in 32.24s
```

warning 3件は`kaggle_environments`配下のPydantic deprecated fieldであり、C1 codeからのwarningではない。最終Evidenceはoptional cabt依存を有効化した366 passedを採用する。

### 近接回帰と実simulator smoke

```text
python -m pytest -q tests/test_public_belief_decision_loop.py tests/test_rule_agent.py tests/test_cabt_trace.py
71 passed, 5 skipped in 0.17s

env PYTHONPATH=/tmp/c1-cabt-deps python -m pytest -q \
  tests/test_rule_agent.py::test_official_cabt_rule_agent_smoke \
  tests/test_rule_agent.py::test_official_cabt_rule_vs_rule_smoke \
  tests/test_batch_match_evaluation.py::test_real_cabt_batch_smoke \
  tests/test_first_playable_match.py::test_real_cabt_completes_one_smoke_match \
  tests/test_cabt_trace.py::test_real_cabt_smoke_creates_valid_jsonl_records \
  tests/test_cabt_trace.py::test_real_cabt_smoke_records_satisfy_privacy_invariants \
  tests/test_cabt_trace.py::test_real_cabt_smoke_contains_both_record_types
7 passed, 3 warnings in 2.99s
```

clean subprocess importはfocused test内に加え、次でも確認した。

```text
python -c 'import main; assert main.make_rule_agent().__name__ == "rule_legal_agent"; assert main.make_rule_agent_v1().__name__ == "rule_v1_challenger_agent"'
exit 0
```

## 実cabt batch

debug用20試合を20/20 `DONE`で通した後、次を実行した。

```text
env PYTHONPATH=/tmp/c1-cabt-deps .venv/bin/python scripts/run_batch_eval.py \
  --agent-a rule_v1 --agent-b rule --num-matches 400 --base-seed 7000 \
  --max-steps 10000 --output-dir /tmp/c1-rule-v1-shared-view-400 \
  --save-html none --overwrite
```

- completed：400/400
- Rule v1 wins：205
- Rule v0 wins：195
- draw：0
- `AGENT_ERROR`／`AGENT_INVALID`／`AGENT_TIMEOUT`／`STEP_LIMIT`：各0
- match elapsed total：54.906908秒
- engine seed supported：false

別の実cabt diagnostic smokeではRule v1 seatにdeck registration reset 1回、decision 45回があり、45回すべてで`DecisionState`構築後に`no_permitted_prior` fallbackが選ばれた。最終decisionの内部計測は0.513688 msだった。

205–195はengine seed非対応かつruntime既定でRule v1がRule v0と同じ行動を返す条件の結果であり、性能差、勝率改善、昇格根拠として解釈しない。

## C1 decision path latency

representativeなmain selection（END／ATTACK／PLAY）を使い、warm-up 1,000回後に`RuleAgentV1.choose`全体を20,000回計測した。

| metric | result |
|---|---:|
| sample count | 20,000 |
| median | 0.190890 ms |
| p95 | 0.321805 ms |
| maximum | 3.401830 ms |
| mean | 0.216057 ms |

環境はWSL2 Linux 6.6.87.2、AMD Ryzen 5 9600X（6 core／12 thread）、CPython 3.12.3。synthetic focused benchmarkであり、実cabt observation分布のpercentileではない。

実行したone-linerの主要部は`perf_counter_ns()`で各`agent.choose(obs)`を囲み、昇順sampleの`int(0.95 * n) - 1`をp95とした。raw sampleや一時cabt出力はrepositoryへ保存していない。

## Rule v0 regression

- `agents/rule_agent.py`は変更していない
- valid DecisionStateではRule v0の最終選択をshared actor view adapterから生成
- productive main action、mandatory multi-select、unknown type、hidden payload非読取を含む既存Rule v0 testsがpass
- Rule v1のno-prior／malformed／timeout pathがRule v0と同一選択になることをfocused testで確認
- 実cabt 400試合でillegal／error／timeoutは0

## 前提、代替案、決定、残リスク

- 前提：cabtのagent observationと既存trace allowlistを現在のvisibility boundaryとする。
- 棄却した案：raw observationのdeep copy、module-global belief、未許可Replay prior、SMC／solver、beliefでlegal candidateを削除する実装。
- 決定：C1ではexact public filteringと明示fallbackに限定し、Rule v0の行動authorityを維持する。
- 反証条件：同じactor-visible observationから異なるDecisionStateが生成される、hidden guardへのaccessが発生する、game registration後にhistoryが残る、Rule v1がcabt option範囲外を返す場合はC1完了判定を撤回する。

残る制約：

- runtimeへ接続済みのpermitted public prior sourceはなく、既定Rule v1はbelief scoreで行動を変更しない
- exact composition exclusionは公開zoneでcard IDが表現できるcardに限定され、attached cardは既存allowlistどおり件数のみ
- `remaining_time_ms`は未解決のcabt overage contractを読まず`null`とし、timeoutはlocal elapsed timeで判定
- benchmarkはsynthetic focused inputであり、hardware／loadに依存する
- 400試合はlifecycle／合法性Evidenceで、promotion evaluationではない

## 最終確認command

```text
git diff --check
rg -n '^(<<<<<<<|=======|>>>>>>>)' . --glob '!*.ipynb'
python scripts/docs/validate_docs.py
git status --short
```

これらの最終結果、commit ID、push状態はcommit直前／直後に確認する。

## C1 hardening follow-up（2026-07-15）

Opus reviewの指摘を受け、C1の通常の有効入力経路を変更せず、identity・repr・malformed fallbackを限定的に補強した。

- `ActionKey`はown-hand card IDを補助情報として保持するが、`area` / `index` の意味は未検証であるため、canonical payloadから`index`を除外しない。`area == 2`かつ同一card IDの異なるindexが別ActionKeyになる回帰テストを追加した。
- `ActorInformationView`、`DecisionState`、関連metadata／keyの`repr`は、手札ID、`own_private_state_json`、private-derived `action_set_digest`を出力せず、公開digest・件数・redaction markerだけを示す。
- malformed／partial observationのfallbackもupdate countを単調に進め、公開履歴を変更せず、unknown massを正規化したまま保持する。
- Rule v1の最終deterministic fallbackはRule v0と同じsafe bounds clampを使い、clamp後の最終合法性検証を通過した場合だけ返す。
- `_json_scalar`は`name`属性を持つMapping／non-string Sequenceを除外する。文字列は保持し、bytesはJSON textではないため除外し、enum-like scalarだけ`name`を正規化する。

追加・置換したfocused testsはidentity collision、privacy-safe repr、連続malformed update、malformed bounds、structured scalar guardを対象にし、focused suiteは28 passed、full suiteは363 passed・7 skippedだった。通常の有効入力に対する挙動変更ではないため、既存の400 cabt evaluationは再実行していない。
