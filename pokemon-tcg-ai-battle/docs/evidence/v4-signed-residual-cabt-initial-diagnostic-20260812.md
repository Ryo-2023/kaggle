# V4 signed residual 初回CABT診断（2026-08-12）

## 結論

この結果は、hash-bound sidecar factoryをCABTへ接続でき、seed別に24局をfault 0で完走したことを確認する研究用診断である。性能candidate、promotion evidence、noise floor超過の証拠ではない。後続レビューで判明したとおり、この時点のrunnerはruntime coverage counterを接続しておらず、`coverage.observed=false`・coverage count 0を出力する。したがって勝率差がresidual発火によるものか、base policyと同じ挙動だったのかを判別できない。

さらに、targetはstate-valueではなくfold外episode returnのglobal meanをbaselineとする`cross_fitted_mc_signed_behavior_residual`であり、prefix単位のsigned log-probability objectiveである。screenのbehavior actionは`decoding_mode=greedy`で収集されたため、これはpolicy-gradient estimatorではなく、outcome-conditioned ranking/self-imitation heuristicとして扱う。

## 実行identity

- subject deck: `opponents/tomatomato_archaludon/deck.csv`
- subject deck SHA-256: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- fixed pool: `EVAL_HELD_OUT_V1` の6 opponent
- seat: 0/1
- games per opponent×seat: 2
- requested games per seed: 24
- engine seed: 未対応（CABT `random_device`/`shuffle`、game-level paired不可）
- evaluation protocol SHA-256: `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`
- base seed: `10100000`
- evaluator: `scripts/run_frozen_residual_cabt_eval_v1.py`
- evaluator schema: `meta-specialist-frozen-residual-cabt-strength-v1`
- sidecar factory: `src/mage_ptcg/meta_specialist/frozen_residual_factory_v1.py`
- base policy: 対応seedのWave6 V4 checkpoint
- residual target: signed outcome tiny artifact（各seed最大2 episode / 1 update）
- authority: `training_permitted=false`, `promotion_authority=false`, `longrun_allowed=false`

## 結果

| seed | sidecar report | sidecar SHA | wins / 24 | seat 0 | seat 1 | faults | elapsed |
|---:|---|---|---:|---:|---:|---:|---:|
| 0 | `runs/meta-specialist-signed-residual-tiny-20260812/seed-0/fixed-six-24.json` | `e512024175133257ad2a4280d0b99ca6b8f0857a96c6f821368e7066695550fc` | 11 | 5/12 | 6/12 | 0 | 32.6 s |
| 1 | `runs/meta-specialist-signed-residual-tiny-20260812/seed-1/fixed-six-24.json` | `1af6823337d35a4b788d0cf83b509f6f578e6810f1c4b3c38d3485a7082c0d82` | 13 | 7/12 | 6/12 | 0 | 30.5 s |
| 合計 | — | — | **24 / 48 (50.00%)** | — | — | **0** | — |

per-opponentはseed0が`kiyotah 3/4, nihei 1/4, ozawa 2/4, skarin 3/4, sue 1/4, yaroslav 1/4`、seed1が`kiyotah 2/4, nihei 2/4, ozawa 3/4, skarin 1/4, sue 3/4, yaroslav 2/4`である。

比較用に同じ24局/blockで記録済みのWave6はseed0 `11/24`、seed1 `11/24`、合計`22/48`である。見かけ上はsigned residualが+2勝（+4.17pt）だが、seed0は同率、seed1の+2勝だけであり、各cell 2局・CABT非paired・coverage未観測である。既知のWave6同一checkpoint noise（seed0 SD 2.62pt、seed1 SD 7.51pt）より小さいため、改善とは判定しない。

## coverageの欠落と再分類

初回runnerは次のように出力した。

```json
{
  "coverage": {
    "observed": false,
    "reason": "runtime sidecar counters not yet connected",
    "total_decisions": 0,
    "known_context": 0,
    "known_action": 0,
    "nonzero_residual": 0,
    "ood_pass_through": 0,
    "stop": 0
  }
}
```

これは「coverageが0だった」という観測ではなく、「測定していない」という意味である。したがって、これらのJSONの`faults=0`だけを根拠にperformance evidenceと呼ばない。次回runnerでは少なくとも、total decisions、exact-known context、known action slots、residual-applied、nonzero residual、top-1 flip、OOD/malformed pass-through、STOP、opponent×seatの各カウンタを保存する。

## target semanticsの限界

`src/mage_ptcg/meta_specialist/cross_fitted_outcome_residual_v1.py` の現行baselineは、episode SHAで2 foldに分け、当該episodeと同じfoldを除いたepisode returnのglobal meanである。これはstate value `V(s)`ではなく、次のheuristicである。

- 勝ちepisodeの実行prefixを全体的に強める
- 負けepisodeの実行prefixを全体的に弱める
- discountによりterminal距離を少し分ける

敗戦episode内の正しいaction、forced action、敗因actionを区別しない。今後は`episode-outcome signed self-imitation v1`として履歴保存し、性能本線ではactor-visible stateからcross-fitted `V_hat(s)`を推定し、`A_t = G_t - V_hat_heldout(s_t)`へ置換する。

## loss normalizationの限界

現行signed trainerは各episode/sequenceの中で`sum(abs(signed_weight))`をnormalizerにする。そのためepisode間は1 updateあたり同程度だが、episode内ではprefix数と`abs(weight)`に比例する。multi-selectのprefix列、長いgame、長い勝ちepisodeがrecord数以上のmassを持つ。

性能比較前に、complete physical recordのlog probability（prefix log probabilityの合計）を1 sampleとする`record_normalized`と、episode全体の総abs advantageを固定する`episode_normalized`を別armとして比較する必要がある。現行prefix-weight結果をこの二つと同列に扱わない。

## behavior policy

V4 DAgger screenのjob builderは`ActorJobConfigV1(decoding_mode="greedy")`を固定し、runtimeは`greedy_decode_runtime_action_v2`を呼ぶ。したがって今回のscreen chosen actionはcategorical samplingではなくdeterministic greedyである。保存された`behavior_log_probability`はそのgreedy actionをsoftmaxで再評価した値であり、行動がその分布からsampleされたことを意味しない。importance ratioやunbiased REINFORCE/AWRと解釈しない。

## 次の再開条件

1. zero-init sidecarでWave6 action sequenceが一致する parity testを通す。
2. runtime coverageを実測し、exact gateがほぼ0なら、pre-registered coarse public bucket gateへ切り替える。
3. feature auditで、exact SHAはgate/provenanceだけに使い、residual inputがpublic state、candidate/domain summary、base logit/margin、action type、STOP、prefix depth、coarse OODを十分表すことを確認する。
4. global mean baselineをstate-value cross-fitへ置換するか、少なくとも現行targetをdiagnostic controlとして固定する。
5. complete-action/record-normalizedおよびepisode-normalized lossの2 armを同じseed/data/update budgetで比較する。
6. その後に限り、24局/seedをcoverage smokeとして再実行し、通過armだけ96局×3 independent blocksへ進める。

上記が終わるまで、signed residual full-screen学習、shadow-C勝率評価、longrun、Champion変更、Kaggle提出は開始しない。
