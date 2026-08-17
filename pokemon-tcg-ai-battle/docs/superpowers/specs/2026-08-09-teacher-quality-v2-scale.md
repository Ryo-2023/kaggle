# Teacher quality v2 scalable authority

## 結論

teacher-quality v1 の fail-closed 方針は維持するが、READY 形式は actual corpus へ使わない。v1 は eligible record ごとに七つの一次証拠ファイルを要求するため、Alakazam 249,299 record と Archaludon 162,925 record では最大 2,885,568 files になり、長時間学習の入力 authority として運用不能である。

v2 は一次証拠を teacher/lane 単位の sealed bundle にまとめ、record ごとの結果は sorted JSONL overlay sidecar へ分離する。Gate と recurrent dataset は overlay を frozen selection index と lockstep で照合し、欠落、余分、順序差、content hash 差、weight 再導出差を optimizer update 前に拒否する。

## Authority topology

1. `teacher-quality-rule-v2.json`
   - caller-owned raw file SHA-256 を必須にする。
   - closed canonical schema、approval identity、固定 threshold、欠損時の扱い、許可 weight setを持つ。
   - 実験結果を見た後の threshold 変更は禁止する。
2. `teacher-quality-primary-bundle-v2.json`
   - lane、teacher ID/revision、policy implementation SHA/version/usage boundary、deck bytes SHA、source permission SHAを固定する。
   - current-pool schedule/pool/engine/source commit、logical game matrix、per-attempt fault provenance、result filesのraw SHAを固定する。
   - confidence/agreement/search strengthが取得不能なら `unavailable` とし、推測値で埋めない。
3. `teacher-quality-overlay-v2.jsonl`
   - `record_id` のbyte順で一意に並べる。
   - 各行は `record_id`, `content_hash`, `teacher_id`, `source_artifact_sha256`, `evidence_class_sha256`, `quality_weight`, `exclusion_reason` のclosed schemaとする。
   - weightはruleとprimary bundleからdeterministicに再導出する。既存record内の `teacher.quality_weight` はauthorityにしない。
4. `teacher-quality-manifest-v2.json`
   - rule、primary bundle、overlayのpath basename、raw file SHA、row count、eligible/excluded counts、weight histogram、manifest self hashを固定する。
   - pathはmanifest directory内のregular fileに限定し、parse前にcaller-owned raw SHAを照合する。

## Recurrent dataset / Gate join

- full-corpus selection index と overlay は `record_id` と `content_hash` で一対一に結ぶ。
- train/validation両partitionを完全coverageし、overlayのextra/missing rowを拒否する。
- `quality_weight=0` は学習対象からsilent skipせず、selection自体をREADYにしない。除外を許可する別selectionを作る場合は、split/componentを再生成して新しいauthorityとして扱う。
- positive weightは `BCExampleV3` へrecordのmaterialize時に注入する。callerが後付けできる既定値 `1.0` はproduction sourceで禁止する。
- preflight receiptはoverlay manifest/file SHA、weight histogram、joined record countを含み、各passでfrozen indexとoverlayを再照合する。

## 現時点の停止条件

- 既存t1 teacher manifestsにはcurrent-pool再評価、per-attempt fault、confidence/agreement/search-strengthの一次artifactがない。
- external teacherはhard selectionのみでpolicy distributionがunavailableである。approved ruleがこの欠損を明示的に許可しない限りweightを正値にしない。
- したがってv2 schemaを実装しても、一次証拠収集と事前承認ruleが揃うまでは `AUTHORITY_GAP` のままであり、actual Gate/θ0/長時間学習を開始しない。

## 実装順

1. chunked primary bundle、sorted overlay、manifestのstrict reader/writerをTDD実装する。
2. 12 logical gameのserial calibration runnerを実装し、p50/p95 attempt timeとfault/retry率を測る。
3. 固定した 2 teachers × 6 opponents × 2 seats × 8 repetitions を収集する。
4. approved rule v2を外部SHAで固定し、overlayを再導出する。
5. recurrent preflightへlockstep joinし、full-corpus Gateを開始する。

