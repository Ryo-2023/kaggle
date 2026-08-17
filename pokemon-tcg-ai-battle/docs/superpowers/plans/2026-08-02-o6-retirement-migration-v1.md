# O6 Retirement and Evidence Migration v1

## Goal

Remove the obsolete O6 implementation, tests, reports, and external handoff
trees requested by the user without deleting current non-O6 code or losing the
evidence needed to explain why O6 cannot support a current leaderboard census.

Deletion is last.  Every dependency and retained byte is migrated and verified
first; a partial migration does not authorize a partial broad delete.

## Retain before deletion

Copy these exact files from
`handoff-artifacts/o6-public-live-acquisition-v1-gemini-remediation/` to one
content-addressed non-O6 evidence directory and preserve bytes:

| file | SHA-256 |
|---|---|
| `candidate_audit.json` | `7523431d571fcb2ca88460d7daa200405a6bdc38e88920802a0377cbd106625c` |
| `leaderboard_evidence.json` | `307ca85dc32735b2befbea36acf989d2fccfe73b3bbf9eced063e306905d5ba2` |
| `live_probe_report.json` | `c5db40cb73fd7792327559e67cc33f9cb63530d5ffbf6ef821250557ebbccdd2` |
| `finding_closure.json` | `78bfeabce99ac79f17b1414f6a466295b568f2db98e698ca1ba17f590b41d043` |
| `artifact_roots.json` | `8ca331b41a135e8f3057b3b7e17be1462b0a5998d5444f10a3996a72f55fed53` |
| `checksums.sha256` | `85d25d453ac1ec4ff6006c465f8428c2088fec6a60468c1576cd3f304ed6a79f` |
| `migration_report.json` | `b30014bca665363d902c55e8dc07f577a70a11f7fc4050e40910e61e2aaf6d64` |
| `legacy_to_revision_mapping.json` | `660d9da17c1443ad4839e973ebdd2fa6ee9c61a4dc75472abcfa877493261748` |

The new manifest states that these are negative provenance: 45 LOW-confidence
candidate sources with no verified rank/score, not a current meta snapshot.
It also references, without copying, the non-O6 historical snapshot SHA
`17b694e48ce605161c5491c7cde34dbdfc31f4c1b625c3c90a51e0aecac2b188`.

Verify source and destination from independently opened bounded snapshots.
Publish the migration manifest before deleting any source directory.

## Migrate live code dependencies

1. Create a neutral, reviewed native-agent process boundary outside
   `mage_ptcg.opponents`, with a fresh-process-per-game lifecycle, bounded JSON
   IPC, stderr limits, process-group timeout/termination, safe archive snapshot
   and extraction, exact member hashes, and cleanup.
2. Replace the live import in `src/mage_ptcg/offline_scaleup/pipeline.py` for
   `NativeAgentWorker`, `prepare_native_participant`, and
   `cleanup_native_participant` with that neutral module.  Preserve the current
   Task3B edits in the same file.
3. Update `scripts/offline_scaleup/prepare_handoff.py` and its reuse map.
4. Move the non-O6 behavioral coverage needed by
   `tests/test_offline_scaleup_pipeline.py`; replace the old
   `o6-meta-opponent-lab-v0` fixture name with a neutral fixture.
5. Rewrite `src/mage_ptcg/opponents/__init__.py` so it exposes only retained
   current modules.

Retain in place for this cleanup:

- `src/mage_ptcg/opponents/synthetic_stress_v1.py`
- `src/mage_ptcg/opponents/lineage_v2.py`
- `src/mage_ptcg/opponents/privacy_ipc_v2.py`

They contain no O6 contract and still have current consumers.  Moving their
namespace is a separate refactor.

## Repair documentation and quarantine

- Replace O6 status/link sections in `docs/status/current_status.md` and
  `docs/status/handoff.md` with the new meta-specialist state and migration
  manifest.
- Retarget the comment in `scripts/run_submitted_asset_lifecycle.py`.
- Preserve `.gitignore` privacy-quarantine behavior for invalid private v3
  evidence even when its O6 wording/tombstone is removed.
- Do not delete O2 or files whose name merely begins with numeric `06`.
- Do not rewrite embedded historical text inside the O1 checkpoint bundle.

## Repository deletion manifest

After the migration tests pass, delete exactly:

- directories `docs/evidence/o6-opponent-intelligence-v1/`,
  `docs/evidence/o6-opponent-intelligence-v2/`,
  `docs/evidence/o6-opponent-intelligence-v4/`, and
  `docs/evidence/o6-public-sources-v1/`;
- the ten exact O6 evidence/design/runbook/plan files listed in
  `/tmp/o6-cleanup-inventory.md`;
- `scripts/run_o6_team_league.py`;
- the 15 legacy modules under `src/mage_ptcg/opponents/` listed in that
  inventory, excluding `__init__.py` and the three retained modules;
- all 11 `tests/opponents/` files plus
  `tests/test_o6_integrity_tamper.py`, `tests/test_run_o6_team_league.py`, and
  `tests/test_team_reference_v1.py`.

Expected direct repository deletion count is 453 tracked files.  Before
applying the delete patch, regenerate the tracked-file list and require an
exact match; drift blocks deletion.

## External handoff deletion manifest

After the eight-file migration verifies, delete only the following exact
top-level directories under `/home/bfe-lab-ono/kaggle/handoff-artifacts/`:

```text
o6-deck-family-taxonomy-v1
o6-deck-family-taxonomy-v1-independent-audit
o6-deck-family-taxonomy-v1-remediated
o6-deck-family-taxonomy-v2-official-card-db
o6-gemini-intelligence-20260721
o6-live-recovery-20260721_214054
o6-meta-opponent-lab-v0
o6-post-integration-final-audit
o6-public-collector-prototype-v1
o6-public-collector-prototype-v1_backup
o6-public-live-acquisition-gemini-independent-reaudit
o6-public-live-acquisition-independent-audit
o6-public-live-acquisition-v1
o6-public-live-acquisition-v1-gemini-remediation
o6-public-source-corpus-v1
o6-public-source-corpus-v1_backup
o6-public-trajectory-final-targeted-reaudit
o6-raw-trajectory-targeted-reaudit
o6-safe-squash-integration
o6-stage-d1-v2-handoff
o6-stage-d1-v2-official-taxonomy-handoff
o6-stage-d1-v2-taxonomy-import-handoff
o6-team-population-independent-audit
```

Resolve and verify every target is an immediate child of the exact handoff
root, has an `o6-` basename from this closed list, and is not a symlink before
deletion.  No glob or unresolved variable is a deletion target.  Record the
pre-delete byte total, exact list, deletion time, and post-delete absence.

## Verification gates

Before deletion:

- migrated eight destination hashes equal the table;
- neutral native-worker focused tests and offline-scaleup tests pass;
- no retained source imports a module in the repository deletion manifest;
- documentation has no live link to a deletion target;
- regenerated deletion list/count equals the sealed manifest.

After deletion:

- `git diff --check`;
- focused offline-scaleup, synthetic stress, opponent lineage, and privacy IPC
  suites;
- full collection and full regression;
- compileall and AST import closure;
- exact residual `o6` audit, manually classifying the preserved quarantine,
  O1 historical archive, O2, and numeric-06 false positives;
- external targets absent and migrated evidence still hash-valid.

No commit, push, PR, upload, Kaggle submission, active-slot mutation, or broad
cleanup outside this closed manifest is part of the retirement.
