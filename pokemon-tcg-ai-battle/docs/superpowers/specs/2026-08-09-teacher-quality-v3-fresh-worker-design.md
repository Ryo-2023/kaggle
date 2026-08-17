# Teacher-quality v3 fresh-worker execution design

## 決定

teacher-vs-Rule v0 のprimary evidenceは、各logical-game attemptを独立したspawn workerで
実行する。親collector内でexternal teacher policyをimportしない。workerはimport前に
Python `random`、NumPy、Torchのglobal RNGを `agent_sampling_seed` へ固定し、worker process
をattemptごとに終了する。これはexternal policyに明示的なseed引数が無い現状でも、通常の
process global RNGを実際に初期化し、policy module global stateをattempt間で共有しないためである。

engineがenvironment seedを実際に受け取るかは別契約とする。CABTが未attestedなら、ledgerは
`environment_seed`を実行済みengine seedと主張せず、`engine_randomness: unattested`をsealed
metadataへ残す。OS entropy、network、wall-clockを使うpolicyをdeterministicと主張しない。

## 比較した方式

1. 親processでmodule cacheをunloadしRNGだけresetする方式は、policyのimport副作用、C拡張、
   hidden singletonを完全に消せないため不採用。
2. clean Git worktreeを必須にしてそのままsubprocessを起動する方式は、現在の統合作業の未commit
   sourceを実行できず、依存engine closureも曖昧なため不採用。
3. **採用: sealed source snapshot + fresh spawned worker。** 実行に必要なrepo source、selected
   teacher/panel policy・deck、engine module closureをprivate staging rootへraw bytesでcopyし、
   file manifestとtree digestを固定する。workerはsnapshotだけをimport rootとし、開始時にmanifest
   と自身のinput bytesを検証する。実行内容とplanのHEADを混同しない。

## Source snapshot

- parent preflightはO_NOFOLLOWのsingle FDで各source fileをreadし、pre/post
  `dev/ino/mode/size/mtime/ctime` とEOF SHA-256を確認する。
- private staging rootへ0600/0700権限でraw bytesを書き、file path・SHA・sizeをcanonical manifestへ
  記録する。完了後にmanifestとtree digestをatomic publishする。symlink、absolute path、`..`、
  manifest外fileを拒否する。
- snapshot範囲はcollector/worker、`src/mage_ptcg`、`scripts/test_sim.py`、`main.py`、selected
  teacher/panel policy/deck、engine module originとそのpackage treeである。workerの実行前に全entryを
  再hashし、source pathを元worktreeへfallbackしない。
- source snapshotはperformance resultとmanifestにraw SHAでbindし、次のattemptでも同一digestを
  要求する。staging失敗・hash drift・missing engine closureはfaultでなくcampaign authority buildを拒否する。

## Worker protocol

1. parentはcanonical request JSONをworkerへ渡す。requestはcampaign/logical-game/attempt identity、
   source snapshot digest、policy/deck relative paths、subject seat、`agent_sampling_seed`、
   engine-seed capabilityを含む。
2. workerはsnapshot manifestをsingle-FDで検証してから、`random.seed`、利用可能なら
   `numpy.random.seed` と `torch.manual_seed`/`torch.cuda.manual_seed_all` を設定し、設定済みlibrary
   とseedをcanonical execution provenanceに記録する。
3. workerはsnapshotからのみengine、teacher、opponent、Rule v0をimportし、1 gameを実行する。
   標準出力はcanonical result JSON 1件のみ、例外はclass/message/traceback SHA/exit codeを返す。
4. parentはworker resultのrequest identity、source digest、RNG provenance、result schemaを検証して
   attempt ledgerへatomic追加する。timeout、nonzero exit、malformed JSON、unexpected stdout、worker
   source driftはfault rowとして保存する。retryは同じlogical-game idとseedで最大1回である。

`agent_sampling_seed`は「process global Python/NumPy/Torch RNGをimport前に初期化したseed」と定義する。
engineがseedを受けない場合のenvironment randomnessは`unattested`であり、paired replayやbitwise
determinismを主張しない。

## Source/output integrity

- output rootは排他lockを保持する。campaign/ledger/result/manifestはcanonical JSONまたはJSONLでatomic
  publishし、再開時はclosed schema、order、request/result identity、snapshot digestを再検証する。
- calibrationは `strata_complete=false`、`CALIBRATION_ONLY`。fullだけが12 strata×2 arms×8 repetitionsを
  全検証してperformance evidenceとなる。
- `smoke_ok=false` subjectはcalibration diagnosticも含めproduction evidenceではfail-closedとする。
  subjectを修復・smoke passして別のfreezeしたcampaignを作るまでfull評価しない。

## テストと完了条件

- RED: parent module globalを汚してもfresh workerのpolicy stateへ漏れない、RNG値がimport前に設定される、
  snapshot外import/symlink/hash/TOCTOUを拒否、worker stdout/identity改竄をfault化、dirty original
  worktreeをworkerが読まない。
- RED: engine seed未対応ならresult/manifestが`unattested`以外を記録できない。attempt retry、output
  concurrent lock、calibration false completionも維持する。
- GREEN: sealed fixtureでteacher/Rule双方をseat 0/1へ結線し、同一request seedでfresh workersの
  provenanceが一致する。full resultは384 logical games、最大768 attempts、logical-game bootstrapを
  再導出する。
- 実host calibrationは独立レビュー後、両subjectのsmoke pass、source snapshot preflight、single
  batch commandが揃った時だけ行う。calibrationはtrust/weight/theta0を解放しない。

## 非対象

- v2 trust setへのdigest追加、teacher weight付与、theta0生成、full 384-game実行、Kaggle提出は本設計の
  完了だけでは行わない。
- hostile codeがworker自身のverifierを動的に改竄するケースは、同一process内のtrust boundaryではなく
  snapshot/OS process boundary外の攻撃として扱う。worker executable・snapshot manifest・raw source
  hashesを親が検証する。
