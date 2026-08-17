# Autonomous Student v3 Set+Cardinality Evidence — 2026-08-13

## 結論

unordered CABT selectionのoptional decline (`k=0`)、variable cardinality、fixed multiを
一件のdecision = 一件のset targetとしてlosslessに保持するgeneric Student v3経路を追加した。
既存Student v2とproduction/Championは変更していない。V3はshared legal-action scorerと
permutation-invariant count headを持ち、legal-mask付きset BCEと合法count-mask付きCEで学習し、
runtimeはcount argmax後にStable ActionKey digest / option indexでtie-breakしたtop-kだけを返す。

これは実teacher性能またはBestKnown超過の証拠ではない。成果物のpurposeは
`DERIVED_MULTI_TEACHER_THETA0_PRETRAIN_ONLY`、authorityは全てfalseである。

## 重要な入力integrity判定

collector v2 hardening、全6のfresh再収集/reseal、formal catalog再検証が完了した。
正典catalogは次のv2b artifactである。

- path: `runs/final-sprint-autonomous/derived-teacher-catalog-v2b/catalog.json`
- file SHA-256: `8f7c9ea02ea8ec23dcfb35d7d721c81fd0b92db3d31d451157b1396d542443a4`
- semantic SHA-256: `da6c44cc6042d4a2cb955d5429390c9e8955d4cdba8381bb0c361d35b5b1425e`

full6 auditでは36,684 decision中36,680がunordered setとして表現可能、Grimのordered
`5:34`が4件未対応だった。さらにTomato trainとLucifer developmentの間に、declared
ubiquitousではないnear-duplicate IDが1件cross-splitした。このためfull6は正しく
manifest-only NO-GOで、部分datasetを公開していない。

- full6 manifest file SHA-256:
  `0639f01c61cd016a4b8b12cfa5b0f675c07ace4552a19a796048f95e45c85c6f`
- full6 semantic bridge SHA-256:
  `bbd6fc7d7a78fb8dd736908699103551d4cad0a06fc1223c4547db50a05f36dc`
- blockers: `sealed_non_ubiquitous_near_duplicate_split_intersection_present`,
  `unsupported_decisions_present`

Tomato単teacherはordered/unsupported 0、episode/non-ubiquitous leakage 0のためREADYとなった。
V3 bridge v2はrepository-relative catalog pathを追加し、formal catalog verifierからprimary
teacher/snapshot/raw recordへ遡り、生成source bytesを再構築してSHA一致を要求する。

- Tomato bridge manifest file SHA-256:
  `8c026b2ad5eaf9de67a109aaa5393722d4b3c5c05d2813ec9827b6ba42d0c983`
- Tomato semantic bridge SHA-256:
  `3e9cdf0605078f48cb7f1b8bb33dae1023e4e0a74f33afb97f483657896d95b0`
- Tomato source SHA-256:
  `47ae3578b70fab181931fe6bdfa08eae36b5676e1dadc4b5df50f02c893eba9b`
- rows: 5,110（train 3,623 / validation 486 / test 1,001）

formal `verify_teacher_snapshot_student_v3_bridge_manifest_v1`の別実行もPASSした。

## 実装した契約

### Source bridge

- formal catalog loaderの検証済み結果を使用し、bridge独自のcollection status語彙を持たない。
- catalog / decision / teacher manifest / permission bytes / snapshot index / shard / raw record /
  policy / deck SHAをcross-bindする。
- raw record `source.kind`とcatalog `source_kind`、raw source artifactとcatalog policy SHAを
  明示的にcross-bindする。fresh internal teacher recordは`team_internal_agent`へ追従する。
- 1 decision = 1 canonical source row。multi-selectをsingle-target replicaへ展開しない。
- optional decline、variable/fixed unordered setをSUPPORTEDとする。
- ordered selection、selected ActionKey alias、probabilistic targetは件数と理由を残して
  dataset全体をfail-closedにする。
- max=0 forced emptyは`NO_TRAINABLE_CHOICE`として集計し、unsupportedへ偽装しない。
- native teacher code/deckをbundleしない。

### GPU dataset

- source JSONLはcanonical/strict JSON、closed schema、unique canonical `record_id`を要求する。
- actor-visible state、legal ActionKey feature、target-set、target-count、min/maxだけをtensor化する。
- teacher/opponent/seat/record IDはmodel featureへ入れない。
- state/action/offset/target/count tensorのshape、dtype、cardinalityをload時にも再検証する。
- source SHA、catalog SHA、各shard SHA、feature schema、semantic dataset SHA、episode leakage=0を
  manifestへ固定する。
- performance sourceではformal bridge verifierをconsumer側でも実行し、任意のself-consistent
  JSONLがcatalog SHAを自称する経路を閉じる。

実Tomato GPU dataset:

- path: `runs/final-sprint-autonomous/student-v3-set-gpu-dataset-v2-tomato`
- manifest file SHA-256:
  `67bba0f4abb94ec0092473301b7ce2a4f21087ebfe79693e2f41121e8b53d518`
- semantic dataset SHA-256:
  `351459083349917faf3b30384506849be0493de9996a4a4afa043c8f646626b5`
- train shard: `911989b241b15b1e34e98099b78dc9c5f063ee0f5578023dbc8240ccc499acff`
- validation shard: `013f834864438dd86b1291f594dec096f30ca0996c198eca198ebbd0f945a038`
- test shard: `f7b8ad1b760be01b0dd7806e09ec2fb46410b28402c3bbc052e2d7ccc4bebe0a`
- records 3,623 / 486 / 1,001、episodes 68 / 9 / 19、leakage 0

### Model / loss / runtime

- state/action shared encoderと`state + action + state*action` joint trunkを使用する。
- action headはcandidate-wise、count headはmasked mean/max poolingで順序不変である。
- lossはlegal candidateだけのBCEと`min <= k <= max`だけのcount CE。
- runtimeはpromptのcount boundsをmaskし、k=0を明示的に返せる。
- k>0はscore降順、Stable ActionKey digest、option index順でexact legal indicesを返す。
- training bridgeではorderedを未対応としてdataset全体をfail-closedにする。live candidateは
  ordered pointer-head未実装と、公式CABT optionのduplicate Stable ActionKeyという明示的な
  set-head非対応selectionだけをRule v0へ限定fallbackする。
- Rule v0返値もmin/max、重複、option index範囲を再検証し、不正ならfail-closedにする。
  unknown schema、その他のdecision-state/feature障害、model例外、non-finite logits、shape不一致、
  count class不足、checkpoint不整合はfallbackせず例外伝播する。
- candidate policy identityはcheckpoint SHAそのものではない。checkpoint、training summary、
  exact runtime closure source SHA、dataset/bridge/catalog/sidecar/objective/purpose、formal qualified
  deck artifact/file/semantic/deck identityをdomain-separated hashへ束ねる。
- exact runtime closureはfallback実装だけでなく`agents/rule_agent.py`のbytesも含むため、
  Rule v0またはruntime sourceの変更でpolicy identity、game ID、model cache keyが変化する。
- submission-owned root deckはformal qualification
  `b7715b357508961717fd1243386ab39843b97a8327763e491f010e7eafbb9b67`
  とexact deck SHA/identityへbindする。

### Live fallback telemetry

- fallback allowlistは`ordered_selection_requires_pointer_head`と
  `duplicate_stable_actionkey_identity`の2理由だけである。例外を広く捕捉してRule v0へ
  すり替える経路はない。
- 各game schedule metadataへgame ID hash由来のcanonical absolute telemetry pathを固定する。
  workerはgame ID、candidate artifact SHA、policy identity、runtime closure SHA、seat、match status、
  selection/model/fallback count、reason count、全authority falseを閉じたJSONへ保存する。
- telemetry JSONはsemantic self-hash付きで、temporary file + fsync + atomic replace後に再読検証する。
  evaluatorの固定ledger schemaは任意raw fieldを保持しないため、ledger rowにはこのartifact pathを
  metadata bindingとして残し、最終candidate summaryが全game artifactをstrict再検証して集約する。
- path欠落、duplicate game ID、self-hash不一致、candidate/policy/runtime/seat/status不一致、未知reason、
  count不整合はsummaryをfail-closedにする。artifact宣言のない旧synthetic rowは
  `UNAVAILABLE`かつcount `null`で、測定済み0へ偽装しない。

### θ0 / AWR共通trainer

- weight sidecar無しは`THETA0_PRETRAIN`。
- weight sidecar有りは`AWR_FINE_TUNE`。
- sidecarはcanonical `record_id`でevery/only train recordへ完全一致joinする。
- missing / duplicate / extra（非train IDを含む）/ non-finite / non-positive weightはfail-closed。
- validation/test/holdout weightをoptimizerへ使用しない。
- summaryへobjective kind、catalog SHA、dataset manifest SHA、sidecar SHA、external/effective
  weight mass、ESSを保存する。
- checkpointはpurpose、objective、dataset/catalog/sidecar SHA、model/training config SHA、
  epoch、model/optimizer stateを持ち、resume時に完全一致を要求する。

## Synthetic GPU probe

一次artifact:

- `runs/final-sprint-autonomous/student-v3-set-synthetic-v1/probe.json`
- file SHA-256: `9f10adc7ad8c21779b8d3bf214c6eadfbee9421af7434fb42cc2fe01be58dd1c`

環境と結果:

| 項目 | 値 |
|---|---:|
| GPU | NVIDIA RTX PRO 5000 Blackwell |
| torch / CUDA | 2.11.0+cu128 / 12.8 |
| BF16 | supported / 使用 |
| synthetic examples | 6 |
| steps | 80 |
| initial total loss | 1.3361600637435913 |
| final total loss | 8.940706948123989e-07 |
| loss ratio | 6.691344241403482e-07 |
| final set loss | 9.768514122598937e-13 |
| final count loss | 8.940696716308594e-07 |
| exact-set fidelity | 1.0 |
| GPU/CPU decode agreement | 1.0 |

coverageはoptional decline k0、optional accept k1、variable multi k2、fixed single k1、
fixed multi k2である。これはsynthetic tiny-overfitであり、競技性能の根拠ではない。

## V2 catalog status互換修正

formal catalogはcollection stateを`READY`としてclosed validationする一方、旧V2 bridgeは
`SEALED`を独自hardcodeしていた。実catalogを使うfocused testで
`every selected teacher must have a SEALED collection`を再現後、V2 `_selected_teachers`から
status文字列比較を除去し、formal loaderの検証済みteacher setへ委譲した。

同時にraw record source kind/artifactとcatalog teacher kind/policy SHAのcross-bindを追加した。
tomatomato actual snapshot 5,146 decisionsを読むfocused testは修正後PASSした。ただし、旧collector
integrity問題により、このPASSを実6-teacher学習許可として扱わない。

## 検証

実行済み:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q -s \
  tests/test_gpu_student_v3_set_contract.py \
  tests/test_student_v3_set_runtime.py \
  tests/meta_specialist/test_teacher_snapshot_student_v3_bridge_v1.py
```

結果: `14 passed`。内容はdataset lossless変換、ordered NO-GO、permutation invariance、
legal count mask、set/count loss、strict sidecar join、θ0/AWR summary、checkpoint/resume/config SHA、
CPU evaluation、GPU BF16 tiny-overfit/CPU parity、runtime legality/tie、bridge classifier/CLIを含む。

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q -s \
  tests/meta_specialist/test_teacher_snapshot_student_v2_bridge_v1.py -k 'not real_sealed'
```

結果: `7 passed, 1 deselected`。

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q -s \
  tests/meta_specialist/test_teacher_snapshot_student_v2_bridge_v1.py::\
  test_real_sealed_teacher_bridge_audits_all_records_and_refuses_partial_dataset
```

結果: `1 passed in 56.61s`（並行I/O負荷下）。

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/test_student_v3_set_runtime.py \
  tests/meta_specialist/test_run_student_v3_set_candidate_pilot_v1.py \
  tests/meta_specialist/test_teacher_snapshot_student_v3_bridge_v1.py
```

結果: `32 passed in 1.36s`。限定fallbackではorderedとduplicate Stable ActionKeyだけが
Rule v0へ到達すること、Rule v0返値の合法性、unknown/一般DecisionState/model/non-finite errorの
例外伝播、game-bound atomic telemetry、self-hash/candidate root改ざん拒否、summary strict集約、
runtime/Rule v0 source変更によるpolicy identity/game/cache identity変化を含む。formal bridge verifierの
root-of-trust negative test、primary output byte reconstruction、qualified deck exact binding、
candidate builder atomic new/strict reload/CABT非起動も同じsuiteでPASSした。

この検証時点のlive closure SHA-256は
`0c22467b33a8e02f13148843292c028157ad238abcbb4b5f5aab757bef01aff8`、runtime sourceは
`66d5fb61379012fff6850cfd34f3e95b260b4add0881ca5288da455c6f8aff63`、candidate pilot sourceは
`fbe9fbd8a32a0f42a0b4039ae879677ec3abfd08687c5a724c0421e5136ca239`、Rule v0 sourceは
`fe855dffc9592f4957d6afdedf3b2b2fd0a3ad531e442f5ba616ff73f1bb16e6`である。

対象4 Python fileの`py_compile`、対象5 fileの末尾空白scan（該当0）、module CLI help、
`scripts/docs/validate_docs.py`（13 canonical documents）もPASSした。ruff/pyflakesは環境に
未導入のため未実行である。

## API / 成果物

- `mage_ptcg.offline_scaleup.gpu_student_v3_set.build_set_dataset`
- `mage_ptcg.offline_scaleup.gpu_student_v3_set.make_set_cardinality_model`
- `mage_ptcg.offline_scaleup.gpu_student_v3_set.set_cardinality_loss`
- `mage_ptcg.offline_scaleup.gpu_student_v3_set.train_set_student`
- `mage_ptcg.offline_scaleup.gpu_student_v3_set.load_training_weight_sidecar`
- `mage_ptcg.offline_scaleup.gpu_student_v3_set.load_set_checkpoint`
- `mage_ptcg.offline_scaleup.gpu_student_v3_set.evaluate_set_student`
- `mage_ptcg.offline_scaleup.student_v3_set_runtime.StudentV3SetCandidatePolicy`
- `mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1.build_teacher_snapshot_student_v3_bridge_v1`
- `mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1.verify_teacher_snapshot_student_v3_bridge_manifest_v1`
- `scripts.run_student_v3_set_candidate_pilot_v1.build_student_v3_candidate_artifact_v1`
- `scripts/build_teacher_snapshot_student_v3_bridge_v1.py`
- `scripts/run_student_v3_set_synthetic_probe.py`

## 未実施 / 残blocker

- full6 training: ordered 4件とglobal non-ubiquitous cross-split 1件のためNO-GO。
- actual V3 θ0/AWR training: 本境界では未実施。rootが別途制御する。
- candidate artifact: actual model完走後にbuilder CLIで生成する。
- CABT/BestKnown比較/package: 本境界では未実施。

commit、push、remote branch、Champion変更、Kaggle submissionは行っていない。
