# Outcome-Only Alternating Runtime Design

> status: user-approved research-only implementation design; no production, submission, or native-behavior authority

## Goal

既存のdeck/policy候補を、同一META_TRAIN評価分布とnative BestKnown controlへ接続し、
`96 -> 384 -> 768 -> 1536` の評価結果を再開可能な交互最適化状態へ戻す。

## Scope

新規runtimeは、既存の `build_native_candidate_games_v1` と
`run_parallel_cabt_evaluation` を組み合わせる。候補は既存deck mutationまたはhash-bound
policy configとして入力し、candidate/controlの両armを一つの評価ブロックへ束ねる。
通常実行は `workers=12`、`worker_recycle_games=16` とする。

META_TRAINのoutcome-only scheduleは opponentの重み付けに使えるが、nativeのprivate state、
teacher behavior label、local-eval-only資産のbehavior学習は入力にしない。

## Runtime contract

`OutcomeOnlyCandidateSpecV1` は candidate id、policy/deck pathとSHA、config SHA、env、biases、
min-score-gainを保持する。`OutcomeOnlyAlternatingRuntimeV1` は次を行う。

1. candidateとnative controlのgame cellsを同じreference IDs、seat、repetition、seedで生成する。
2. 全armを同一 `run_parallel_cabt_evaluation` 呼び出しへ渡し、独立workerで並列評価する。
3. ledgerからWDL、fault、seat、opponent、pair-keyを再集計し、control identityをSHA固定する。
4. stage結果をatomicなmanifest/summary/ledgerへ保存し、positiveなら次stage候補、negativeなら停止とする。
5. phaseは `POLICY_FIXED_SHORT` と `DECK_FIXED_LONG` のどちらかをmanifestへ固定し、固定側のSHA変更を拒否する。

authorityは常に `execute/training/promotion/submission/longrun=false`、`research_only=true` とする。
実行CLIは明示的な `--execute` がない限りmaterializeのみとし、既存run rootを上書きしない。

## Failure and resume

faultは分母から除外せず、candidate/controlいずれかにfaultがあればstageを停止する。
同一stageの再開はsealed manifest、候補SHA、control SHA、seed schedule、evaluator SHAが一致する場合だけ許可する。
candidateがnativeを二回連続で下回る場合は停止理由と直前state SHAを保存する。

## Verification

unit testsでcandidate/control strata一致、SHA再計算、phase固定、authority false、dry-run/no-clobberを確認する。
fixture runnerで実際のparallel evaluatorを呼ぶintegration testを用意し、最終的に対象suite、
`py_compile`、docs validator、`git diff --check`を実行する。
