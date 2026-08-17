# Submission Closure read-only audit v1 (2026-08-13)

## 結論

この監査は既存の提出物・コード・性能artifactを変更せず、提出closureを一次artifactの再ハッシュと静的検証だけで確認した。結論は次の通りである。

| 対象 | 判定 | 根拠 |
|---|---|---|
| 凍結済み `Rule v0 + root deck` のローカルportable archive | **GO（local package only）** | archive member、manifest、deck qualification、Rule v0 default entrypointのhashが一致。既存archive-only CABT smokeはDONE/fault0/illegal0。 |
| root deckのdeck qualification | **PASS（local qualification）** | 60枚、`bundle_allowed`、CABT legality `passed`、production vocabulary一致。authorityは全false。 |
| `tomatomato_archaludon` / `lucifer19_battlecore` / `plamen06_steel` native pairのas-is提出 | **NO-GO** | pool全102 assetが`local_eval_only`。native source/deckのsubmission permissionはない。 |
| Strong Asset由来自前studentの提出 | **NO-GO（未成立）** | real META_TRAIN public advantage未生成、candidate package/entrypoint/clean-room closure未成立。 |
| Kaggleへの外部提出 | **NO-GO / contract確認待ち** | format、archive type/size、runtime制約、Rules acceptanceが`UNKNOWN`。本監査は提出権限を付与しない。 |

したがって現在の安全なfallbackは、性能BestKnownを意味するものではないが、`Rule v0 + root deck` の凍結archiveだけである。Strong Assetの性能分類ではTomato nativeが`EvaluationBestKnown`/`BestKnownArchaludon`暫定control、`GlobalBestKnown=UNRESOLVED`、Strong Assetの`SubmissionEligibleBestKnown`は未成立である。

## 対象と変更境界

- 既存B collector、Task4 materializer、production `main.py`、`deck.csv`、性能runner、performance artifactを編集していない。
- CABT実対戦、学習、longrun、Kaggle API/CLI、archive build、commit/pushは起動していない。
- 実施したのは、既存JSON/manifest/archiveの読込、SHA再計算、archive構造検証、qualification verifier、静的import/default-route確認のみである。
- worktreeは既存差分を含むdirty状態である。今回の変更はこのevidenceファイルのみである。

## 1. Root deck qualification

一次artifactは次の通り。

| artifact | SHA-256 / 事実 |
|---|---|
| `runs/final-sprint-autonomous/submission-root-deck-qualification-v1/qualification.json` | `8de4add0d58ab41f6c3ccb1d3bf0ab33208ac132485bab87ea474ee80be1af2c` |
| qualification semantic field | `b7715b357508961717fd1243386ab39843b97a8327763e491f010e7eafbb9b67` |
| qualified deck file | `deck.csv`, SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` |
| card count | `60` |
| usage boundary | `bundle_allowed` |
| CABT legality | `passed`, schema `specialist-cabt-legality-v1` |
| embedded CABT evidence | SHA `3d10ed9c577214da40c895b274b0beea815c12eec025169ccaa7c4c1cfb0a347`; engine source SHA `b920133088bb09aa0da10891e856b31ab6d8a51b27d083a3a5942e1319c379c5` |
| production vocabulary | `meta-specialist-en-card-database-v1`, source SHA `a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373` |
| authority | `training=false`, `promotion=false`, `submission=false` |

`verify_submission_deck_qualification_v1` を現行rootに対して読み取り専用で実行し、qualification semantic SHA、deck SHA、vocabulary、CABT evidence、authorityの再検証が通った。これはlocal qualificationであって、Kaggleの外部submission acceptanceではない。

## 2. Rule v0 portable entrypoint / archive

凍結archiveは `runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck/submission.tar.gz` である。

| artifact | SHA-256 / 事実 |
|---|---|
| archive | `da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a` |
| sibling manifest | `9c5388fe7c9fd1573f71b3acdba75016fbeed8aca56f7a66f45d26afc929a170` |
| manifest content hash | `7fe5be9a33ed3d4e8767331336c6fdc553d757583313f7f701c1b94a49e09d98` |
| source revision recorded by manifest | commit `30cade0e5d349d6ea545f019fc411e9d53288f16`, `dirty=true` |

archive memberは順序を含めて次の4件だけであり、sibling manifestおよび現行source bytesと一致した。

```text
main.py
deck.csv
agents/__init__.py
agents/rule_agent.py
```

default routeは `main._DEFAULT_AGENT = make_rule_agent()` → `agents.choose_rule_indices` である。`knowledge_pack=None` のためKnowledge importは実行されず、entrypointの実行経路はarchive内のstdlib + `agents`に閉じる。`make_rule_agent_v1`、bounded search、Student loaderはresearch/local factoryであり、archiveのpublic `agent(obs_dict)` default routeから呼ばれない。したがってこの判定は「Rule v0 default routeのportable closure」に限定する。

既存一次evidence [`performance-first-submission-bundle-20260812.md`](performance-first-submission-bundle-20260812.md) はarchive-only subprocess smokeを2局で記録している。2局とも`DONE`、fault 0、illegal action 0、legality `pass`、archive-only `true`である。今回そのCABT smokeを再実行しておらず、既存evidenceを再ハッシュ・再利用した。

CABTを起動しないportable import smokeも一時展開したarchive rootで実行し、`python -I`から`main.agent({})`が60枚deckを返すことを確認した（`portable_default_import=PASS deck_count=60`）。

なおarchiveはCABT engine本体を同梱しない。`test_sim.py`のengine SHAはdeck qualificationのlocal evidenceへbindされるが、提出環境のengine/runtime contractを証明するものではない。従って「local package GO」と「external submission GO」を混同しない。

## 3. Strong Asset permission / teacher provenance

primary inventoryは次のSHAに固定されている。

| artifact | SHA-256 |
|---|---|
| `opponents/pool_manifest.json` | `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca` |
| `configs/meta_specialist/autonomous_meta_distribution_v1.json` | `222a32772a640c5362399d1839cc6ada743481670497784da849f8415ab12fde` |
| autonomous meta manifest | `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae` |
| derivation decision `docs/decisions/2026-08-05-archaludon-teacher-derivation.md` | `e64cc3f3e74bf5b96932438b4718af3079f56d1c7da64bc27524d02432e3a6fc` |
| internal permission policy | `8adebfe8b886831c21883e5d7c4298afcd39827a5f22d75958e12c5ce8261f05` |

pool 102件はすべて`usage_boundary=local_eval_only`であり、native policy/deckのas-is submissionを許可しない。Tomato/Lucifer/Plamenの既存teacher datasetは、manifest上の`allowed_usages=["training-local"]`と`teacher_usage_boundary=local_eval_only`を分離して記録している。これは許可済みrecordからのローカル導出を意味するが、native source/deckの提出やbehavior sourceの無条件利用を意味しない。

既存 teacher manifest SHA:

```text
tomato  de04f029ff18cbe0e2209c57dd17a73d90d5ae7a4ac6a0bc8706543349e2d41c
Lucifer a03dfc1f410cdf23b2404cdd3411271776805018f679ad61f6777f32ea949e0d
Plamen  ade4643925ac9ec3b4c737499b0cb8994279555505fb458d629ae5fdc8e1f45e
```

現行META_TRAIN entryは`behavior_allowed=false`で、real public advantage tableは未生成である。B public-only self-rolloutはdesign/audit laneに留まり、実収集0局、authorization/pool/projection/completion gatesの修正完了前はNOT_READYである。したがってstudent candidateのpackage/entrypoint/teacher provenanceを提出可能状態へ昇格できない。

B routeの独立設計監査は `.superpowers/sdd/2026-08-13-native-preserving-meta-overfit/b-route-design-review.md`（SHA `35a922d9fc9cd32527672250b47a052a5ad1a5e11faa3a2906e2a05c31f615df`）で、Important 4 / Minor 1、`DESIGN_ONLY / collection NO-GO` と判定されている。route比較のJSON/Markdownはそれぞれ `f8fee41ebfc7f43413335c9c96a6e29aa0557bbb35ca0fb49372f66039131653` / `412f2769da821a2d04854939ad95581413096eb572df1223817b8f945cc0c94e` であり、既存の`local_eval_only`/`behavior=false`を上書きする根拠にはならない。

## 4. BestKnownと提出経路の分離

integrated scoreboardの一次artifactは次の通り。

| artifact | SHA-256 |
|---|---|
| `docs/evidence/autonomous-integrated-scoreboard-v1-20260813.json` | `39f76c6474bbf6dbe89d8adf620da92a8cd240487c35c8d4c40637b4afd7023a` |
| explanation markdown | `dc4047a90594d97b6b986c9b93c4a18a1cf756618694a6c647308c48a9e4fd95` |

native pooled1536ではTomato `1107/1536=72.0703%`、Lucifer `1103/1536=71.8099%`、Plamen `1102/1536=71.7448%`、全fault0である。Tomatoを`EvaluationBestKnown`/`BestKnownArchaludon`の暫定controlに固定しているが、native poolのas-is submission permissionは無い。`GlobalBestKnown=UNRESOLVED`、Strong Assetの`SubmissionEligibleBestKnown`は未成立で、現在のsubmission fallbackはRule v0 root archiveだけである。

## 5. Blockerと再開条件

### Blocker

1. Kaggle external contractが`UNKNOWN`（format/archive type/size、runtime dependency、Rules acceptance、Submit entrypoint）。
2. Strong Asset native source/deckは`local_eval_only`で、as-is package permissionなし。
3. real META_TRAIN public advantage tableとbehavior-authorized B collector artifactが未成立。
4. Strong Asset candidateのportable entrypoint、self-owned/bundle-allowed deck、clean-room package、checkpoint/rollback lineageが未成立。
5. 現archive manifestは`dirty=true`のsource revisionを記録する凍結artifactである。root runtime bytesを変更した場合はarchive/manifest/deck qualificationを再生成・再検証する必要がある。

### 再開条件

1. verified external behavior permission、または独立監査を通過したpublic-only self-rollout authorization/pool/projection/completion artifactを作る。既存`local_eval_only`/`behavior=false`を上書きしない。
2. real META_TRAIN public advantage tableを新run-rootへ生成し、policy/deck/evaluator/common24/seed/source SHAとpermissionをbindする。synthetic fixtureは性能根拠にしない。
3. self-ownedまたは明示`bundle_allowed`の60枚deckをqualification/CABT legality/lineageで固定する。
4. teacher source/deck/raw recordsを同梱せず、自前candidate weight/runtimeだけをportable archiveへ閉じる。default entrypoint、fallback、dependency/secret scan、clean-room runtimeを一次artifact化する。
5. native Tomato controlに対してcommon24を96→384→768→1536局で逐次評価し、fault/illegal/timeout/seat gateを満たしつつBestKnownを上回ることを確認する。
6. Kaggle Submit画面でcontractを人間確認し、独立package reviewと明示承認を得る。これ以前にKaggle API/CLIやChampion変更を行わない。

## 再現コマンド（今回実行済み、すべてread-only）

```bash
tar -tzf runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck/submission.tar.gz
sha256sum runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck/{manifest.json,submission.tar.gz}
sha256sum runs/final-sprint-autonomous/submission-root-deck-qualification-v1/qualification.json deck.csv
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python - <<'PY'
from pathlib import Path
from scripts.build_submission import validate_submission_archive
from mage_ptcg.meta_specialist.submission_deck_qualification_v1 import verify_submission_deck_qualification_v1
validate_submission_archive(Path('runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck/submission.tar.gz'))
verify_submission_deck_qualification_v1(
    Path('runs/final-sprint-autonomous/submission-root-deck-qualification-v1/qualification.json'),
    Path('.'),
)
print('read-only archive/qualification verification: PASS')
PY
# CABTを起動せず、一時展開したarchiveだけでdefault entrypoint/deck登録を確認
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
from pathlib import Path
import subprocess, sys, tarfile, tempfile
with tempfile.TemporaryDirectory(prefix='portable-rule-v0-') as tmp:
    root = Path(tmp)
    with tarfile.open(Path('runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck/submission.tar.gz'), 'r:gz') as handle:
        handle.extractall(root, filter='data')
    subprocess.run([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0, "."); import main; deck=main.agent({}); assert len(deck)==60; print("portable_default_import=PASS deck_count=%d" % len(deck))'], cwd=root, check=True)
PY
```

今回の検証はCABT本体を起動しない。既存archive-only smokeおよびdeck qualificationのCABT evidenceは上記primary artifactにbindされた過去結果として扱う。
