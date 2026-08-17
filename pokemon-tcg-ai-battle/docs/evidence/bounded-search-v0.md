---
project: MAGE-PTCG
evidence_type: bounded-search-v0
as_of: 2026-07-15
---

# C3 Bounded Search v0 Evidence

## 結論

C3 Bounded Search v0の探索コア、`EngineAdapter` protocol、guided／unguided評価入口、決定trace、budget／fallback telemetryを実装した。cabtが提示したoptionだけをhard truthとし、Rule Agent v0 rankとC2a Knowledge Packは順序・同値時のsoft priorに限定する。全optionを少なくとも1つの合法responseへ含めるprimitive escapeを保持し、priorによる候補削除は行わない。

ただし、**実cabt paired評価は未実施であり、性能改善も未確認**である。`kaggle-environments==1.32.0`の公開`Environment.clone()`／`step()`は、外部評価器が所有する現在のEnvironmentを複製・前進できる。一方、submissionの`agent(obs)`が単独の観測からそのEnvironmentを再構成する公開契約は確認できなかった。このため実cabt adapterをsubmission runtimeへ追加せず、adapter未指定時はRule Agent v0へ決定的にfallbackする。

| 判定対象 | 状態 |
|---|---|
| bounded search core | 実装済み |
| deterministic fake adapterによる契約評価 | 実施済み |
| 実cabtでbounded searchをpaired評価 | **未実施** |
| Rule Agent v0超過を実cabtで確認 | **未確認** |
| submission runtimeでsearchを有効化 | **無効。Rule v0 fallback** |

## 公開API調査と採用しなかった経路

調査対象はインストール済み`kaggle-environments==1.32.0`の公開メソッド、docstring、`env.specification`、agentへ実際に渡る観測構造、およびKaggleの公開リポジトリに限定した。公開`Environment`には`clone()`、`reset()`、`run()`、`step()`、`train()`があり、`clone()`は現在のEnvironmentのcopy、`step(actions)`は現在stateをactionで前進させる契約である。cabtの公開specificationはactionをoption indexのlistとして定義する。

この公開経路は外部harnessが所有するEnvironmentには使えるが、提出時の`agent(obs)`にはEnvironment handleが渡らない。外部Environmentをclosureで捕捉するadapterはsubmission APIへ移植できず、clone内の対局truthを評価へ混ぜるとhidden-state境界も破るため採用しない。観測に存在するopaqueな`search_begin_input`は、公開specificationに意味、安定性、復元APIが定義されていないため読まず、`DecisionState`、trace、`EngineAdapter`から明示的に除外した。

private binary symbol、非公開復元関数、opaque tokenへ依存するadapterは実装していない。仮に使用すると、version／ABI変更での破損、submission環境との差、hidden-state混入、利用契約不明のリスクがある。代替案は、(1) 公式にdocumentされたagent観測からのforward APIが提供された時点でpublic adapterを追加する、または(2) submission外の許可された評価器で、情報集合境界を検証できるadapterをoffline teacher専用に実装することである。

公開契約の参照先はKaggleの[Environment core](https://github.com/Kaggle/kaggle-environments/blob/master/kaggle_environments/core.py)と[cabt environment](https://github.com/Kaggle/kaggle-environments/tree/master/kaggle_environments/envs/cabt)である。調査時点のruntime versionは`1.32.0`である。

## 探索アルゴリズム

1. Rule Agent v0のselectionを先に計算し、常時保持する。
2. C1 `build_decision_state`でraw観測をactor-visible allowlistへ投影する。合法option setはcabtの`select.option`だけから作る。
3. Rule fallback、optional時のempty response、各optionをanchorにした合法responseを重複除去し、primitive escape setを作る。rootでは全option coverageが1.0でなければ探索を開始しない。
4. guidedはRule v0互換score、次にcompatible Knowledge Packの`prior_score(ActionKey)`、最後にoption indexで安定順序化する。unguidedはoption index順だけを使う。どちらも同じroot response setを使い、priorで削除しない。
5. root responseを全てforwardし、root actorのvalue最大を選ぶ。depth 2では次actorがroot actorならmax、相手ならminとする。engine valueがpriorより常に優先され、同値時だけguided順序を使う。
6. root全候補をcall／expansion budgetへ収容できない場合は1件もforwardせずRule v0へfallbackする。深いnodeも全primitive responseを収容できる場合だけ展開し、収容できなければ親transition valueを保持する。

v0はchance sampling、belief determinization、Replay学習、MCCFR、Student学習を含まない。`EngineTransition.value`はadapterがroot player視点で返す有限値とし、terminalでない場合だけ次のprivacy-safe `DecisionState`を要求する。

## Budget定義

runtime既定値は次である。fake評価CLIは最小1-ply契約を見るため`max_depth=1`だけを上書きする。

| budget | runtime既定 | 動作 |
|---|---:|---|
| `max_depth` | 2 | root transitionをdepth 1と数え、depth 2で打ち切る |
| `max_expansions` | 64 | `EngineAdapter.step`を伴う展開の総上限 |
| `max_engine_calls` | 64 | adapter callの独立上限 |
| `wall_clock_budget_ms` | 20.0 ms | `search_bounded`入口からの総search上限 |
| `hard_deadline_margin_ms` | 1.0 ms | adapterへ渡す実効deadlineを19.0 msに短縮 |
| `primitive_exploration_fraction` | 1.0 | v0では1.0以外をvalidation errorにする |

decision traceは`complete`、`max_depth`、`max_expansions`、`max_engine_calls`、`wall_clock`のbudget reason、call／expansion数、到達depth、coverage、p50／p95集計用latencyを保持する。wall-clock境界を跨いだ場合、部分探索結果は採用せずRule v0へfallbackする。通常完了時の選択とtrace signatureは同じ入力、config、deterministic adapterで一致する。OS schedulingによりtimeout発生有無が変わる境界では、採用結果ではなくRule fallbackへfail closedする。

## Fallback契約と情報境界

次を理由付きでRule Agent v0へ戻す。

- `engine_adapter_unavailable`
- `unknown_selection_type`
- `invalid_observation`
- `insufficient_primitive_budget`
- Knowledge priorの型・有限値違反または例外
- adapterの例外、戻り値契約違反
- wall-clock timeout
- search内部の検出可能な契約例外

traceはraw観測、相手hand／prize、opaque token、logs、actor自身のhand card IDを保存しない。responseの公開ActionKey hash、selection、value、budget／fallback理由だけを固定容量telemetryへ保存する。pack未指定時は`mage_ptcg.knowledge`をimportしない。`main.agent`のdefaultはRule Agent v0のままで、submission artifactのruntime file集合も変更しない。

独立レビューN1に対し、direct callerがnonlegalな`fallback_selection`を渡した場合は`BoundedSearchError("invalid_rule_fallback")`を送出し、非合法な`BoundedSearchResult.selection`を生成しないよう修正した。公開factoryの正常経路は既存のRule Agent v0 selectionを渡すため変更しない。N2はコード修正不要である。

## fake adapter評価

再現コマンドは次である。

```bash
python scripts/evaluate_bounded_search.py --output-dir /tmp/c3-bounded-search-eval
```

5件の決定論的fixtureをRule v0、guided、unguidedへ同じ順序で与えた。これはmatchでもcabt simulatorでもなく、`EngineAdapter`契約、legal response、prior順序、forward value優先を検証するfixtureである。

| condition | decisions | legal | primitive coverage | fixture oracle agreement | calls/decision | fallback | timeout | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Rule v0 | 5 | 100% | 該当なし | 20% | 0.0 | 0% | 0% | 0.011209 | 0.023008 |
| guided | 5 | 100% | 100% | 100% | 2.8 | 0% | 0% | 0.212192 | 0.343321 |
| unguided | 5 | 100% | 100% | 80% | 2.8 | 0% | 0% | 0.200624 | 0.220722 |

selection type別のfixture oracle agreementはguidedがtype 0／1／4／8ですべて100%、unguidedがtype 0で50%、type 1／4／8で100%だった。guided対unguidedのselection差は1/5件で、Rule priorの同値順序差である。C2a packはguided 5/5件で互換・有効だったが、sample priorはRule v0由来の`PLAY` tie-break 1件だけで、このfixtureからKnowledge固有の改善は分離できない。

2回のdecision signatureは一致した。latencyはfake adapterとPython処理だけの局所値であり、実cabt latencyまたは対局性能へ外挿しない。実cabt paired matchesは0、実cabt win rateは未測定、Rule v0超過は未確認である。

最終runの`summary.json` SHA-256は`6dcb5cc64794654adb9fd4a6c160516318290e1d78a23cb44dbc08c782f0d273`、`counterexamples.json`は`c83d643c269798c568e5a295b829c3f400f7376ed77ef8e589acb4cc8e3f150e`、`decisions.jsonl`は`2964eaab4569e018ccbf23fe25bdb870534270d51b1210ba1d525fa9bbb61803`である。latencyを含む前後2ファイルは`/tmp`生成物でありGit管理しない。tracked counterexampleは生成物とbyte一致する。

## Counterexampleとoffline teacher再利用

[counterexample artifact](../../artifacts/search/c3_bounded_search_v0_counterexamples.json)は、Rule／guided／unguidedが異なる5 fixtureを公開selectionだけで記録する。代表例`forward_value_overrides_prior`では、Rule v0は`[0]`、guided／unguidedはfake forward valueが高い`[1]`を選ぶ。これは探索がpriorを上書きできる単体反例であり、cabtで`[1]`が強い証拠ではない。

将来public adapterが得られた場合、同じtrace schemaへ実cabt decision value、fallback、budget exhaustionを保存し、runtime upliftがなければcounterexampleをoffline teacher候補として再利用できる。実cabt artifactとfake artifactは`scope`で分離し、混同しない。

## 検証結果

```text
focused bounded search: 23 passed
focused integration: 113 passed
full pytest: 475 passed, 3 warnings
docs validation: Validated 12 canonical documents.
git diff --check: pass
submission build x2: identical submission.tar.gz
submission content hash: 480b24ecfaa1914fd9af19ac4cfbe5990eb42aaae7508a034abd3493e9076a59
submission tar.gz SHA-256: 25b977a49f386e5259b1f1f6ef0092534ed82f60ce314572571ac8bd9964dcd9
submission verify/clean-room: deck_size=60, mandatory=[0, 1]
submission secret scan: 0 findings
```

## 正典統合時のsubmission runtime差分

`feature/belief-guided-search`への通常mergeではsubmission runtimeの4ファイル中`main.py`だけが変化した。`deck.csv`、`agents/__init__.py`、`agents/rule_agent.py`は統合前後で同一blobである。変化理由はC3のoptional public factoryとそのlazy import境界を`main.py`へ追加したためであり、`_DEFAULT_AGENT = make_rule_agent()`は不変である。solver、fake adapter、Knowledge Packはstandalone artifactへ含めない。

| artifact | 統合前（`8d39a04`） | 統合後（C3 merge） |
|---|---|---|
| content hash | `61243e8e3d210fae16907f95beb0594641669133278099e8f490e8011e58452b` | `480b24ecfaa1914fd9af19ac4cfbe5990eb42aaae7508a034abd3493e9076a59` |
| tar.gz SHA-256 | `2fea32c175598fe071fcb8df6a7f25f9cab1ac196318f3c4f3c073b4c1b25cdd` | `25b977a49f386e5259b1f1f6ef0092534ed82f60ce314572571ac8bd9964dcd9` |

2回buildのtarball byte一致、builder verify、repo外のtarballのみを使う`python3 -I` clean-room、artifact secret scan 0件を統合後にも確認する。生成artifactはGit管理外であり、commitしない。

初回C3受入の検証値はfocused 23、関連integration 113、full 475 passed（warning 3件）である。N1 follow-upはfocused 24、関連integration 114、full 476 passed（warning 3件）であり、初回値を上書きせず別のfollow-up検証として扱う。

## 反証、判断、残リスク

- 前提: cabt option setとC1 projectionが合法性hard truthを保持する。focused testsでsingle、optional、multi-selectionとunknown typeを固定した。
- 強い反証: public `clone`を利用すれば外部Environmentは前進できる。しかしsubmission agentへhandleがなく、raw internal stateを用いるadapterは移植性とhidden-state境界を満たさないためruntime解ではない。
- 判断: coreとoffline teacher入口は保持するが、実cabt public adapterとpaired evidenceが得られるまでsubmission runtimeでは無効にする。ChampionはRule Agent v0のままとする。
- 残リスク: wall-clock timeoutの発生境界はOS schedulingの影響を受ける。結果は部分探索を採用せずRuleへ戻るが、通常完了とtimeout fallbackのどちらになるかまでbitwiseに保証できない。実adapterはdeadline遵守と最悪call時間の検証が必要である。
- 再開条件: submission `agent(obs)`から利用でき、opaque/private stateへ依存せずActorInformationView境界を守る公式forward API、または同等の公開契約が確認されること。
