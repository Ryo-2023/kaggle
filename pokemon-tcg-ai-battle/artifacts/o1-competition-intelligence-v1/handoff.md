# O1 Competition Intelligence Framework — Handoff (Slice 0–4)

This is a portable, self-contained summary of this work package. The canonical
source of truth remains the in-repo docs listed below; this file exists so the
artifact bundle is readable without checking those out separately.

## What this delivers

`mage_ptcg.competition_intelligence`:

- **O1-0** (Guardrails) and **O1-1** (Foundation): data contracts,
  permissions, provenance, a content-addressed raw archive with quarantine, a
  run manifest/lock, a rebuildable SQLite catalog, a config schema.
- **O1-2** (Local Replay Normalization and Analytics): a read-only,
  tolerant reader for existing `offline_training` runs; Episode/Decision
  normalization reusing Stable ActionKey verbatim; deterministic phase
  segmentation; deck/policy/joint fingerprints; Wilson-based matchup
  statistics; failure hypotheses; high-information decision selectors.
- **O1-3** (Knowledge Registry): raw human-text note archiving; Claim
  Bundle (YAML/JSON) import; lifecycle-validated `KnowledgeClaim` registry;
  deterministic contradiction detection; self-verifying immutable Knowledge
  Snapshots.
- **O1-4** (Immutable Intelligence Snapshot and Offline Training
  Adapter): cutoff/permission/source-cap filtering; a composite-key
  group-aware split generalizing the repo's proven rank-by-hash technique;
  a machine-computed leakage audit; a **selection-only** adapter that never
  modifies `offline_training`'s own code.

A 10-command CLI (`doctor`, `ingest-local`, `rebuild-catalog`, `normalize`,
`analyze`, `import-knowledge`, `build-knowledge-snapshot`, `build-snapshot`,
`export-offline-dataset`, `report`) drives the full pipeline. See
`docs/plan/implementation/04_kaggle_competition_intelligence_and_joint_optimization_implementation_plan.md`
§23 (Slice 0–2) and §24 (Slice 2–4) for the full module map and reuse
decisions, and the two evidence documents
(`docs/evidence/o1-competition-intelligence-sidecar-slice0-1.md`,
`docs/evidence/o1-competition-intelligence-sidecar-slice2-4.md`) for test/review
evidence.

## What this does NOT deliver

- **O1-5** (Kaggle live/team-bundle adapters) and **O1-6** (Meta
  posterior/Opponent Surrogate/Promotion Report) are **not implemented**.
  This is a scope decision, not an oversight: attempting the entire O1
  framework with genuine design rigor, tests, and self-review in one or two
  sessions was judged likely to produce shallow/fake work, which the
  original mandate explicitly forbids. The continuation order is recorded
  in the implementation plan §23.3.
- Known data-source limitations (not implementation gaps): `winner`,
  `termination_reason`, per-decision latency, and result-to-go do not exist
  anywhere in `offline_training`'s collected data as of this session, so
  they are `None` in every normalized record rather than fabricated.

## Safety invariants preserved

- Champion/default: Rule Agent v0 (unchanged)
- Promotion: `NO_DECISION` (unchanged)
- `main.py`, `deck.csv`, Rule Agent code, Promotion-related code, Kaggle
  packaging/verification scripts: unchanged across all five commits (git blob
  SHA before/after identical — see `protected-files-before.json` /
  `protected-files-after.json`)
- `main.py`'s reachable import graph never includes
  `mage_ptcg.competition_intelligence`, `mage_ptcg.dataops`, `sqlite3`,
  `pandas`, or `sklearn` (verified via a clean subprocess test); the sidecar
  itself never imports `mage_ptcg.student` or
  `mage_ptcg.offline_training.dataset` either (both would transitively pull
  in the Student runtime module via `mage_ptcg.student`'s package `__init__`)
- `mage_ptcg.offline_training`'s own source code is untouched; the O1-4
  adapter integrates by generating a filtered copy of the existing
  `rule-bc-v1.jsonl` row format and handing it to the existing, unmodified
  `build_dataset()` — an operator who never uses a snapshot is byte-for-byte
  unaffected
- No Kaggle submission, no canonical-branch write, no remote push, no new
  remote branch, no Champion/Promotion change

## Integration procedure (not performed — for a human to run after Promotion Gate completes)

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle   # canonical repo
git fetch <this-worktree-or-a-pushed-branch>        # or apply the bundle below
git checkout -b feature/o1-competition-intelligence-sidecar feature/belief-guided-search
git cherry-pick 1b0a320   # feat(competition): add Competition Intelligence sidecar foundation (Slice 0-2)
git cherry-pick 4f8339a   # docs(o1): document Competition Intelligence sidecar architecture and Slice 0-2 evidence
git cherry-pick 9efe409   # chore(o1): add portable evidence/patch bundle for Slice 0-2 handoff
git cherry-pick 4e5a0a9   # feat(competition): implement O1-2/O1-3/O1-4
git cherry-pick 64d7542   # docs(o1): document O1-2 through O1-4 architecture, DEC-014, and Slice 2-4 evidence
# then run the focused + full test suite again on canonical before opening a PR
```

Note: `docs/status/current_status.md` and `docs/status/handoff.md` were also
touched by unrelated concurrent work on canonical (`c21ebed`, a Promotion
Gate submission-freeze commit) during this session — expect a merge conflict
on those two files specifically when cherry-picking the docs commits, and
resolve by keeping both sets of additions (they touch different sections).

Alternative, without network access to this worktree:

```bash
git bundle verify artifacts/o1-competition-intelligence-v1/checkpoint.bundle
git fetch artifacts/o1-competition-intelligence-v1/checkpoint.bundle HEAD:refs/heads/o1-competition-intelligence-import
git cherry-pick <same five commit hashes, from the imported branch>
```

Do not merge, rebase, or reset the canonical `feature/belief-guided-search`
branch directly; cherry-pick onto a new branch and open it for review.

## Commits (oldest first)

See `commit-list.txt` in this directory for the exact list (5 commits); all
appear in `implementation.patch` (single unified diff against base `6782e68`)
and as an ordered `git format-patch` series under `patches/`.

## Checkpoint tags (local-only, never pushed)

- `checkpoint/o1-competition-intelligence-v1` — preserved, unmoved; marks the
  O1-0/O1-1 (Slice 0–2) completion point at commit `9efe409`.
- `checkpoint/o1-competition-intelligence-v1-slice0-4` — new; marks this
  bundle's final commit (O1-0 through O1-4 complete).
