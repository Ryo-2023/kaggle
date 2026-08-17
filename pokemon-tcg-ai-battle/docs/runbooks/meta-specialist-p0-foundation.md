# Runbook: Meta Specialist P0 Foundation ビルド／検証 CLI

## 結論

`python -m mage_ptcg.meta_specialist`（`src/mage_ptcg/meta_specialist/cli.py`）は、meta-specialist 提出バンドルを**ローカルでビルド・構造検証するだけ**の JSON CLI である。デッキの CABT 適格性判定、DeckLock 作成、アーカイブの build／verify のサブコマンドを持つ。Kaggle への提出、ネットワークアクセス、Git remote 操作、`kaggle` CLI 呼び出しのコードパスは存在しない（`tests/meta_specialist/test_meta_specialist_cli.py::test_cli_source_has_no_submission_or_network_path` などで機械検証済み）。

現状で **Kaggle 上で実際に動作するエージェントはまだビルドできない**。理由は既知の未解決依存 1 件であり、本ドキュメントの [5. 既知の制限](#5-既知の制限-p0-の未解決依存) に記載する。ビルド・構造検証（`build-submission`／`verify-submission`）自体はこの依存に影響されず、現時点でも正しく動作する。

## 0. 前提条件

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
export PYTHONPATH=.:src
PY=.venv/bin/python
```

以降のコマンド例は上記の `PY` 変数を使う。`scripts/build_meta_specialist_submission.py` と `scripts/verify_meta_specialist_submission.py` は自分自身で `sys.path` に `src/` を追加するため `PYTHONPATH` は不要だが、`python -m mage_ptcg.meta_specialist` を直接呼ぶ場合は `PYTHONPATH` の設定が必要。

## 1. サブコマンド一覧

| サブコマンド | 用途 | 主な入力 |
|---|---|---|
| `show-runtime-constraints` | 凍結済み v1 runtime constraint manifest を表示 | なし |
| `show-ladder-contract` | 公式 ladder mechanics contract を表示 | `--checked-at-utc`（手動確認日時、caller-recorded） |
| `qualify-deck` | デッキ資産を registry・known card ID・CABT evidence に対して適格化 | `--asset-json` `--registry` `--known-card-ids` `--cabt-evidence-json` |
| `lock-deck` | `DeckLockDecision` を作成 | 6 個の canonical field（`create_deck_lock` と同一） |
| `build-submission` | ローカル bundle spec からアーカイブを build＋構造検証 | `--spec` `--output` |
| `verify-submission` | 既存アーカイブを import/実行せず構造検証のみ | `--archive` |

成功時は canonical JSON 1 行を stdout へ、失敗時（exit 2）は `{"status":"ERROR","error_type":...,"message":...}` を stderr へ出力する。`error_type` は `ARGUMENT_ERROR` / `INPUT_ERROR` / `CONTRACT_ERROR` / `SECURITY_ERROR` のいずれか。

```bash
$PY -m mage_ptcg.meta_specialist show-runtime-constraints
$PY -m mage_ptcg.meta_specialist show-ladder-contract --checked-at-utc 2026-08-02T00:00:00Z
```

## 2. デッキの適格化から DeckLock まで

`qualify-deck` は CABT legality を **自分では計測しない**。`--cabt-evidence-json` に、デッキと厳密に紐づく（`deck_identity`／`deck_file_sha256` が一致する）既測定の証拠ファイルを渡す必要があり、一致しない場合や証拠が無い場合は `CONTRACT_ERROR` で失敗する。これは「測定していない CABT 合格を捏造しない」というハード要件を CLI レベルで担保するための設計であり、実際の CABT 計測手段（`tests/meta_specialist/test_runtime_cabt.py` が使う native engine 等）は本 CLI の外で別途実行する。

以下は動作確認済みの最小例（**合成データ**であり、競技性能や実デッキの合法性を主張するものではない）。

```bash
WORKDIR=$(mktemp -d)

# 1. 60 枚デッキ（alakazam archetype の core card を含む合成データ）
$PY - <<PYEOF
from pathlib import Path
cards = ([741, 742, 743] * 4) + [1] * 48
Path("$WORKDIR/deck.csv").write_text("\n".join(map(str, cards)) + "\n")
PYEOF

# 2. asset-json（provenance）
cat > "$WORKDIR/asset.json" <<EOF
{
  "asset_id": "runbook-example",
  "archetype_id": "alakazam",
  "deck_path": "$WORKDIR/deck.csv",
  "source_ref": "docs/runbooks/meta-specialist-p0-foundation.md",
  "source_commit": "$(git rev-parse HEAD)",
  "asset_class": "deck_only",
  "usage_boundary": "bundle_allowed",
  "policy_compatibility": "specialist-v2",
  "card_database_version": "runbook-example-v1"
}
EOF

# 3. known-card-ids（Card ID 列を持つ CSV。ここでは合成の広い範囲を使う）
$PY - <<PYEOF
import csv
with open("$WORKDIR/known_card_ids.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["Card ID", "Name"])
    for cid in range(1, 2000):
        w.writerow([cid, f"card-{cid}"])
PYEOF

# 4. CABT evidence（deck_identity/deck_file_sha256 はこのデッキから計算する。
#    "evidence" 文字列は実測の記録を書く場所であり、ここではダミー）
DECK_IDENTITY=$($PY -c "
from mage_ptcg.knowledge.model import deck_identity_from_card_ids
print(deck_identity_from_card_ids([int(x) for x in open('$WORKDIR/deck.csv').read().split()]))
")
DECK_SHA=$(sha256sum "$WORKDIR/deck.csv" | cut -d' ' -f1)
cat > "$WORKDIR/evidence.json" <<EOF
{
  "schema_version": "meta-specialist-cabt-deck-evidence-v1",
  "passed": true,
  "deck_identity": "$DECK_IDENTITY",
  "deck_file_sha256": "$DECK_SHA",
  "card_database_version": "runbook-example-v1",
  "cabt_runtime_version": "kaggle-environments==1.32.0",
  "evidence": "runbook example placeholder; not a real CABT measurement"
}
EOF

# 5. qualify-deck 実行（configs/meta_specialist/archetypes_v1.json は実在する registry）
$PY -m mage_ptcg.meta_specialist qualify-deck \
  --asset-json "$WORKDIR/asset.json" \
  --registry configs/meta_specialist/archetypes_v1.json \
  --known-card-ids "$WORKDIR/known_card_ids.csv" \
  --cabt-evidence-json "$WORKDIR/evidence.json" \
  > "$WORKDIR/qualified.json"
cat "$WORKDIR/qualified.json"
```

`qualified.json` の `deck_identity` を使って `lock-deck` を実行する。

```bash
DECK_IDENTITY=$($PY -c "import json;print(json.load(open('$WORKDIR/qualified.json'))['deck_identity'])")
$PY -m mage_ptcg.meta_specialist lock-deck \
  --archetype-id alakazam \
  --selected-deck-identity "$DECK_IDENTITY" \
  --compared-deck-identities "$DECK_IDENTITY" \
  --foundation-init-id "$(printf 'b%.0s' {1..64})" \
  --joint-race-schedule-id "$(printf 'c%.0s' {1..64})" \
  --equal-transition-budget 1 \
  > "$WORKDIR/deck_lock.json"
cat "$WORKDIR/deck_lock.json"
```

## 3. Bundle spec の作成と build／verify

`build-submission`／`verify-submission` は `mage_ptcg.meta_specialist.package.BundleSpec` を消費する。`BundleSpec` は `QualifiedDeckAsset`／`DeckLockDecision`／`RuntimeConstraintManifest` などの Python オブジェクトを直接要求するため、CLI の外（Python スクリプト）で一度組み立てて `write_bundle_spec` で JSON 化する。テンプレートは `templates/meta_specialist/`（`main.py`／`policy_loader.py`／`rule_policy_v1.py`）にある。

```bash
$PY - <<PYEOF
import hashlib, shutil
from pathlib import Path
from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.meta_specialist.contracts import ladder_mechanics_payload
from mage_ptcg.meta_specialist.decks import ArchetypeSpec, DeckAssetInput, create_deck_lock, qualify_deck_asset
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import CABT_AGENT_JSON_CONTRACT_SHA256_V1
from mage_ptcg.meta_specialist.package import (
    BundleSpec, DependencyContractIds, derive_entrypoint_contract_id, write_bundle_spec,
)

workdir = Path("$WORKDIR")
source = workdir / "bundle_source"; source.mkdir(exist_ok=True)
shutil.copy(workdir / "deck.csv", source / "deck.csv")
templates = Path("templates/meta_specialist")
for name in ("main.py", "policy_loader.py", "rule_policy_v1.py"):
    shutil.copy(templates / name, source / name)

cards = tuple(int(x) for x in (source / "deck.csv").read_text().split())
asset = DeckAssetInput.from_path(
    asset_id="runbook-example", archetype_id="alakazam", path=source / "deck.csv",
    source_ref="docs/runbooks/meta-specialist-p0-foundation.md", source_commit="a" * 40,
    asset_class="deck_only", usage_boundary="bundle_allowed",
    policy_compatibility="specialist-v2", card_database_version="runbook-example-v1",
)
qualified = qualify_deck_asset(
    asset, ArchetypeSpec("alakazam", (), (cards[0],), "qualified_not_trained"),
    known_card_ids=set(cards), cabt_legality=lambda _: (True, "runbook example placeholder; not a real CABT measurement"),
)
constraints = RuntimeConstraintManifest.frozen_v1()
ladder = ladder_mechanics_payload(checked_at_utc="2026-08-02T00:00:00Z")
ladder["ladder_mechanics_id"] = content_id("meta-specialist-ladder-mechanics-v1", ladder)
lock = create_deck_lock(
    archetype_id=qualified.archetype_id, selected_deck_identity=qualified.deck_identity,
    compared_deck_identities=(qualified.deck_identity,), foundation_init_id="b" * 64,
    joint_race_schedule_id="c" * 64, equal_transition_budget=1,
)
members = ("deck.csv", "main.py", "policy_loader.py", "rule_policy_v1.py")
dependency_ids = DependencyContractIds(
    cabt_agent_json_contract_id=CABT_AGENT_JSON_CONTRACT_SHA256_V1,
    runtime_constraints_id=constraints.runtime_constraints_id,
    ladder_mechanics_id=ladder["ladder_mechanics_id"],
    entrypoint_contract_id=derive_entrypoint_contract_id(source, members, policy_members=("rule_policy_v1.py",)),
)
policy_bytes = (source / "rule_policy_v1.py").read_bytes()
policy_identity = content_id("meta-specialist-static-policy-v1", [
    {"path": "rule_policy_v1.py", "sha256": hashlib.sha256(policy_bytes).hexdigest(), "size": len(policy_bytes)},
])
spec = BundleSpec(
    source_root=source, members=members, deck_member="deck.csv",
    policy_entrypoint_member="policy_loader.py", qualified_deck_asset=qualified, deck_lock=lock,
    runtime_constraints=constraints, ladder_mechanics=ladder, dependency_contract_ids=dependency_ids,
    candidate_class="static_rule_bundle", policy_members=("rule_policy_v1.py",), model_member=None,
    policy_identity=policy_identity, checkpoint_lineage_id=None,
    checkpoint_lineage_reason="not_applicable_static_policy",
)
write_bundle_spec(spec, workdir / "bundle_spec.json")
print("spec written to", workdir / "bundle_spec.json")
PYEOF
```

```bash
$PY scripts/build_meta_specialist_submission.py --spec "$WORKDIR/bundle_spec.json" --output "$WORKDIR/submission.tar.gz"
$PY scripts/verify_meta_specialist_submission.py --archive "$WORKDIR/submission.tar.gz"
```

両方とも `"status":"structurally_verified"` の JSON を返せば成功。この構造検証は tar/gzip の byte-level 整合性、manifest とデッキ・DeckLock・policy bytes の一致、`202,400 KiB`（`contracts.BUNDLE_SIZE_LIMIT_KIB`）以下のアーカイブサイズを確認するが、**`main.py` を import も実行もしない**。

## 4. テストと検証コマンド

```bash
$PY -m pytest -q -p no:cacheprovider tests/meta_specialist/test_entrypoint.py tests/meta_specialist/test_meta_specialist_cli.py tests/meta_specialist/test_submission_scripts.py
$PY -m pytest -q -p no:cacheprovider tests/meta_specialist/
```

## 5. 既知の制限（P0 の未解決依存）

`templates/meta_specialist/main.py` は import 時に `mage_ptcg.meta_specialist.entrypoint.build_packaged_agent` を呼び、**現時点では常に例外を送出する**。原因は 1 点のみ:

- `mage_ptcg.meta_specialist.actor_visible_features_v1.require_production_card_vocabulary_v1` が、信頼できる sealed card-vocabulary registry が未整備であることを理由に無条件で `raise` する（設計上の意図的な fail-closed であり、本タスクの不具合ではない）。
- この registry が整備されるまで、`main.py` は Kaggle 上でエージェントとして機能しない。これは意図的な安全側の挙動であり、`test_only` の代用ボキャブラリで隠蔽していない（`tests/meta_specialist/test_entrypoint.py::test_build_packaged_agent_never_bypasses_the_production_vocabulary_gate` が、代用を差し込もうとしても gate が塞ぐことを検証する）。

加えて、`main.py`／`policy_loader.py` は `mage_ptcg.meta_specialist.*` を import するため、Kaggle へ実際に提出可能なアーカイブを作るには `src/mage_ptcg` の必要な依存クロージャをアーカイブの `members` に含める必要がある（Rule Agent v0 の `scripts/build_submission.py` は `src/` を含めず完全に自己完結させている一方、meta-specialist の runtime はそのままでは自己完結しない）。この依存クロージャの決定・vendoring 方針は本タスクのスコープ外であり、`TODO:` として残す（要検証: 必要ファイル一覧、`_MAX_MEMBER_COUNT=4096`・`BUNDLE_SIZE_LIMIT_KIB=202400` に収まるか）。

`build-submission`／`verify-submission` はこれらの依存に影響されず、[3. Bundle spec の作成と build／verify](#3-bundle-spec-の作成と-buildverify) の手順は現時点でも正しく動作する。

## 6. 提出について

このドキュメントとこの CLI は **Kaggle への提出を一切行わない**。提出は、ユーザーが対象アーカイブと提出実行を明示した場合にのみ、別途手動で行う（[AGENTS.md](../../AGENTS.md) の「共通規則」を参照）。
