# Lane E — External cell-tracking method feasibility (primary-source audit)

Date: 2026-08-21 (JST). Author: Claude Lane E (`claude/e-external`).
Scope: decide, from primary sources only, whether any established cell-tracking method
can be plugged into this project's point-detection pipeline, and kill the ones that
cannot. No installs, no weight downloads, no container writes were performed — this is a
read-only research pass. Every claim below is cited to a repo file, PyPI/arXiv metadata
endpoint, or a peer-reviewed paper; anything not directly verified is marked `UNVERIFIED`.

A prior, independent pass exists at
`scratch/strong-baseline-v1/biohub-cell-tracking-during-development/docs/results/multi_method_feasibility_ja.md`
(Codex worktree, read-only, dated 2026-08-20, Japanese). It reached similar conclusions
for Trackastra/Ultrack/HOCT/Linajea/DeepCenter without executing installs. This document
independently re-verifies those claims against primary sources, adds exact license/size/
dependency evidence that document did not capture, and adds candidates it did not cover
(laptrack, EmbedTrack, Cell-TRACTR, BiologicalNeeds/KIT-Sch-GE, ByteTrack). Where the two
documents agree, that is corroboration from independent primary-source checks, not copying.

## 0. What this project actually has (verified against the vendored upstream)

Source: `artifacts/strong_baseline_v1/upstream/src/tracking_cellmot/models/temporal_unet.py`,
`.../README.md` (Codex worktree, read-only, pinned commit `075fc5f5a52d11077f9dc2b074644618f26939e2`).

- Detection: `TemporalUNet3D` produces a per-voxel detection heatmap; cell centres are
  recovered by **local-max suppression** — a point, not a labelled region.
- Linking: per-node features are **pooled at the detected centre points** and fed to
  `SimpleNodeTransformer`.
- The upstream README states the prediction graph is literally point nodes: *"A prediction
  graph is just a `tracksdata` `InMemoryGraph` of cell detections linked across time"* with
  nodes carrying only `{t, z, y, x}` — no shape, no mask, no region.

**Conclusion: the coordinator's framing is correct.** This project has point detections
only, never instance segmentation. Every candidate below is judged against that fact, not
against what would be convenient.

---

## 1. Trackastra — `weigertlab/trackastra`

| Question | Answer | Evidence |
|---|---|---|
| Input contract | `Trackastra.track(imgs, masks, mode=...)`. Docs: *"The input to Trackastra is a sequence of images and their corresponding cell (instance) segmentations."* Point detections are **not** an accepted input. | github.com/weigertlab/trackastra README, fetched directly |
| 3D + time | Native. Shapes documented as `time,(z),y,x` — `(z)` optional, so both 2D and 3D are first-class. | same |
| Division | Yes. Three linking modes: `greedy` (with division), `greedy_nodiv` (no division), `ilp` (slower, more accurate). | same |
| 3D checkpoint | `ctc` — `dimensionality: [2, 3]`, description *"successor of the winning model of the ISBI 2024 CTC generalizable linking challenge"*, trained on all CTC 2D+3D datasets with GT/ERR_SEG. URL `https://github.com/weigertlab/trackastra-models/releases/download/v0.3.0/ctc.zip`. | `trackastra/model/pretrained.json`, raw-fetched |
| Checkpoint size | **101,957,234 bytes (~97.2 MiB)**, confirmed via `curl -I` HEAD request (no download performed). | direct HEAD request, this session |
| Code licence | BSD-3-Clause | repo README/PyPI |
| Checkpoint licence | BSD-3-Clause, `Copyright (c) 2024, weigertlab` — full text fetched from `raw.githubusercontent.com/weigertlab/trackastra-models/main/LICENSE`. No NC/ND clause at the repo level. Residual open question: whether the *Cell Tracking Challenge training data* itself carries redistribution terms that bind derived weights — **not checked**, flag before Kaggle-dataset staging. | trackastra-models `LICENSE`, raw-fetched |
| Python / deps | `requires_python >= 3.10`. Full `requires_dist` (PyPI JSON): `numpy, scipy, pandas, scikit-image, torch, torchvision, pyyaml, edt, joblib, lz4, imagecodecs>=2023.7.10, chardet, dask, numba, geff>=1, tqdm, requests, psutil, platformdirs, motile[gurobi]>=1.0, ilpy>=0.6`. | pypi.org/pypi/trackastra/json |
| GPU | Optional. Docs literally warn *"slow on CPU!"* but CPU is a supported `device=` value. | repo README |
| **Container reality check** | This project's own `uv.lock` (MAIN, read-only) **already resolves** `ilpy==0.6.0`, `numba==0.67.0`, `geff==1.3.0.1.2`, plus `numpy`, `scipy`, `scikit-image`, `networkx`, `pydantic`, `click`, `typing-extensions`, `zarr` — pulled in transitively via the existing `tracksdata` dependency. That means Trackastra's heaviest, most ARM-risky deps (`ilpy`, a SCIP-backed C++ ILP wrapper; `numba`, a JIT compiler) are **proven to already install on this exact ARM64/Python-3.11 container**, because they're already in the lock. Net new deps: `torchvision, pyyaml, edt, joblib, lz4, imagecodecs, chardet, dask, requests, psutil, platformdirs, motile, gurobipy, pandas`. All are mainstream, wheel-published packages; none are known ARM64-Linux gaps like the one found for Ultrack (§2). | `MAIN/uv.lock`, grepped directly |

**Verdict: CONDITIONAL feasible, adapter required.** Real 3D checkpoint, permissive
licence, division support, dependency footprint that this container has already partly
proven. The blocker is purely the input contract (§4).

---

## 2. Ultrack — `royerlab/ultrack`

| Question | Answer | Evidence |
|---|---|---|
| Input contract | `Tracker.track(labels=..., foreground=..., contours=..., scale=..., vector_field=...)`. Accepts **either** a single integer `labels` array **or** separate `foreground`+`contours` maps (a helper `ultrack.utils.labels_to_contours()` converts labels → the internal representation). Point detections are not an accepted input; a labelled/probability volume is required either way. | royerlab.github.io/ultrack/api.html, fetched directly |
| 3D + time | Native. *"Supports 2D, 3D, and multichannel datasets"*; `labels` shape documented as `(T, (Z), Y, X)`. | same |
| Division | **Yes, confirmed** (this corrects the ambiguity in the prior Japanese survey, which left it unconfirmed). Output schema uses `track_id` + `parent_track_id` (parent's `track_id` after division, `-1` if none); `to_networkx()` exports parent→child edges directly. | royerlab.github.io/ultrack docs + README, corroborated via search |
| Checkpoint | None required — classical multi-hypothesis segmentation + ILP-solve method, not a learned tracker. An optional `ml` extra (`scikit-learn`, `catboost`) exists for feature-based edge weighting but is not mandatory. | PyPI `requires_dist` |
| Code licence | BSD-3-Clause | repo LICENSE |
| Python | `>=3.11,<3.14` — compatible with this project's `3.11` in principle. | PyPI JSON |
| **Dependency reality — HARD BLOCKER on this container** | `higra>=0.6.10` is an **unconditional, non-optional** core dependency (`requires_dist`, no `extra ==` marker). Direct query of `pypi.org/pypi/higra/0.6.13/json` (latest version, the one that would be installed) lists wheel files for `cp311` on **`macosx_10_9_x86_64`, `macosx_11_0_arm64`, `manylinux_2_27_x86_64`, `win_amd64` — and no `manylinux*_aarch64` (Linux ARM64) wheel, and no source distribution**. This container is Ubuntu 24.04 on an **ARM64** host (confirmed: `uname -m` → `arm64` on host, `docker version --format '{{.Server.Arch}}'` → `arm64`). `pip install higra` therefore has **no installable artifact on this platform** — this is a hard install-time failure, not a "heavy but doable" caveat. | `pypi.org/pypi/higra/0.6.13/json`, fetched directly this session; host/container arch checked via `uname -m` and `docker version` |
| Other dependency-bloat flags (also unconditional core deps, would matter even on x86_64) | `napari>=0.4.18` + `magicgui` + `qtawesome` + `qtpy` (full Qt GUI stack), `fastapi`+`uvicorn`+`websockets`+`httpx` (web server stack), `sqlalchemy`+`psycopg2-binary` (DB driver needing `libpq`), `pydot` (needs system `graphviz` binary), `gurobipy` (proprietary solver; `mip`/CBC also present as an open alternative). This reflects Ultrack's design centre — an interactive napari plugin with a Postgres-backed job queue — not a lean batch library. | pypi.org/pypi/ultrack/json `requires_dist`, fetched directly |

**Verdict: NO-GO on this container as currently specified.** Even setting aside the input-
contract adapter problem (same class of issue as Trackastra/HOCT), `ultrack` cannot be
`pip install`-ed on ARM64 Linux today because of the `higra` wheel gap. This is falsifiable
and should be re-checked (`pip download higra==0.6.13 --no-deps --platform manylinux2014_aarch64 --python-version 311 --only-binary=:all:` — will fail with "no matching distribution") rather than assumed permanent, but as of this audit it is a real blocker, independent of any feature judgement about Ultrack itself.

---

## 3. HOCT — `royerlab/hoct`

| Question | Answer | Evidence |
|---|---|---|
| Input contract | CLI: `hoct track <IMAGES> <SEGMENTATION> -o <OUTPUT.geff>`. `<SEGMENTATION>` must be *"one integer label per object"*, same shape as `<IMAGES>`. Point detections are not accepted. | repo README, fetched directly |
| 3D + time | Yes — *"(T, Y, X) or (T, Z, Y, X) integer array"*, with a worked CTC 3D example (12 frames). | same |
| Division | The CLI/README text does not surface a division flag, **but the underlying paper is explicitly about division-aware tracking**: arXiv:2607.11754 ("Higher-Order Cell Tracking Transformer", Bragantini, Theodoro, Royer) abstract: *"Reconstructing lineages ... requires linking cell detections across time, **including through cell divisions**... cell divisions entangle distinct lineage paths in the node embedding space"* — division handling is the paper's core contribution, evaluated on CTC **and a bacteria division benchmark**. Output is `.geff`, which natively represents 1-parent→2-children topology. | arxiv.org/abs/2607.11754, fetched directly |
| Checkpoint | `general_v0`, auto-downloaded from GitHub releases, registry in `src/hoct/_models.py`. **Size: 25,510,490 bytes (~24.3 MiB)**, confirmed via HEAD request. Training-data domain / 2D-vs-3D coverage of this specific checkpoint: **not confirmed** — flag before relying on it for a 3D-only claim. | README + direct HEAD request this session |
| Code licence | **MIT** — full text fetched from `raw.githubusercontent.com/royerlab/hoct/main/LICENSE`: `Copyright (c) 2026 Jordao Bragantini and the HOCT contributors`. | raw LICENSE fetch, this session |
| A licence trap worth naming explicitly | The arXiv **abstract page** lists the paper's distribution licence as **CC BY-NC-ND 4.0**. That licence governs the **PDF/manuscript text on arXiv** (arXiv's default when the author doesn't pick otherwise) — it does **not** override the repository's own MIT `LICENSE` file, which governs the code and, by ordinary convention, the checkpoint hosted in the same repo's releases. I checked both independently rather than assuming one from the other; they genuinely differ, and conflating them would produce a false "unusable" or false "clear" verdict depending on which one you read. Still open: there is no separate model card asserting the checkpoint itself is MIT rather than "all rights reserved" — reasonable default assumption, not confirmed to model-card rigor. | both fetched independently, this session |
| Maturity | Paper is roughly one month old at the time of this audit (arXiv ID `2607.xxxxx` = July 2026); single-paper repo, no track record comparable to Trackastra's CTC-2024 win or Ultrack's Nature Methods 2025 publication. Higher execution risk simply from being new (undiscovered platform bugs, thinner community, no ARM-tested reports). | arXiv ID date convention |
| Deps | `bioio` extra needed for the `track` CLI's I/O; core inference otherwise CPU-fallback, no CUDA-only op confirmed. Full dependency list **not independently verified** beyond this — `UNVERIFIED`, would need a `pyproject.toml` read before staging. | repo README |

**Verdict: CONDITIONAL feasible, same adapter problem as Trackastra, lower priority.**
Genuinely division-aware per the paper, permissive code licence, small checkpoint — but
immature (weeks old), unconfirmed full dependency footprint, and unconfirmed checkpoint
dimensionality coverage. Rank below Trackastra until those gaps close.

---

## 4. laptrack — `yfukai/laptrack` (found during this audit; not in the prior survey)

This is the one candidate that does **not** need a segmentation adapter at all, because it
was built for exactly this project's input shape: point/centroid detections.

| Question | Answer | Evidence |
|---|---|---|
| Input contract | **Point coordinates directly**, as a pandas DataFrame. Worked example: `lt.predict_dataframe(regionprops_df, coordinate_cols=["centroid-0", "centroid-1"], only_coordinate_cols=False)`. `coordinate_cols` is an arbitrary-length list — the same call form takes `["z","y","x"]`. No mask, no image, no shape/texture feature is required; the input the project already produces (`t,z,y,x` centres) is a first-class input, not an adapter target. | laptrack.readthedocs.io cell_segmentation example, fetched directly |
| 3D + time | Demonstrated in the peer-reviewed paper on a 3D Cell Tracking Challenge dataset (`Fluo-N3DH-CE`), with the authors stating the cost-function design is dimension-agnostic (default cost = squared Euclidean distance over an arbitrary-length coordinate vector). | Bioinformatics 39(1):btac799 (2023), fetched directly |
| Division | **Yes, native**, via `splitting_cost_cutoff` (aliased `splitting_cutoff` in some examples). `predict_dataframe()` returns `(track_df, split_df, merge_df)` — `split_df` is exactly the 1-parent→N-children event list the competition's `division_jaccard` term needs. Merging is also supported (not needed here, harmless). Mathematical model (from the paper): a two-stage LAP, frame-to-frame cost `L_ff`, then a segment-connecting stage `L_sc` with explicit splitting-index-pair and merging-index-pair cost terms `s_αβ`, `m_αβ`. | docs + paper Methods, both fetched directly |
| Checkpoint | **None. There is nothing to download, licence, or stage as a Kaggle dataset.** It is a classical LAP solver (a modern, tunable-cost re-implementation of the Jaqaman et al. 2008 algorithm used by TrackMate/u-track), not a learned model. This directly answers the brief's "TrackMate/LAP" candidate line: laptrack's own README states it was *"inspired by TrackMate."* | paper + README |
| Code licence | BSD-3-Clause. Paper: Bioinformatics, **CC BY 4.0**, DOI `10.1093/bioinformatics/btac799` (open access, peer-reviewed, citable). | PyPI + journal page, both fetched directly |
| Python / deps | `requires_python: <3.15,>=3.10`. Full `requires_dist`: `click, networkx, numpy, pandas, pooch, pydantic, scikit-image, scikit-learn, scipy, typing-extensions`; optional `geff` extra (`>=1.1.4.1.1`) adds direct GEFF export. **No torch dependency at all** — fully decoupled from the project's CPU-only torch constraint. | pypi.org/pypi/laptrack/json, fetched directly |
| **Container reality check** | Cross-checked every dependency against `MAIN/uv.lock`: `numpy, scipy, scikit-image, networkx, pydantic, click, typing-extensions, geff` are **already resolved** on this exact ARM64/Python-3.11 container (transitively, via `tracksdata`). Only `pandas`, `scikit-learn`, and `pooch` would be net-new — three extremely mainstream, universally-wheeled packages, none with any known ARM64-Linux gap. This is the lowest-risk dependency profile of every candidate examined, Trackastra included. | `MAIN/uv.lock`, grepped directly |

**Verdict: GO.** Exact input-contract match (no adapter, no synthetic data invented),
native 3D, native division, zero licensing/redistribution risk, smallest and safest
dependency delta of any candidate. See §7 for the concrete smoke-test plan.

---

## 5. Fast kills (primary-source-verified, one table)

| Candidate | Verdict | Reason (cited) |
|---|---|---|
| **EmbedTrack** (`git.scc.kit.edu/kit-loe-ge/embedtrack`, arXiv:2204.10713, IEEE Access 2022) | **NO-GO** | Benchmarked exclusively on the 9 **2D** Cell Tracking Challenge datasets; no 3D variant published. Hard kill per the brief's 3D+time rule. |
| **BiologicalNeeds / KIT-Sch-GE mitosis tracker** (`TimoK93/BiologicalNeeds`, IEEE TMI 2025) | **NO-GO** | Explicitly *"2D data sets from the Cell Tracking Challenge"* — 2D-only, despite being genuinely mitosis-aware (EmbedTrack + Multi-Hypothesis Tracking). Additionally tested only against `PyTorch 1.13 / CUDA 11.7`; no CPU path documented. Double kill: dimensionality and device. |
| **Cell-TRACTR** (`gitlab.com/dunloplab/Cell-TRACTR`, PLOS Comp Biol 2025) | **NO-GO** | Evaluated only on bacterial mother-machine (1D-constrained) and 2D mammalian-cell movies; no 3D volume processing discussed anywhere in the paper. Checkpoints exist on Zenodo (`zenodo.org/records/14509424`) but dimensionality kills it before licence matters. |
| **ByteTrack** (`FoundationVision/ByteTrack`, ECCV 2022, MIT licence — repo/licence reconfirmed live this session) | **NO-GO** | Not a cell-tracking method: a 2D bounding-box multi-object tracker for pedestrian/vehicle video (MOT17/MOT20/BDD100K), pairing an arbitrary 2D detector (YOLOX in the paper) with Kalman-filter motion + confidence-tiered IoU association ("BYTE"). No 3D concept, no division/mitosis concept — object identities in MOT are expected to be conserved 1:1, never to split. Domain mismatch is definitional, not a detail to verify further. |
| **CellTrackFormer** (named in the brief) | **NOT FOUND** | No repository, paper, or package by this exact name surfaced under that name after targeted search. Closest name-adjacent hits: **Cell-TRACTR** (above, killed on dimensionality) and **Bayesian-Transformer-Cell-Tracking** (next row). Do not let a plausible-sounding name stand in for a real citation — reporting "not found" here rather than substituting a lookalike. |
| **Bayesian-Transformer-Cell-Tracking** (`NabaviLab/bayesian-transformer-cell-tracking`, MICCAI paper) | **BLOCKED / insufficient evidence** | Genuinely division-aware by design (*"performs higher-order (triplet) graph matching across frames... handles divisions"*), MIT licence — but dimensionality (2D vs 3D) is not stated anywhere in the repo's visible content, no pretrained checkpoint is published, and the repo shows **0 stars / 0 forks** — an early, unvalidated paper-release with no community signal. Not ruled out on principle, but there isn't enough here yet to rank it; would need a full `pyproject`/paper Methods read before it earns a real verdict. |
| **Linajea** (`funkelab/linajea`) | **NO-GO** (per prior Codex survey, re-read not re-derived) | No generic pretrained checkpoint; training-first pipeline (`01_train→04_solve`); legacy `gunpowder/daisy/funlib/MongoDB` stack with unconfirmed Python 3.11 / Zarr-3 compatibility. Not independently re-verified this session — citing the existing finding in `multi_method_feasibility_ja.md` rather than re-doing the work, since nothing in this session contradicts it. |

---

## 6. The adapter question — a real opinion, not a hedge

The brief asks directly: is *"official centre detections → pseudo instance masks (spheres
of radius r µm) → Trackastra → GEFF"* legitimate, or does the pseudo-mask step destroy the
signal Trackastra (and by the same argument, Ultrack and HOCT) relies on?

**My opinion: it is not legitimate as a way to produce a comparable score, and only
marginally legitimate as a plumbing smoke test.** Three concrete reasons:

1. **There is no radius that works.** Trackastra's node embeddings and Ultrack's whole
   hierarchical-contour-uncertainty mechanism are built to *use* real shape, size, texture,
   and boundary information to disambiguate crowded or dividing cells. A synthetic sphere
   of fixed radius `r` carries none of that — every cell looks identical except for
   position. Pick `r` too large and daughter cells from a real division (which start
   adjacent, sub-µm apart) fuse into one blob before the tracker ever sees two objects to
   link. Pick `r` too small and you've thrown away the one thing (relative size/shape) that
   would have helped in a crowded field. There is no setting that both preserves
   separability and supplies real shape signal, because the shape signal does not exist in
   a point detection — that is precisely what the coordinator flagged, and it verifies out.
2. **Any score you get back is a measurement of the adapter, not the tracker.** If
   Trackastra-on-spheres scores worse than harmonic_ilp, that is not evidence Trackastra is
   worse at this problem — it's evidence that uniform spheres are worse cell segmentations
   than dropping segmentation entirely, which nobody doubted. If it scores better, the
   result doesn't transfer to the real Kaggle test images (no masks there either), so it's
   not a leaderboard-relevant number either way. Either outcome is uninformative for the
   actual decision ("should we invest more in this method"), which is why the brief is
   right to treat "looks promising" here as expensive to get wrong.
3. **It is out-of-distribution for the checkpoint specifically.** The `ctc` checkpoint was
   trained on real CTC instance segmentations with genuine, varied morphology. Uniform
   spheres are a distribution the model never saw in training; a smoke test on them tells
   you the code path runs, not that the model's learned linking behaviour is engaged in any
   meaningful way.

Practical consequence: a pseudo-mask smoke test is worth doing **once, cheaply, non-GT,
explicitly labelled as a plumbing check** (import → checkpoint load → tiny inference →
GEFF reload — never a `final_score` claim). It should not be prioritized as a real
association-method candidate while laptrack exists and consumes the actual point
detections without inventing data. This is why laptrack outranks Trackastra in §7 despite
Trackastra having the flashier checkpoint.

---

## 7. Ranked go/no-go table

| Rank | Candidate | Verdict | One-line reason |
|---|---|---|---|
| 1 | **laptrack** | **GO** | Exact input-contract match (point centres, no adapter), native 3D + native division, zero checkpoint/licence risk, smallest dependency delta (3 new mainstream packages, rest already in this container's lockfile). |
| 2 | **Trackastra (`ctc` checkpoint, greedy mode)** | **CONDITIONAL-GO, diagnostic only** | Real 3D checkpoint + division support + permissive licence + most of its dependency footprint already proven on this container, but needs a pseudo-mask adapter whose signal-destruction problem is argued in §6 — usable only as a labelled, non-comparable plumbing smoke test. |
| 3 | **HOCT (`general_v0`)** | **CONDITIONAL, lower priority** | Same adapter problem as Trackastra, genuinely division-aware per its paper, MIT + small checkpoint — but weeks-old repo, unconfirmed full dependency list, unconfirmed checkpoint dimensionality coverage. |
| 4 | **Ultrack** | **NO-GO (this container)** | Core dependency `higra` publishes no Linux ARM64 wheel and no sdist for the latest version (verified via PyPI JSON) — `pip install ultrack` cannot succeed on this ARM64 container today, independent of the same input-contract adapter problem. |
| 5 | **Linajea** | **NO-GO** | No generic pretrained checkpoint; training-first pipeline; legacy dependency stack (prior survey finding, re-cited not re-derived). |
| 6 | **EmbedTrack** | **NO-GO** | 2D-only, no 3D variant exists. |
| 7 | **BiologicalNeeds / KIT-Sch-GE** | **NO-GO** | 2D-only and CUDA-only (no documented CPU path) — double kill. |
| 8 | **Cell-TRACTR** | **NO-GO** | 2D-only (mother-machine + 2D mammalian cells). |
| 9 | **ByteTrack** | **NO-GO** | Wrong domain entirely: 2D bounding-box pedestrian/vehicle MOT, no division concept. |
| — | **CellTrackFormer** | **NOT FOUND** | No repo/paper by this name located; do not substitute a lookalike. |
| — | **Bayesian-Transformer-Cell-Tracking** | **BLOCKED / insufficient evidence** | Division-aware by design, but dimensionality unconfirmed, no checkpoint, 0-star repo. |

---

## 8. Smoke-test plan for the top candidate (laptrack)

All commands below are written for execution **after** the coordinator releases installs
(Codex's job is still live per BRIEF §0.1/A5 at the time of writing) — none have been run.
Every step avoids GT except the final, separate evaluation phase, per BRIEF §0.4.

### Tier 0 — pure plumbing, synthetic data, no container dependency at all (~seconds)

Purpose: prove the library and the GEFF round-trip work before touching any real cache.

```bash
docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}'   # gate: used mem < 6.0 GiB
docker compose exec -T biohub sh -lc \
  'cd /workspace/biohub-cell-tracking-during-development/scratch/claude/e-external/biohub-cell-tracking-during-development && uv add "laptrack[geff]"'   # QUEUED-HEAVY (dependency change) — see §9
docker compose exec -T biohub sh -lc \
  'cd /workspace/biohub-cell-tracking-during-development/scratch/claude/e-external/biohub-cell-tracking-during-development && uv run python - <<PY
import pandas as pd
from laptrack import LapTrack

# synthetic 3-frame, 3D point set with one designed division at frame 1->2
df = pd.DataFrame([
    {"frame": 0, "z": 0.0, "y": 0.0, "x": 0.0},
    {"frame": 1, "z": 0.0, "y": 0.5, "x": 0.0},
    {"frame": 2, "z": 0.0, "y": 1.0, "x": -1.0},  # daughter A
    {"frame": 2, "z": 0.0, "y": 1.0, "x": 1.0},   # daughter B
])
lt = LapTrack(track_cost_cutoff=9.0, splitting_cost_cutoff=9.0)
track_df, split_df, merge_df = lt.predict_dataframe(
    df, coordinate_cols=["z", "y", "x"], only_coordinate_cols=False,
)
assert len(split_df) == 1, f"expected 1 synthetic division, got {len(split_df)}"
print("OK", track_df.shape, split_df.shape, merge_df.shape)
PY'
```

Expected runtime: a few seconds. Success = the assert passes and a GEFF can be written
from `track_df`/`split_df` and re-read by this project's existing official-metrics loader
(`src/biohub/official_metrics`) without a schema error. Drop laptrack's 3D claim (not the
whole candidate) if `coordinate_cols=["z","y","x"]` silently mis-orders axes.

### Tier 1 — real, non-GT point detections, reusing Codex's already-materialized cache (read-only)

Per BRIEF ADDENDUM §A3/A4, a full 100-frame detector-fixed point cache for
`44b6_0113de3b` already exists (read-only, in Codex's worktree) and cache-only association
experiments are cheap (~116 s wall for four methods per §A4). laptrack would slot in as a
**fifth association method on the same fixed cache** — directly comparable to
`official_ilp` / `harmonic_ilp` / `mutual_confidence` / `motion_gated` with no re-running
of the forbidden detector step.

```bash
docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}'   # gate again, immediately before running
docker compose exec -T biohub sh -lc \
  'cd /workspace/biohub-cell-tracking-during-development/scratch/claude/e-external/biohub-cell-tracking-during-development && \
   uv run python scripts/run_detector_fixed_race.py associate --method laptrack \
     --cache artifacts/detector_fixed_race/full_auto/cache/44b6_0113de3b \
     --output-root artifacts/detector_fixed_race/laptrack_smoke'   # script name illustrative — the real
     # entrypoint lives in Codex's src/biohub/detector_fixed_race/, not yet mirrored into this
     # worktree; adapt to whatever CLI Codex's module actually exposes at execution time.
docker compose exec -T biohub sh -lc \
  'cd /workspace/biohub-cell-tracking-during-development/scratch/claude/e-external/biohub-cell-tracking-during-development && \
   uv run python -m biohub.official_metrics.evaluate \
     --pred artifacts/detector_fixed_race/laptrack_smoke/44b6_0113de3b.geff \
     --gt ../../../../../../data/train/44b6_0113de3b.geff'   # GT touched only here, evaluation-only
```

Expected runtime: comparable to or faster than `blob_lap`'s measured 33 s (§ existing
`multi_method_benchmark_race.md`) — laptrack solves one polynomial-time LAP per adjacent
frame pair over ~260 nodes/frame on average (26k nodes / 100 frames), no ILP-scale solve
required for the base `greedy`-equivalent mode. Ceiling estimate: under 2 minutes CPU;
flag and investigate rather than let it run unbounded if it exceeds ~10 minutes.

**Counts as success:**
- Produces a schema-valid prediction GEFF, evaluates without crashing in the vendored
  official metric.
- `edge_tp > 0` against the 50-edge GT (i.e., a non-degenerate result, same order of
  magnitude as the existing 42-48 TP spread across `official_ilp`/`harmonic_ilp`/
  `mutual_confidence`/`motion_gated`).
- `division_jaccard` becomes **non-null** on the one validation-panel sample with a real
  division (`44b6_12dfb391`, per BRIEF §3.7) when `splitting_cost_cutoff` is tuned — this
  is the first candidate in the whole project's history with a plausible path to a
  non-null division score, which is worth stating plainly.

**Makes me drop it:**
- `splitting_cost_cutoff` cannot be tuned to recover the one known division without also
  manufacturing false splits elsewhere in the panel (division precision/recall unusable) —
  laptrack still stands as a plain LAP linker, just loses its one differentiating feature.
- GEFF export schema mismatches what `official_metrics` expects badly enough to need a
  nontrivial custom converter (would raise real adapter cost, though this looks unlikely
  given the project already hand-builds `tracksdata` GEFF graphs elsewhere).
- Runtime blows past ~10 minutes CPU for the full 100-frame graph — investigate before
  treating it as viable at panel scale (5 samples).

---

## 9. QUEUED-HEAVY

None of these were run. Container-resource and no-install rules were respected throughout
this audit (verified `docker stats` once at the start of this session: `biohub-dev`
`~4.8 GiB / 7.651 GiB`, no exec calls made into the container afterward — this audit needed
none).

1. **`uv add "laptrack[geff]"`** inside this worktree (container), then Tier 0 + Tier 1
   commands in §8. Est. total runtime: a few minutes including dependency resolution.
2. Diagnostic-only, explicitly non-comparable: `uv add "trackastra==0.5.5"`, then a 2–3
   frame **non-GT** pseudo-mask smoke (`mode="greedy"`, `device="cpu"`, `n_workers=0`) per
   §6 — never promote its score to a leaderboard comparison.
3. Verification-only, cheap, no install: confirm the `higra` ARM64 gap is still real before
   fully retiring Ultrack: `docker compose exec -T biohub sh -lc 'pip download higra==0.6.13 --no-deps --platform manylinux2014_aarch64 --python-version 311 --only-binary=:all: -d /tmp/probe'` — expected to fail with "no matching distribution"; if it ever succeeds, Ultrack's verdict in §7 should be revisited.
4. **Not queued, flagged only:** downloading `ctc.zip` (~97.2 MiB, BSD-3-Clause,
   `github.com/weigertlab/trackastra-models`) or `general_v0.pt` (~24.3 MiB, MIT,
   `github.com/royerlab/hoct`) as a Kaggle dataset requires explicit user approval — this
   is a download decision, not a research one, and is out of scope for this lane to
   initiate unilaterally.

---

## 10. Primary sources cited (for traceability)

- github.com/weigertlab/trackastra (README, fetched directly)
- github.com/weigertlab/trackastra/blob/main/trackastra/model/pretrained.json (raw-fetched)
- github.com/weigertlab/trackastra-models (repo + raw `LICENSE`, fetched directly)
- pypi.org/pypi/trackastra/json (fetched directly)
- github.com/royerlab/ultrack (README, fetched directly) + royerlab.github.io/ultrack/api.html
- pypi.org/pypi/ultrack/json (fetched directly)
- pypi.org/pypi/higra/0.6.13/json (fetched directly — the ARM64-gap evidence)
- github.com/royerlab/hoct (README, fetched directly) + raw `LICENSE`
- arxiv.org/abs/2607.11754 — "Higher-Order Cell Tracking Transformer" (Bragantini, Theodoro, Royer), fetched directly
- github.com/yfukai/laptrack + laptrack.readthedocs.io (fetched directly)
- academic.oup.com/bioinformatics/article/39/1/btac799/6887138 — LapTrack paper, Bioinformatics 2023, CC BY 4.0 (fetched directly)
- pypi.org/pypi/laptrack/json (fetched directly)
- git.scc.kit.edu/kit-loe-ge/embedtrack, arXiv:2204.10713 (search-corroborated)
- github.com/TimoK93/BiologicalNeeds (README, fetched directly)
- journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1013071 — Cell-TRACTR (fetched directly)
- github.com/FoundationVision/ByteTrack (search-corroborated, license reconfirmed live)
- github.com/NabaviLab/bayesian-transformer-cell-tracking (README, fetched directly)
- `MAIN/uv.lock`, `MAIN/pyproject.toml` (this repo, read-only, grepped directly)
- `scratch/strong-baseline-v1/.../artifacts/strong_baseline_v1/upstream/` — vendored
  `royerlab/kaggle-cell-tracking-competition` at pinned commit
  `075fc5f5a52d11077f9dc2b074644618f26939e2` (read-only, this repo)
- `scratch/strong-baseline-v1/.../docs/results/multi_method_feasibility_ja.md` — prior
  independent survey (read-only, this repo, re-verified not re-derived)
