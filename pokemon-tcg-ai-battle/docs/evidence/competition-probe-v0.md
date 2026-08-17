---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-07-15
---

# C2b Competition Probe and Raw Archive v0

## 結果

C2bは、公式Kaggle CLIを先に、利用できない場合だけ公式Python clientを次に調べる、read-onlyのCapability Probeとして実装した。2026-07-15のlive probeでは両clientが未導入かつ認証も未検出であり、根拠付きで`LOCAL_ONLY`へfail-closedに分類した。Competition dataの取得不能はC3／C4／C5および提出critical pathをblockしない。

## Baseと実装範囲

| 項目 | 値 |
|---|---|
| source branch | `feature/competition-probe-v0` |
| source commit | `a8540e14e3ef9b86e502488d8529a0f0426c23d3` |
| base branch | `feature/belief-guided-search` |
| base HEAD | `2f64fa13ac29a096c4802668ae467ccb7211b1bb` |
| live probe時のsource HEAD | `2f64fa13ac29a096c4802668ae467ccb7211b1bb` |
| final HEAD | このEvidenceを含むC2b作業commit（完了報告の`git rev-parse HEAD`で確定） |
| 実装 | `src/mage_ptcg/competition/`、`scripts/probe_competition.py`、C2b focused tests |

Claude Code指定worktree `/home/onoryosuke/kaggle/pokemon-tcg-ai-battle-cabt-trace` は存在しなかった。baseには既に`db8beb8`（同一内容のremote branch commitは`c88f9d5`）のcabt traceが含まれていたため、C2bではそのcommit／コードをcherry-pick・再実装しなかった。Traceはagent観測のprivacy-safe記録、C2bはcompetition remote capabilityとraw archiveであり、責務を混同していない。

## Probe schemaとmode判定

各actionの`summary.json`はschema/probe version、開始・完了時刻、source HEAD、official action／command、client・Python・Kaggle package versions、安全なauth source種別、requested capability、結果コード、content type／size／SHA-256、sanitized error、retryability、検出したreplay／legal-option field、schema fingerprint、redaction versionを記録する。credential値、header、cookie、token、signed URLのsecret部は記録しない。

`classify_mode`はpure functionであり、次の順に低い能力側へ閉じる。

| 条件 | mode |
|---|---|
| replay bytes、state/action progression、legal option field、schema fingerprintがすべて確認済み | `FULL_REPLAY` |
| replay bytesとstate/action progressionは確認済みだが、完全legal option集合を証明できない | `REPLAY_WITHOUT_LEGAL_OPTIONS` |
| metadata、public files、leaderboard、submissionのいずれかを取得済み | `PUBLIC_ARTIFACTS_ONLY` |
| 上記のremote capabilityを証明できない | `LOCAL_ONLY` |

## Archive、fingerprint、redaction

probeごとに次のtreeをatomic renameでpublishする。既存probe IDは明示的な`--force`なしには上書きしない。path traversalとsymlink output rootは拒否する。

```text
artifacts/competition/probes/<probe-id>/
├── manifest.json
├── summary.json
├── schema-fingerprint.json
├── raw/{response.bin,metadata.json}
├── redacted/response.json
├── errors/error.json
└── quarantine/response.bin      # secret scanがrawをunsafeと判定した場合だけ
```

全action後には`<probe-prefix>-report/`も同じtreeで生成し、その`summary.json`に最終`classified_mode`と判定根拠を置く。

archive rootは`.gitignore`対象である。raw bytesはSHA-256で同一性を表し、JSON fingerprintは値を保持せず、object key path、型、list要素のunique shape、field countをcanonical JSON化してSHA-256を取る。順序、scalar値、同一shapeのlist反復はfingerprintを変えない。

redactionは派生物のみへ適用し、API key、token、Authorization、cookie、password、signed URL query、email、home pathを除去する。rawでsecret様値を検出した場合はraw treeに置かずquarantineへ隔離し、redacted artifactを再scanしてからpublishする。

## 実行とlive結果

```bash
python scripts/probe_competition.py \
  --competition pokemon-tcg-ai-battle \
  --output-dir artifacts/competition/probes \
  --probe-id-prefix live-20260715-v3 \
  --json-summary
```

終了コードは`2`だった。実測では`authentication_detected=false`、Kaggle CLI未導入、公式Python client未導入、`kaggle==None`、`kaggle-environments==None`だった。metadata、public files、leaderboard、submissions、replayはそれぞれ`dependency_missing`の構造化errorとして保存された。分類は`LOCAL_ONLY`、根拠は`no_remote_competition_capability_proven`である。artifact pathは`artifacts/competition/probes/live-20260715-v3-*`、各zero-byte error response hashは`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`である。

この結果はこの環境での取得可否だけを表す。Kaggle CLI/clientと正当な認証を用意した別環境では、同じCLIを再実行してmodeを再確認する。Replayを取得できないためdummy replayは作成していない。

## 検証

| 検証 | 結果 |
|---|---|
| focused C2b tests | `python -m pytest -q tests/test_competition_fingerprint.py tests/test_competition_probe.py tests/test_competition_probe_archive.py tests/test_competition_probe_redaction.py tests/test_competition_probe_cli.py`（38 pass） |
| repository全体tests | 408 collected。分割実行合計で401 passed、7 skipped（focused 38、既存top-level 110／7 skipped、unit 38、orchestration 215） |
| docs validator | `python scripts/docs/validate_docs.py` → `Validated 12 canonical documents.` |
| `git diff --check` | pass |
| secret scan | C2b testsのraw/redacted fixture検証と、実装・文書差分のcredential pattern scanでpass |

残る制約は、live環境に公式Kaggle package／CLIも認証もないため、実competitionのmetadata・public artifacts・episode/replay schemaを観測できていないことである。これはC2bの`LOCAL_ONLY`結論であり、C3／C4／C5の開始条件ではない。
