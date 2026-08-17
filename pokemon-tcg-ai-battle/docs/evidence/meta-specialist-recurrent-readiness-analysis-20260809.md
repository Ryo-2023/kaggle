# Meta Specialist recurrent readiness 現状分析・今後の作業方針（2026-08-09）

## 2026-08-09 追補（full-corpus 実測後）

本節は、以下に残る当初時点の記述を新しい実測で上書きする。旧v1 preflight receipt は selection/split/hash の診断証拠としては読めるが、R3 projection を検証していないため、production authority として使わない。

- 実laneの旧v1 preflightは完走した。Alakazamは2,475.9秒、peak RSS 596,152 KiB、Archaludonは849.9秒、peak RSS 598,724 KiBだった。
- 外部host実行では RTX PRO 5000 Blackwell、PyTorch 2.11.0+cu128、CUDA availableを確認した。2 updateのfixture-only CUDA smokeではparameter delta `4312.604275748134`、peak `27,712,000` bytesを確認したが、これは配線証拠であり性能証拠ではない。
- Archaludon 162,925 recordsの旧v3 full-corpus projection censusを完走した。成功91,406、失敗71,519、失敗率43.8969%だった。失敗内訳は `ambiguous_public_locator` 69,707、`selectable endpoint is not uniquely public` 1,812である。
- census artifactは `runs/meta-specialist-two-lane-readiness/actual-preflight/archaludon/r3-projection-census-v1.json`、raw file SHA-256は `7a562dbca2b5d50f2fa894a37e9ebd91ef304730b2d03d57bf9c30483cc4c27f`、内部result SHA-256は `11b01d5005da56314ed6a78a700c89c043b061d649f4ebe7ad6beaf40a669272` である。
- これは少数の不良recordではない。v1 semantic actionが意図的に物理serialを捨て、`allowed_alias_count`だけを保持する一方、旧v3が単一のpublic locatorを復元しようとした設計矛盾である。失敗recordを除外して学習量を減らす修正は採用しない。
- serial-freeなactor-visible equivalence classを使うrepresentation/model v4へ移行中である。初版の公開feature欠落4件は反例テストで修正されたが、checkpoint外部anchor、partial state拒否、categorical integer collisionの再修正と独立レビューが残る。
- recurrent preflightは新規writeをreceipt v3へ上げ、全record projection後だけauthorityをpublishし、sealed snapshotをpreflight時に一度だけ固定する。root/shard/indexのsymlink反例はstream開始前に拒否する。旧v1/v2 receiptはproduction streamで拒否する。
- teacher-quality v2は実result ledger再導出と412,224行overlayのbounded-memory streamingまで実装した。actual-scale合成計測は約20.45秒、peak RSS 31,908 KiB。ただし承認済みrule digestはまだ存在せず、production trust setは空なので必ず`AUTHORITY_GAP`である。recurrent dataset/Gateへのrecord単位lockstep joinも未実装で、既定`quality_weight=1.0`は引き続きblockerである。

従って現在のcritical pathは、(1) v4の残存反例修正、(2) v4 full-corpus receipt/materializer、(3) teacher-quality overlayのrecord/content hash完全join、(4) actual primary-evidence calibration、(5) lane-fused CUDA Gate、の順である。旧v3 actual Gateや長時間学習は開始しない。

## 結論

現時点では、長時間学習の前提となるデータ完全性、episode単位の再帰学習、hidden carry/reset比較、成果物の外部SHA固定、fail-closedなGateの実装はかなり整った。一方、R3 recurrentモデルの実full-corpus学習結果、CUDA上の12-cell Gate、formal θ0、teacher quality再導出、短期multi-seed learner pilot、独立対戦評価はまだ存在しない。

したがって現在の判定は、**実装・データ基盤は次の実測へ進めるが、長時間学習はまだ開始しない**である。性能向上の見込みは「可能性はあるが未証明」であり、「妥当に上がる」と判断できるのは、recurrent Gate、sealed θ0、短期multi-seed pilot、独立層化対戦評価を順に通過した後である。

## 目標の理解

最終目標は、単に学習コードを動かすことではない。AlakazamとArchaludonの2 laneについて、データ漏洩、再開不能、fault監査欠落、criticの誤校正、単一相手への過適合を閉じたうえで、複数seedにわたり改善傾向があり、独立評価でもcurrent baselineより正方向だと確認してから長時間学習を開始することである。

current-R2は安全な静的baselineとして維持する。temporal hidden stateを持たないため、recurrent θ0と称して流用しない。recurrent候補は既存GRUを持つR3-A/R3-Bを別Gateで比較する。Gate不通過時はR2を偽装昇格せず、長時間学習を停止する。commit、push、Kaggle提出は本作業の範囲外である。

## 現在までに確定した事実

### 元の重大障害

当初残っていた次の2件は修正され、2026-08-09の個別回帰テストで `2 passed` を確認した。

- legacy v1 recordはread/resume互換のみを残し、新規writeでは拒否する。
- persistent timeout後のspawn fallbackで、各attemptの実exit codeを保持し、`None`で上書きしない。

これにより、少なくとも当該2経路については長時間runのresume/fault provenanceを壊す既知障害が閉じた。

### 静的Gate 1

CPU/CUDAの静的Gate成果物はいずれも次の閉じた判断を保存している。

- status: `BASELINE_RETAINED`
- decision: `BASELINE_RETAINED_R3_UNAPPROVED`
- active/preferred: `current-R2`
- R3 promotion: `UNAPPROVED`

CUDA成果物にも18 cellの実測はあるが、v2 major-regression threshold未規定と、Alakazam/Archaludon双方のordered target coverage不在がblockerとして残る。したがって静的GateはR3採用根拠ではない。

consumerは外部anchorを持つ `gate1-selection-v3-cpu.json` または `gate1-selection-v3-cuda-0.json` を入口にする。併存する旧 `gate1-result-v3.json` はstale artifactであり、判断根拠に使わない。

### full-corpus recurrent selection

Gate 1の32 record sliceやfirst-Nではなく、全sealed corpusをstreamしてepisode/near-duplicate component splitしたmanifestが2 laneとも完成している。

| lane | records | train | validation | components | episode overlap | near-duplicate overlap | manifest file SHA-256 |
|---|---:|---:|---:|---:|---:|---:|---|
| Alakazam | 249,299 | 199,414 | 49,885 | 2,989 | 0 | 0 | `8093116b9071847cc17ed0f742bf6000697646386dbcc410d924e145d021bc7e` |
| Archaludon | 162,925 | 130,335 | 32,590 | 2,914 | 0 | 0 | `b3044504df1192ce072377f1ddfbeeafdf071a715ef896076b5adb1471eaf0cc` |

selection index SHA-256はAlakazamが `563022ef6a38c82faacd0fbff889c3fad474a967007f23f8be67af5365b02732`、Archaludonが `24c012255a99c2c44de925063b8cb7b80d497550f4df9ed14e75482f02133f97` である。

### recurrent実行基盤

次の契約が実装・テスト・独立レビューされている。

- 外部manifest file SHAをJSON parse前に照合する。
- 同一episode内だけでsequenceを作り、episode boundary reset、padding、burn-inを保持する。
- 全corpusのshard bytes/count、teacher permission、qualification、record identity、component/partitionを再検証する。
- A→B→Aのような物理stream上のepisode再出現を拒否する。
- R3-onlyでsemantic+STOPのcanonical soft targetを学習し、forced sole STOPをlossから除外する。
- hidden carryとstepごとのreset-only ablationを同一checkpointで比較する。
- train/validationのcomponent/episode overlap、optimizer未更新、parameter delta 0、非有限metricをfail-closedにする。
- production runでは任意factoryや全corpus tupleを許さず、sealed stream adapterだけを使う。
- expensiveなcomponent split再構築はlane/job-startのpreflightで一度だけ行い、run-local frozen indexとreceiptへ固定する。各passではraw corpusをlockstep再qualificationする。
- receipt、manifest、raw shard、frozen indexは同一FDとEOF digestで検証し、symlink追従を許さない。`O_NOFOLLOW`非対応環境はdowngradeせず拒否する。

Task 3.6の最終独立再レビューでは新規Critical/Importantなし、関連回帰は `55 passed` だった。ただし実laneのpreflight/scale計測はまだ実行していない。

### recurrent 12-cell Gate

2 lane × R3-A/R3-B × seeds `(7, 17, 29)` の12 cellを同一sealed data/split/budgetで比較するrunnerとstrict artifact readerは実装済みである。選択規則は事前固定されている。

- laneごとの3-seed平均で、carry complete-action NLLとcarry STOP NLLがreset-onlyより `+0.02`を超えて悪化しない。
- 少なくとも1 laneでcompleteまたはSTOP NLLが `0.01`以上改善する。
- R3-BはR3-Aよりmacro complete NLLが `0.01`以上、またはSTOP NLLが `0.02`以上良い場合だけ選ぶ。それ以外は小さいR3-Aを選ぶ。
- actual optimizer update、positive parameter delta、non-reset hidden steps、early-stop/best checkpoint、STOP evidenceを必須とする。
- CUDAでは全12 cellにdevice名、実測フラグ、正のpeak memoryを要求する。CPU結果は `RESEARCH_ONLY` でpromotion authorityを持たない。CUDA失敗時にCPUへ偽装fallbackしない。

このGateコードは独立再レビューで新規Critical/Importantなし、対象テスト `21 passed` だった。ただし**実データを用いたactual 12-cell CPU/CUDA Gateは未実行**であり、recurrent性能結果はまだない。

## これまでの実験結果

### 旧bounded representation比較

4 lane各128 records、seed 7、3 epochsの旧比較では次の結果だった。

| lane | R2 NLL | R3-A NLL | R3-B NLL | 解釈 |
|---|---:|---:|---:|---|
| Alakazam | 1.820462 | 1.852043 | 1.864900 | R3悪化 |
| Archaludon | 1.517733 | 1.482217 | 1.476455 | R3は僅かに改善、top-1は低下 |
| Grimmsnarl | 1.948337 | 2.020154 | 2.075685 | R3悪化 |
| Rocket | 2.079650 | 2.312241 | 2.367937 | R3悪化 |

R3 latencyはR2のおおむね3倍以上だった。この実験は少数slice・1 seedで、recurrent carryを評価していないため、採用根拠にはならない。

### 新しい静的2-lane × 3-seed比較

CUDA静的Gateの18 cellをlane/candidateで平均すると次のとおりである。

| lane | candidate | mean complete-action NLL | mean top-1 | mean p95 ms |
|---|---|---:|---:|---:|
| Alakazam | current-R2 | 0.538158 | 0.722 | 8.09 |
| Alakazam | R3-A | 0.240888 | 0.889 | 41.85 |
| Alakazam | R3-B | 0.255291 | 0.889 | 41.59 |
| Archaludon | current-R2 | 1.308654 | 0.542 | 11.99 |
| Archaludon | R3-A | 1.449859 | 0.292 | 31.62 |
| Archaludon | R3-B | 1.458539 | 0.333 | 31.27 |

AlakazamではR3が大幅に良く、ArchaludonではR2が良いというlane差が出た。またR3のp95 latencyは約2.6〜5.2倍である。ただしvalidationはlaneごと6 recordsのsealed sliceで、ordered nonempty-prefix coverageが実corpusにない。静的Gate自身もR3を未承認としているため、この数値をfull-corpus性能やrecurrent性能へ外挿しない。

### critic

toy warm-upではuniform Brier `0.666667`から `0.665898`への僅かな改善に留まった。stable opponent categoryを使うC1 toy ablationは設計上の妥当性を示したが、実laneの勝率校正は証明していない。

過去の14-round V-traceではcriticが全laneで楽観的だった。平均 `V-ret` はArchaludon `+0.209`、Grimmsnarl `+0.182`、Alakazam `+0.296`、Rocket `+0.277` である。重要度重みの上側clipも約0.24〜0.33に達していた。

### 過去のV-trace長時間run

旧14-round runは、500 games × 4 lanesの収集、V-trace 80 steps、評価24 gamesを1 roundとして実行したが、14 round後の96-game評価は全4 laneでθ0より低下した。

| lane | θ0 | RL 14 round後 | 差 | 両側p |
|---|---:|---:|---:|---:|
| Archaludon | 0.448 | 0.281 | -0.167 | 0.016 |
| Alakazam | 0.398 | 0.295 | -0.103 | 0.137 |
| Grimmsnarl | 0.271 | 0.208 | -0.062 | 0.310 |
| Rocket | 0.400 | 0.339 | -0.061 | 0.379 |

主因は収集がmirrorの単一rule agent、評価が6体の別poolであり、収集分布へ過適合して転移しなかったことだった。これは「RLでは向上しない」という証明ではないが、長く回すだけでは悪化を増幅するという強い反例である。

## 分析

### 良くなった点

最大の進展は、学習が失敗したときに原因を追跡できる構造になったことである。データの外部anchor、full-corpus split、episode continuity、recurrent carry、optimizer実更新、CUDA実測、result artifactを結び、欠損時に昇格しない経路ができた。データ漏洩とartifact自己申告による偽装も主要経路で閉じた。

full-corpus selectionは合計412,224 recordsで、episode/near-duplicate overlapが0である。旧32-record Gate sliceから、実学習に使える規模のauthorityへ進んだ点は大きい。全record dictをRAMへ保持する旧案を廃し、Alakazam実buildでpeak RSS約535 MBに抑えた。

### まだ性能向上を主張できない理由

recurrent routeで得られた値は、現時点ではfixture/testの結果だけであり、実full-corpusのvalidation NLL、carry-reset差、seed分散、checkpoint、対戦勝率はゼロである。テスト通過は「測定器が契約どおり動く」証拠であって、「モデルが強くなる」証拠ではない。

静的結果はlaneごとに方向が逆である。AlakazamではR3が優位、ArchaludonではR2が優位であり、R3を一括採用できる根拠にならない。旧4-lane結果でも3/4 laneでR3-B NLLが悪化した。recurrent carryがこの差を覆す可能性はあるが、実測前には仮説に留まる。

過去RLの悪化は、相手分布の不一致とcriticの楽観性が長時間runで増幅された実例である。現在のopponent schedule/provenance修正は必要条件だが、修正後のmulti-seed learner curveと独立評価はまだない。

### 計算量と環境

Alakazamのfull selection build/recompileは約39分、Archaludonは約14分、Alakazamのpeak RSSは約535 MBだった。旧実装のまま各passでsplitを再構築すると、Alakazamだけで1 cell約7.8時間、6 cell約46.8時間の再構成下限になる。このためjob-start一回のpreflight/frozen-index方式を実装したが、実laneでのpreflight時間、各pass throughput、scratch、CPU/GPU memoryは未測定である。

現在のsessionでは `torch.cuda.is_available() == False`、device count 0、`nvidia-smi` は `GPU access blocked by the operating system` である。したがって、この環境からpromotion authorityを持つCUDA Gateは実行できない。

## readiness判定

| 領域 | 判定 | 根拠 |
|---|---|---|
| legacy resume/fault | READY（確認範囲内） | 残存2障害の回帰2件PASS |
| static baseline | READY | current-R2 retained、R3 unapprovedをsealed |
| recurrent data authority | READY | 2 lane full corpus、overlap 0、外部SHA |
| recurrent code/integrity | READY FOR REAL PREFLIGHT | Task 3.6最終独立レビュー、新規Critical/Importantなし、55 tests PASS |
| recurrent performance evidence | NOT READY | actual full-corpus recurrent Gate未実行 |
| CUDA promotion run | BLOCKED | 現sessionでGPUがOS遮断 |
| teacher quality | NOT READY | current-pool result/fault/provenanceからの再導出未実装 |
| formal θ0 | NOT READY | checkpoint seal、新process reload未実装 |
| learner selection | NOT READY | 同一θ0からのPPO/V-trace/AWR-CRR実pilotなし |
| independent game evaluation | NOT READY | fresh-game、seat/opponent層化の改善証拠なし |
| long training | DO NOT START | 性能Gateと短期pilot未通過 |

## 今後の作業方針

### Phase 1: 実lane preflightとscale計測

両laneの既存manifest file SHAを外部anchorとして、Task 3.6のpreflightを1回ずつ実行する。preflight receiptとrun-local frozen indexを生成し、再読込で同一性を検証する。

必須記録はwall time、peak RSS、peak scratch bytes、shard hash time、index rebuild/freeze time、train/validation stream throughput、episode/step数である。途中でmanifest/snapshot/teacher/index/shardの不一致、symlink、episode再出現、split overlapが出たら停止する。

### Phase 2: GPU実行環境の復旧

RTX PRO 5000 Blackwellへアクセスできるhost/sessionで、CUDA、NVML、device name、peak allocated/reserved memoryを実測できることを確認する。GPUが使えない場合はCPU research smokeまでに留め、R3を選択・sealしない。

### Phase 3: research-only micro-pilot

actual sealed streamを使い、1 lane × 1 candidate × 1 seedの小update budgetで、配線、throughput、OOM、STOP loss、hidden carry、checkpoint書出しを確認する。この結果はselectionには使わない。目的は12-cell本番前の運用事故検出である。

### Phase 4: exact recurrent 12-cell CUDA Gate

2 lane × 2 candidates × 3 seedsを、同じsealed split、seed、budget、early-stop条件で実行する。既定thresholdを実験後に動かさない。

合格条件は、全12 cell完備、全metric有限、optimizer updateとpositive parameter delta、non-reset hidden steps、各lane STOP evidence、CUDA evidence、carry/reset non-inferiority、少なくとも1 laneの改善である。どちらのR3も不合格なら長時間学習へ進まず、表現・teacher・lane差の診断へ戻る。

### Phase 5: teacher quality再導出とformal θ0 seal

record内の既存 `quality_weight` を信用せず、current-pool result、fault、policy/deck/version provenance、confidence/agreement/search strengthからweightを再導出する。一次証拠がないrecordは理由付きで除外する。

Gateで選ばれたbest validation checkpointをatomic `.pt`へ保存し、新processでreloadして全tensor hash一致を確認する。θ0 manifestはsource/data/split/teacher/model/config/command/seed/metric/result artifact SHAを固定する。

### Phase 6: 短期multi-seed learner pilot

同一sealed θ0からPPO、V-trace、AWR/CRRを実optimizer updateさせ、2 lane × 3 seedsの短期pilotを行う。収集相手は評価poolと整合したscheduleを使い、seatとopponentを層化する。

最低条件は、複数seedでvalidation/returnが同方向に改善し、critic Brierがuniformより良く、value-returnの系統的な正バイアスや負相関がなく、importance clipping/faultが許容範囲であること。単一seedだけの改善、収集returnだけの改善、評価poolでの悪化は不合格とする。

### Phase 7: 独立層化対戦評価

fresh gamesを用い、lane × seed × opponent × seatを独立に集計する。engine seed/replay前提が証明できない場合はpaired promotion推論を使わない。baselineとの差がmacroで正方向であり、主要層で反転していないことを確認する。

### Phase 8: 長時間学習

Phase 1〜7を通過したcandidateだけを長時間学習へ進める。runにはatomic checkpoint、resume検証、attemptごとのexit code、fault provenance、traceback/state/action trace、progress summary、定期的な独立validationを持たせる。短期pilotの最良点を超えず、複数評価点で悪化する場合は自動停止し、長く回したこと自体を成功としない。

## 優先順位

直近の順序は次で固定する。

1. 実lane preflightとscale artifact生成。
2. CUDA host復旧・実測可能性確認。
3. actual sealed streamのresearch-only micro-pilot。
4. exact 12-cell CUDA Gate。
5. teacher quality materializerとformal θ0 seal。
6. 2 lane × 3 seedsの短期learner pilot。
7. 独立層化評価。
8. 合格時のみ長時間学習。

## 現時点の最終判断

「長時間学習で性能が上がる可能性」は残っている。特にfull-corpus recurrent supervision、相手分布是正、fault provenance、sealed evaluationは旧runより明確に改善している。しかし、性能面の新しい実測はまだなく、過去には4 laneすべてのRL方策がθ0より低下した。よって現時点の確度は**低〜中**であり、長時間学習開始の合理性はまだ不足する。

recurrent 12-cell Gateと短期multi-seed pilotが複数seedで正方向、critic校正が改善、独立層化対戦もbaselineより正方向になった時点で、初めて見込みを**中程度以上**へ引き上げ、長時間学習を開始するのが妥当である。

## 主要な根拠

- `docs/evidence/meta-specialist-v3-final-report.md`
- `docs/evidence/vtrace-rl-degrades-against-eval-pool-20260807.md`
- `docs/evidence/meta-specialist-two-lane-readiness-baseline-20260809.md`
- `docs/superpowers/specs/2026-08-09-r3-recurrent-theta0-gate-design.md`
- `.superpowers/sdd/2026-08-09-r3-recurrent-gate/task-3.6-report.md`
- `.superpowers/sdd/2026-08-09-r3-recurrent-gate/task-4-report.md`
- `.superpowers/sdd/2026-08-09-r3-recurrent-gate/scale-audit.md`
- `runs/meta-specialist-two-lane-readiness/recurrent-selection/alakazam.json`
- `runs/meta-specialist-two-lane-readiness/recurrent-selection/archaludon.json`

## Git・実行状態

作業branchは `feature/belief-guided-search`、確認時HEADは `30cade0e5d349d6ea545f019fc411e9d53288f16`。worktreeには本件以前からの多数の変更・未追跡ファイルがある。commit、push、Kaggle提出は行っていない。staleな中断scratch directoryも削除していない。
