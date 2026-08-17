---
project: MAGE-PTCG
evidence_type: kaggle-submission-contract
as_of: 2026-07-16
---

# Kaggle submission contract

> 2026-07-30 更新: 実提出を行う wrapper は削除済みである。リポジトリは package build とローカル検証だけを扱い、Kaggle 提出の実行経路を提供しない。

competition slugはrepository正典の`pokemon-tcg-ai-battle`である。これはSimulation agent型で、`main.py`の`agent(obs_dict)`と60枚deckが必要である。一方、Kaggle CLIはこの環境に未導入であり、公式Overview／RulesはJavaScriptのみでSubmit仕様を取得できなかった。submission method、required archive type、package size limit、internet/GPU制約、status APIは`UNKNOWN`である。

提出実行 wrapper は削除済みである。`probe_kaggle_contract.py`はCLI未導入、auth未設定、Rules/access未確認を値を出さずに区別し、method/archive typeを推測しない。

package build/verifyはローカルで可能であり、Student packageのarchive-only actual cabt smokeもPASSした。したがってreadinessは`PACKAGE_READY`、contract状態は`CONTRACT_CONFIRMATION_REQUIRED`である。実提出には人間がSubmit画面でformat、entrypoint、Rules acceptanceを確認してconfig contractを更新し、独立レビュー後に明示承認する必要がある。
