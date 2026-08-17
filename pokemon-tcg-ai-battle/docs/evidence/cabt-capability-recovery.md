---
project: MAGE-PTCG
slice: C5
status: ready
as_of: 2026-07-16
---

# cabt Capability Recovery v0

## 結論

official cabtは`kaggle-environments==1.32.0`に同梱・登録されている。従来の`CAPABILITY_UNAVAILABLE`の原因分類は`COMBINED_ENVIRONMENT_AND_PROJECT_GAP`である。環境側では、system Pythonに`kaggle-environments`がなく、ROS由来の`PYTHONPATH` overlayも混入し得た。一方、canonical baseにはactual capabilityを`make("cabt")`で測るprobe、real cabtの`run_match`を`run_actual_league`へ接続するcaller、actual League実行CLI、environment／trace provenance manifestがなかった。隔離`.venv`の既存固定versionとこの評価統合を追加後、capability reportは`READY`となり、official loaderで1-game traceと20-game League smokeを実行した。

これはsubmission runtimeのbugではなく、official cabtを用いる評価のintegration gapである。submission default、Champion、promotionの決定は変更していない。

Champion、submission defaultはRule Agent v0のまま、promotionは`NO_DECISION`のままである。実行環境の復旧はagent昇格の根拠ではない。

## 再現可能な環境

| 項目 | 値 |
|---|---|
| canonical base | `f88682c2833cd13710e6a9f629dd76eb522937de` |
| Python | 3.12.3 |
| system Python | `kaggle-environments`未導入 |
| required package | `kaggle-environments==1.32.0` |
| active package path | `<site-packages>/kaggle_environments` |
| `.venv` | required packageとcabt pluginを含む |
| requested environment | `cabt` |
| registry | 58 environments、`cabt`を含む |
| external asset | 不要（PyPI package内のpluginで`make("cabt")`成功） |

```bash
python -m venv .venv
env -u PYTHONPATH .venv/bin/python -m pip install kaggle-environments==1.32.0
env -u PYTHONPATH .venv/bin/python scripts/cabt_capability.py --output /tmp/cabt-capability.json
```

hostのROS `PYTHONPATH` overlayは無関係なpackageを混入させるため、actual cabtの正規実行導線は`env -u PYTHONPATH .venv/bin/python ...`である。この状態で`pip check`はpassする。`python scripts/cabt_capability.py`はactive interpreterだけを検査し、未導入なら`PACKAGE_NOT_INSTALLED`でfail closedする。reportはprivate path、例外本文、secretを保存しない。

## Capability diagnostic

`/tmp/c5-cabt-capability.json`（SHA-256: `5656ad1ef85d8382d566c14008826a51cb8c2a9ad2ae04edd8abed1c5c35adc0`）:

- status: `READY`
- reason code: `READY`
- package version: `1.32.0`
- plugin/asset path: `null`（外部path未使用）
- actual execution allowed: `true`

診断CLIは`PACKAGE_NOT_INSTALLED`、`PACKAGE_IMPORT_FAILED`、`VERSION_INCOMPATIBLE`、`PLUGIN_NOT_REGISTERED`、`COMPETITION_ASSET_MISSING`を区別する。package import時のthird-party stdout/stderrはreportへ混入させない。

## Actual Gate 1 — trace probe

```bash
env -u PYTHONPATH .venv/bin/python scripts/cabt_trace.py \
  --matches 1 --agent-a rule --agent-b deterministic --base-seed 0 \
  --output /tmp/c5-attestation-actual-clean.jsonl \
  --manifest-output /tmp/c5-attestation-actual-clean.manifest.json
```

| 項目 | 結果 |
|---|---|
| status | PASS / actual |
| games | 1 |
| invalid / crashes / timeouts | 0 / 0 / 0 |
| fallback | NOT_RECORDED（trace schemaにcounterなし） |
| privacy violations | 0（forbidden key scan） |
| public trace records | 79（保持済みoriginal runを再計数） |
| trace SHA-256 | `62dc1ef14158f470e6c193784c7d555a633bbe08bee710369eed91c8b6a38480` |
| manifest SHA-256 | `f274a9f7add965175c0822cb2584c60d0f651e5558faf2279200925fbefd93ca` |
| config SHA-256 | `562238e40d841cefc14615e6e8b68a56aae233df1726e71e173af73a87b22d72` |
| provenance | `kaggle_environments.make` / `cabt` / package `1.32.0` / commit `f88682c…` |
| TR-000010 applications | 0（binderはsubmission／trace defaultへ未接続） |

manifestはtraceと別ファイルで、loader、environment identity、commit、config hash、trace hashのみを保存する。public traceにcandidate card identity、own-hand identity、binding resultは保存しない。private binding artifactはこのprobeでは生成していないため、public traceとの混在はない。

このGate 1の原本は`/tmp/c5-attestation-actual-clean.jsonl`であり、`wc -l`は79、SHA-256は上表の`62dc1ef…`だった。対応manifestの`trace_sha256`も同じ値である。trace record countはrun-specificであり、`engine_seed_supported=False`のcabt engineでは、同一config hashでもrecord countが変動し得る。したがってconfig hash一致はゲーム展開の完全再現を意味しない。独立レビューの別runで得られた32 recordsは、このoriginal runの79 recordsへ置き換えない。

## Actual Gate 2 — 20-game side-swap smoke

```bash
env -u PYTHONPATH .venv/bin/python scripts/run_actual_league.py \
  --games 20 --base-seed 0 --challenger deterministic \
  --output /tmp/c5-cabt-actual-league-20.json
```

| 項目 | 結果 |
|---|---|
| status | PASS / actual |
| games | 20 |
| W-L-D（Rule Agent v0） | 16-4-0 |
| champion player 0 | 8-2-0 |
| champion player 1 | 8-2-0 |
| invalid / crashes / timeouts | 0 / 0 / 0 |
| fallback | 0 by construction for this Rule v0 smoke path |
| privacy violations | 0 |
| latency p50 / p95 / max seconds | 0.092626 / 0.308714 / 0.500293 |
| config hash | `b7599b9426a659f34166896810fd12804d894f8205bdf96dcc9e444729866548` |
| artifact hash | `b5cbce3d593d2a866a825eb6b50f0cc58d166cd1d8d7522ce9a23e80732548ee` |
| resume | PASS（completed match再利用後もartifact hash不変） |
| trace records | NOT_CAPTURED_BY_LEAGUE_RUNNER（Gate 1の保持済みoriginal runは79件） |
| TR-000010 applications | 0（binder default無効） |

League callbackはstatus、winner、latency、fallbackだけをartifactへ許可し、raw observation等のcallback-only fieldを破棄する。これはfixtureでなく`kaggle_environments.make("cabt")`を各gameで新規にロードしたactual runである。

この20-game結果は、同一`deck.csv`のmirror setupでRule Agent v0とdeterministic baselineを比較し、各seat 10 gamesずつside swapした**pipeline smoke result**である。actual cabt full episode、League wiring、side swap、20 gamesの完走（invalid／crash／timeoutなし）、privacy-safe public artifactの生成を確認する。一方で、C3 Bounded Search、C4 Student、C5 teacher bindingの性能向上、Rule v0の統計的優位、engine randomnessの完全再現性、Promotion条件の成立は証明しない。

`fallback: 0`はこのRule v0 smoke pathでcallbackが返す値によるby-constructionの値であり、Student／Search／Teacher agentのmeasured fallback counterではない。C3／C4／C5のruntime安全性evidenceとして扱わない。

resume determinism / idempotencyは、既存artifact内のcompleted matchを再利用して重複実行せず、resume後もartifact hashが変化しなかったことを指す。engine randomnessを含むgame resultの完全再現性を証明しない。seed scheduleとseat scheduleは決定的だが、cabt engine randomnessは未seed／非決定的であり、W-L-D、latency、trace record countは再実行で変動し得る。artifactの`reproducible` fieldは`len(records) == games`を表すcompleteness flagであり、統計的・乱数的再現性を意味しない。

## 残リスクと次の行動

- package importはOpenSpiel等のoptional environment warningをstdout/stderrへ出す。capability JSONは抑制するが、third-party console warning自体はpackage更新時に再確認する。
- Gate 2はpublic decision traceを各gameには取得していない。League artifactは意図どおりmatch-level metadataだけである。
- 現時点の実測はRule Agent v0対deterministicの20 gamesだけで、promotion判断に必要な比較evidenceではない。
- pre-existing follow-up：broader batch toolingの利用前に、`scripts/test_sim.py`と`scripts/run_batch_eval.py`の`terminal_reason` exception textをsanitizeする。この経路は新しいofficial League callbackでは到達しないため、今回のrecovery integrationのblockerではない。今回このコードは変更しない。

次の正確なコマンドは、別条件のactual Leagueを実行する前に以下でcapabilityを再確認すること。

```bash
env -u PYTHONPATH .venv/bin/python scripts/cabt_capability.py --output /tmp/cabt-capability.json
```
