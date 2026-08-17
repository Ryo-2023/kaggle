---
project: MAGE-PTCG
evidence: o1-competition-intelligence-o1-5
as_of: 2026-07-18
scope: detached O1 Competition Intelligence worktree only
---

# O1-5 External Source Adapter Evidence

O1 sidecarへdefault-disabled external transport、capability report、schema drift gate、secure Team Bundle import、5 CLI commandを追加した。live Kaggle accessは実行していない。

- `PUBLIC_OTHER`の`TRAINING`と`REDISTRIBUTION`はSourceEnvelope契約で拒否される。
- baselineはresponse valueを保存せず、structural shape、fingerprint、trust labelだけを保存する。fixture／recorded responseだけがtrusted test baselineを確立し、未知live structured responseはquarantineする。
- raw archive、source manifest、schema baseline、runstate recordは単一run lockで保護する。
- Team Bundleはpath traversal、symlink、non-regular file、hash mismatch、duplicate path、permission escalationをquarantineし、absolute bundle pathをportable output／quarantine detailへ保存しない。
- sidecarからOffline Training／Studentへexternal actionを渡す経路は追加していない。

## 検証

```bash
pytest -q tests/competition_intelligence/test_external_sources.py \
  tests/competition_intelligence/test_cli.py tests/competition_intelligence/test_contracts.py
```

結果：70 passed。

repository rootを`PYTHONPATH`へ含めない通常pytest起動では、既存fixtureがimportする`agents.rule_agent`を解決できず、O1-5と無関係に既存Offline Training依存testが収集段階で失敗した。rootを明示する環境で再試行する。

## 制約

live Kaggle CLI／credential／remote APIは未使用。live TOFUは既定拒否であり、有効化には別途明示的なcaller opt-inが必要である。
