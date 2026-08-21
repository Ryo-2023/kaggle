# Lane D — Kaggle submission readiness

Date: 2026-08-21 (JST) · Branch `claude/d-submission`

This document separates **verified** facts (with the command or file that produced
them) from **assumed** ones. Nothing here has been submitted to Kaggle, and nothing
here constitutes permission to submit.

---

## 1. Competition requirements

### 1.1 Verified from the Kaggle API

Retrieved with `KaggleApi.competitions_list(search="biohub cell tracking")` inside
the `biohub-dev` container (Kaggle CLI 2.2.4), 2026-08-21.

| Requirement | Value | Status |
|---|---|---|
| Slug | `biohub-cell-tracking-during-development` | verified |
| Competition id | `136605` | verified |
| Category | `Research` | verified |
| **Submission type** | **`isKernelsSubmissionsOnly = True`** — Notebook-only Code Competition. A CSV cannot be uploaded directly. | **verified** |
| Evaluation metric | `CZI Biohub Zebrafish 133605` (custom, defined on the Evaluation tab) | verified |
| Max daily submissions | 5 | verified |
| Max team size | 5 | verified |
| Entry / merger deadline | 2026-09-22 23:59 UTC = **2026-09-23 08:59 JST** | verified |
| Final submission deadline | 2026-09-29 23:59 UTC = **2026-09-30 08:59 JST** | verified |
| Prize | 60,000 USD | verified |
| Teams entered | 2,580 | verified |
| User has entered | `True` | verified |

Both deadlines agree with `docs/COMPETITION_GUIDE.md` §1. That doc is **not** stale
on deadlines.

### 1.2 NOT verified — the Code Requirements tab could not be read

`https://www.kaggle.com/competitions/.../overview/evaluation` and the Rules and Code
Requirements tabs are a JavaScript single-page app; `WebFetch` and `curl` both return
only a 5.6 KB shell containing the page title and no rule text. The organiser
announcement on `forum.image.sc` returns HTTP 403.

Therefore the following remain **assumed, not verified**, and a human must read the
Code Requirements tab before any submission:

| Requirement | Assumption used in this document | Basis |
|---|---|---|
| Runtime limit | **9 h** per submission run (12 h interactive) | Kaggle's general code-competition policy, not this competition's page |
| GPU availability | assumed offered (P100 / T4 x2) | Kaggle default; unconfirmed for this competition |
| Internet at rerun | assumed **disabled** | universal for code competitions |
| External data / pretrained weights | assumed allowed if publicly available and disclosed | standard Kaggle research-competition wording |

The runtime arithmetic in §3 is therefore presented against **both 9 h and 12 h**, and
the conclusion is the same under either.

### 1.3 Submission format — verified from the organisers' own converter

Source: `scripts/geffs_to_csv.py` and `scripts/csv_to_geffs.py` in
`royerlab/kaggle-cell-tracking-competition`, pinned commit `075fc5f5a52d11077f9dc2b074644618f26939e2`,
vendored read-only at
`<CODEX>/artifacts/strong_baseline_v1/upstream/scripts/`.

Header, exact and ordered:

```
id,dataset,row_type,node_id,t,z,y,x,source_id,target_id
```

* `id` — prepended by `with_row_index("id")`: 0-based, contiguous, globally unique.
* Node row: `node_id,t,z,y,x` valid; `source_id = target_id = -1`.
* Edge row: `source_id,target_id` valid; `node_id,t,z,y,x = -1`.
* `dataset` = `geff.stem`, i.e. the per-sample GEFF filename.
* Rows are emitted **nodes first, then edges**, per dataset.

**The load-bearing detail: submission coordinates are VOXEL INDICES, not micrometres.**
`graph_to_rows` does `pl.col("z").cast(pl.Float64).round(0).cast(pl.Int64)` for each of
`z`, `y`, `x`. The evaluator applies the anisotropic scale separately —
`scripts/evaluate.py::_read_scale` reads it from the dataset's `.zarr` metadata
(falling back to `DEFAULT_SCALE`) and passes it to `DistanceMatching(max_distance=7.0,
scale=scale)`. Writing micrometres into the GEFF would be destroyed silently by the
integer rounding: `x = 0.40625 µm` becomes `0`.

Also verified from `metrics.py`: `n_total` for the node-count penalty is read from the
**ground-truth** GEFF's `extra["estimated_number_of_nodes"]`, i.e. it lives on the
evaluator side and is not something the submission carries.

The existing precedent artefact `MAIN/artifacts/official_one_pass/submission.csv`
matches this contract exactly: 49,531 lines = 1 header + 25,994 node rows + 23,536 edge
rows, and its coordinates are voxel indices (`z` in 0..63, `y`/`x` in 0..255).

---

## 2. Test-side inventory

### 2.1 S0 — the visible test split duplicates four training movies

Enumerated the full competition file list through the Kaggle API (125 pages,
`page_size=200`, backoff on HTTP 429). Raw evidence:
`artifacts/lane_d/kaggle_competition_files.tsv` (24,886 rows, untracked per
`.gitignore`).

| Split | Movies | Bytes | GiB |
|---|---:|---:|---:|
| `test/` | **4** | 1,906,332,008 | 1.775 |
| `train/` | **199** zarr + 199 geff | 85,703,559,720 | 79.818 |
| `sample_submission.csv` | 1 file | 890 | — |
| **Total** | | **87,609,892,618** | **81.593** |

All four test movies duplicate a train movie of the same `sample_id`, byte-size for
byte-size, and **train publishes ground truth for all four**:

| dataset | `test/` zarr bytes | `train/` zarr bytes | equal | chunk mismatches | `train/*.geff` GT bytes |
|---|---:|---:|:--:|:--:|---:|
| `44b6_0113de3b` | 456,757,564 | 456,757,564 | yes | 0 / 102 | 7,617 |
| `44b6_0b24845f` | 547,662,847 | 547,662,847 | yes | 0 / 102 | 7,618 |
| `6bba_05b6850b` | 361,668,669 | 361,668,669 | yes | 0 / 102 | 12,964 |
| `6bba_05db0fb1` | 540,242,928 | 540,242,928 | yes | 0 / 102 | 15,343 |

Every one of the 102 constituent chunk files matches in size for every movie. This is
established, not a hypothesis.

Consequences:

1. The public leaderboard is scored on movies whose ground truth the organisers hand
   out. It is trivially saturable and carries **no generalisation signal**. Do not use
   public LB as validation.
2. `isKernelsSubmissionsOnly = True` means the notebook is **rerun against hidden data**.
   The visible `test/` is a public placeholder. Private-LB movie count is unknown and is
   the single biggest unknown in the runtime budget (§3).
3. **The local best of 0.92112 was measured on `44b6_0113de3b`, which is a public test
   movie.** Any threshold or hyper-parameter chosen against that movie's GT is tuned on
   the public leaderboard, not validated against it. Per `BRIEF` §0.4 this is exactly the
   leakage path that must stay closed.
4. **Two of the five fixed validation-panel samples are public test movies.** Verified
   from `<CODEX>/artifacts/detector_fixed_race/panel.json`, whose samples are
   `44b6_0113de3b`, `44b6_0b24845f`, `44b6_0c582fdc`, `44b6_0db75fae`, `44b6_12dfb391` —
   the first two are in `test/`. Only 3 of 5 panel samples are honest held-out data.
   195 of the 199 train movies are untouched by the public test split and could be used
   instead.

### 2.2 Shape, verified from the file layout

Each `.zarr` contains exactly 102 entries: `<name>.zarr/0/c/<t>/0/0/0` for `t = 0..99`,
plus `<name>.zarr/zarr.json` and `<name>.zarr/0/zarr.json`.

* `T = 100` for every test movie — verified.
* One chunk per timepoint spanning the whole `(Z, Y, X)` — verified. A single frame is
  therefore the minimum readable unit (~3.5–5.5 MB compressed).
* `(Z, Y, X) = (64, 256, 256)` and scale `(1.625, 0.40625, 0.40625)` µm — **assumed**,
  carried over from the five-sample panel; not re-verified for the test movies because
  that needs the zarr metadata, which is not downloaded.
* Zarr format **v3** (`zarr.json`, not `.zarray`) — verified from the filenames.

---

## 3. Runtime and memory arithmetic

### 3.1 Measured inputs — three full 100-frame detector runs

All CPU, all in `biohub-dev`, all `detector_call_count = 100`, all from
`<CODEX>/artifacts/detector_fixed_race/*/cache/<sample>/manifest.json`
(`provenance.elapsed_seconds`, `node_count`, `edge_count`).

| sample | detector s | cache nodes | **candidate edges** | assoc s (1 method) | in public `test/`? |
|---|---:|---:|---:|---:|:--:|
| `44b6_0113de3b` | 4,841.27 | 26,887 | 7,240,938 | 29.07 | **yes** |
| `44b6_0c582fdc` | 5,447.65 | 34,910 | 12,459,009 | 58.1 – 59.3 | no |
| `44b6_0b24845f` | 5,476.42 | 66,845 | **45,354,474** | 145.9 – 161.1 | **yes** |
| min / mean / max | 4,841 / **5,255** / 5,476 | 26,887 / 42,881 / 66,845 | 7.2 M / 21.7 M / **45.4 M** | 29 / 80 / **161** | |

Association figures: 116.29 s for four methods on the dev sample (÷4 = 29.07 s);
`wall_time.txt` receipts under `panel_runs_0c_*` and `panel_runs_0b_*` for the others.
The three sub-minute smoke runs (40.47 s, 138.78 s, 340.36 s) are partial-frame smokes
and are **excluded** — they are not 100-frame movies.

**Per movie, one method, end to end:** dev 4,870.3 s · `0c582fdc` 5,506.4 s ·
`0b24845f` **5,627.8 s** (= 1.563 h). Mean 5,334.8 s. Model load and zarr open are still
not separately measured, so all of this is optimistic.

**The dev sample is the smallest of the three, not a typical one.** Every earlier
estimate anchored on it understates node count by up to 2.5× and candidate edges by
6.3×.

### 3.2 Does the current pipeline finish the test set inside the limit?

Using the worst measured movie (5,627.8 s):

| Scenario | Movies | Wall time | vs 9 h | vs 12 h |
|---|---:|---:|---|---|
| Visible public test | 4 | 22,511 s = **6.25 h** | fits, 69 % used | fits, 52 % used |
| Hidden = 6 | 6 | 33,767 s = 9.38 h | **1.04× over** | fits |
| Hidden = 10 | 10 | 56,278 s = 15.63 h | **1.74× over** | 1.30× over |
| Hidden = 20 | 20 | 112,556 s = 31.3 h | **3.47× over** | 2.61× over |
| Hidden = 50 | 50 | 281,390 s = 78.2 h | **8.68× over** | 6.51× over |
| Hidden = train size | 199 | 1,119,932 s = **311.1 h** | **34.6× over** | 25.9× over |

Break-even: **5.76 movies at 9 h**, 7.68 at 12 h, with zero headroom.

Only two of the four visible test movies have ever been measured — `44b6_0113de3b` and
`44b6_0b24845f`. The two `6bba_*` test movies come from a different acquisition series
and have never been run at all.

### 3.3 The CPU-parity correction — the actual headline

The measurements ran while `docker stats` showed **~700 % CPU** (~7 cores). A Kaggle
CPU-only notebook provides **4 vCPU**. If the detector is compute-bound and scales with
cores, wall time multiplies by about `7 / 4 = 1.75`:

| | container | Kaggle CPU (est. ×1.75) |
|---|---:|---:|
| Per movie (worst measured) | 5,627.8 s | **9,848.7 s = 2.74 h** |
| Visible 4-movie test set | 6.25 h | **10.94 h — over a 9 h limit** |
| Movies that fit in 9 h | 5.76 | **3.29** |

**On CPU this pipeline does not finish even the four visible test movies.** The 1.75×
factor is estimated, not measured, but the margin at four movies is only 1.44×, so the
conclusion is robust to a wide error bar.

Speedup required to fit 9 h:

| Hidden test movies | 4 | 6 | 10 | 20 | 50 | 199 |
|---|---:|---:|---:|---:|---:|---:|
| container baseline | 0.69× | 1.04× | 1.74× | 3.47× | 8.68× | 34.6× |
| Kaggle-CPU estimate | **1.22×** | 1.82× | 3.04× | 6.08× | 15.2× | 60.5× |

**GPU inference is not an optimisation here; it is the only route to a valid
submission.** It remains entirely unmeasured: the container runs `torch 2.13.0+cpu`
with no CUDA and no MPS, and `pyproject.toml` pins torch to the `pytorch-cpu` index
(`[tool.uv.sources] torch = { index = "pytorch-cpu" }`).

### 3.4 Memory — candidate edges grow quadratically and there is no headroom

Across the three measured movies, candidate edges scale almost exactly with the square
of the detected node count:

```
E ≈ 0.01015 · N²
    26,887 nodes → predicted  7,337,544   observed  7,240,938   (+1.3 %)
    34,910 nodes → predicted 12,369,887   observed 12,459,009   (−0.7 %)
    66,845 nodes → predicted 45,354,474   observed 45,354,474   (fit point)
```

A 2.49× spread in node count produces a **6.26× spread in candidate edges**.

Per-edge in-memory footprint, summed from the cache schema in `manifest.json`
(`delta_t` int16; `forward_logit`, `forward_probability`, `physical_distance`,
`reverse_logit`, `reverse_probability`, `voxel_distance` float32; `physical_delta` and
`voxel_delta` float32×3; `source_node_id`, `target_node_id` int64):

```
2 + 4 + 4 + 12 + 4 + 4 + 4 + 8 + 8 + 12 + 4 = 66 bytes per candidate edge
```

| sample | candidate edges | edge arrays in RAM |
|---|---:|---:|
| `44b6_0113de3b` | 7,240,938 | 0.445 GiB |
| `44b6_0c582fdc` | 12,459,009 | 0.766 GiB |
| `44b6_0b24845f` | 45,354,474 | **2.79 GiB** |

The only RAM observation anyone has — 4.2–5.0 GiB of container RSS — was taken on
`44b6_0113de3b`, whose edge arrays are the **smallest of the three, 6.3× below
`44b6_0b24845f`**. The existing memory picture is anchored on the best case.

Combining `E ≈ 0.01015·N²` with 66 B/edge gives `RAM ≈ 0.67·N²` bytes for the edge
arrays alone, and therefore a hard ceiling on detectable cells:

| RAM budget for edge arrays | Max nodes | vs densest movie measured |
|---|---:|---:|
| 7.651 GiB (this container) | ~110,700 | 1.66× |
| 13 GiB (Kaggle GPU notebook) | ~144,400 | 2.16× |
| 30 GiB (Kaggle CPU notebook) | ~219,300 | 3.28× |

**A hidden movie with roughly twice the cell density of `44b6_0b24845f` exhausts a
Kaggle GPU notebook's RAM on the candidate-edge arrays alone**, before the model, the
image volume or the ILP solver are counted. Peak RSS has never been isolated, so the
true ceiling is lower than these figures, not higher.

### 3.5 Output size

The dry-run CSV is 2,532,038 B for 50,506 rows — **50.1 bytes/row**. Predicted
`harmonic_v1` graph sizes: `44b6_0113de3b` 50,506 rows, `44b6_0c582fdc` 61,778,
`44b6_0b24845f` 104,264 (mean 72,183). A four-movie test set is therefore ~289 k rows
≈ 14.5 MB, and a 199-movie set ~14.4 M rows ≈ 720 MB. Both fit Kaggle's 20 GB working
directory; only the latter is slow enough to be worth timing.

---

## 4. Offline execution audit

Every artefact the inference path needs at rerun time, and how it reaches an
internet-off notebook.

| # | Item | Version / hash | Route to an offline notebook | Status |
|---|---|---|---|---|
| 1 | Competition data | 81.6 GiB total; test split 1.775 GiB | Auto-mounted at `/kaggle/input/<slug>/` in a code competition | **OK** |
| 2 | `edge_predictor_best.pth` | 8 MB, sha256 `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235` | Must be uploaded as a Kaggle Dataset and attached as a notebook input. **Not uploaded.** | **S1 gap** |
| 3 | Upstream `tracking_cellmot` source (`build_graph`, ILP, `io`, converters) | pinned commit `075fc5f5` | Not on PyPI. Must ship as a Kaggle Dataset or notebook utility script. **Not packaged.** | **S1 gap** |
| 4 | **`tracksdata`** | installed **`0.1.0rc9.dev4+g7bfeaf845`**, from `git+https://github.com/royerlab/tracksdata@7bfeaf845ceb951226f19b72fe5b80e01601018a` | **This version does not exist on PyPI** (PyPI latest is `0.1.0rc8`). A direct git reference cannot be installed offline. Must be built into a wheel from that exact commit and shipped as a Kaggle Dataset, installed with `pip install --no-index --find-links`. | **S1 blocker** |
| 5 | `ilpy` | 0.6.0 | On PyPI, but ships compiled solver backends (SCIP/Gurobi). Needs a verified manylinux wheel plus whatever SCIP runtime it expects, vendored offline. Unverified. | **S1 gap** |
| 6 | `numba`, `numcodecs`, `imagecodecs`, `bidict` | transitive deps of `tracksdata` | On PyPI; must be vendored as wheels. `numba` is tightly coupled to the numpy version (`numpy 2.4.6` here) — a real compatibility risk. | **S2 risk** |
| 7 | `geff` | 1.3.0.1.2 | On PyPI, exact version available. Vendor the wheel. | OK once vendored |
| 8 | `polars` 1.43.2, `zarr` 3.1.6, `scipy` 1.17.1, `networkx` 3.6.1, `numpy` 2.4.6 | | On PyPI. **`zarr` must be 3.x** — the data is zarr format v3, and Kaggle base images have historically shipped zarr 2.x, which cannot read it. Verify the image's version before relying on it. | **S2 risk** |
| 9 | `torch` | `2.13.0+cpu`, pinned to the `pytorch-cpu` index | For a GPU notebook this is the **wrong build**. The pinned environment cannot use a Kaggle GPU at all without switching to a CUDA wheel — which interacts with §3.3, where GPU is the only viable route. | **S1 gap** |

Nothing in items 2–6 and 9 currently has a working answer. Each is a run-day failure.

---

## 5. What was built

`src/biohub/submission/`:

* `schema.py` — the contract, transcribed from the pinned upstream converter with
  sources cited inline. Encodes the voxel-vs-µm fact and the metric constants.
* `validator.py` — **pure standard library**, enforced by a test that parses the module
  AST and asserts no non-stdlib top-level import. Runs with no competition data and no
  third-party packages present, so it can be the final cell of the Kaggle notebook.
* `packaging.py` — per-sample prediction GEFF → `submission.csv`, mirroring
  `geffs_to_csv` but taking an explicit `{dataset: geff_path}` mapping (see §7), plus a
  provenance sidecar and a stdlib CSV round-trip check.
* `fixture.py` — SYNTHETIC fixtures only, per `AGENTS.md` §8. One valid submission and
  fourteen single-defect variants. Every synthetic dataset name carries the
  `SYNTHETIC_` prefix so a fixture can never be mistaken for a real artefact.
* `cli.py` — `validate` / `package` / `fixture`. **There is deliberately no `submit`
  subcommand.**

### Validator checks

Schema: file exists, non-empty, named `submission.csv`, exact ordered header, 10 fields
per row, `id` is a 0-based contiguous index, every integer column parses strictly (a
float such as `12.5` is rejected), `row_type ∈ {node, edge}`, `dataset` non-blank.

Row-type discipline: node rows carry `source_id = target_id = -1`; edge rows carry
`node_id = t = z = y = x = -1`; no negative values in required fields.

Graph structure, per dataset: unique `node_id`; edge endpoints exist **within the same
dataset**; no self-loops; no duplicate edges; strictly forward in time (`dt ≤ 0` is an
error, `dt ≠ 1` a warning since the official candidate generator only builds `t → t+1`);
isolated-node count reported because isolated nodes still inflate `total_node_ratio` and
depress the adjusted score.

Division representation: fork count (out-degree ≥ 2); out-degree > 2 warns, because
`metrics.py` drops out-edges ranked beyond the second (`_out_rank > 2`) rather than
scoring them; in-degree ≥ 2 warns for the same reason (`_is_merge_dup`); zero forks
anywhere warns (or errors under `--require-divisions`) because the
`0.1 × division_jaccard` term is then forfeited.

Coordinates and axis order: bounds against an assumed `(T,Z,Y,X)`; and a units check —
in voxel space the z extent over the xy extent is ~0.25 on a 64×256×256 volume, whereas
micrometre coordinates (scale 1.625 / 0.40625 / 0.40625) make the volume look isotropic
at ~1.0. A ratio above 0.60 is an error naming both possible causes, wrong units or
permuted `z/y/x`.

Coverage: exact dataset-set match when an expected list is supplied — missing datasets
and **unexpected** datasets are both errors, the latter being what a train movie leaking
into the submission looks like. With no list supplied, coverage is reported as
explicitly **unchecked** rather than silently passing.

Ground-truth leakage (`--gt-dir`, opt-in): ground truth is read **only** as a leak
detector and never reaches any prediction path. Two independent signals — a *size*
signal, since a submission whose node count is within 4× of the sparse GT node count is
not a detector output; and an *exact-coincidence* signal, since predicted centroids
landing on the exact integer GT voxel should be rare.

---

## 6. Measured results

### 6.1 Tests — executed

```
docker compose exec -T biohub sh -lc 'cd <worktree> && \
  PYTHONPATH=src /opt/venv/bin/python -m pytest tests/test_submission_validator.py -q'
→ 32 passed in 0.15s
```

Includes one parametrised case per fixture defect, asserting both the expected finding
code and that the defect blocks submission.

### 6.2 Dry run on a real prediction GEFF — executed

No heavy run. Reused the already-computed `harmonic_v1` prediction from
`<CODEX>/artifacts/detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/`.

```
python -m biohub.submission.cli package \
  --run-dir <CODEX>/artifacts/detector_fixed_race/dev_full_auto_compact_timed \
  --method harmonic_v1 --csv artifacts/lane_d/dryrun/submission.csv
```

| | |
|---|---|
| Datasets packaged | 1 (`44b6_0113de3b`) |
| Nodes / edges | **26,301 / 24,205** — matches `BRIEF` ADDENDUM A3 exactly |
| CSV rows | 50,506 = 26,301 + 24,205 |
| Round-trip | node/edge counts and id references intact |
| `t` range | 0 – 99 |
| Predicted forks | **30** |
| Merges | 0 |
| Validator verdict | PASS, 0 errors, 0 warnings |
| GT leak check vs `MAIN/data/train` | 5/52 (10 %) exact coincidences against 26,301 predicted nodes → consistent with an independent detector |

Coverage run against the real four-movie test set correctly failed:

```
--expect-datasets 44b6_0113de3b,44b6_0b24845f,6bba_05b6850b,6bba_05db0fb1
→ ERROR DATASET_MISSING: 3 expected dataset(s) have no rows
→ FAIL: 1 error(s)
```

### 6.3 A note on the 30 predicted forks

The graph contains **30 forks**, yet the official metric reports
`division_tp/fp/fn = 0/0/0` and `division_jaccard = null` for this movie. The division
term is therefore untested not because the pipeline predicts no divisions — it predicts
thirty — but because the single evaluated movie has **no annotated GT division** to
score them against. This sharpens `BRIEF` §3.3: the risk is not silence from the model,
it is a complete absence of measurement.

---

## 7. Gap list

| # | Gap | Severity |
|---|---|---|
| 1 | The four visible test movies duplicate four train movies with GT. Public LB is meaningless; the local 0.92112 is an in-sample number on a public test movie, not a generalisation estimate. | **S0** |
| 2 | Across three measured movies the worst costs 5,627.8 s. 9 h admits 5.76 movies; under a CPU-parity correction only 3.29, so not even the visible four. Any hidden set beyond a handful fails outright. GPU inference is unmeasured and the environment pins CPU-only torch. | **S1** |
| 2b | Candidate edges scale as `E ≈ 0.01015·N²`. At 66 B/edge the arrays alone reach 2.79 GiB on `44b6_0b24845f`, and a movie ~2× denser exhausts a Kaggle GPU notebook's RAM before the model or solver are counted. The only RAM observation ever taken was on the *smallest* of the three movies. | **S1** |
| 3 | `tracksdata` is installed from a git commit and that version is not on PyPI. An internet-off notebook cannot install it as pinned. | **S1** |
| 4 | Checkpoint, upstream `tracking_cellmot` source, `ilpy` solver backend and the CUDA torch build have no packaging route to an offline notebook. | **S1** |
| 5 | The competition's own Code Requirements tab has never been read. Runtime limit, GPU offering, internet policy and external-data policy are all assumed. | **S1** |
| 6 | Zero end-to-end Kaggle-notebook rehearsal has ever been done, and the team has never submitted. | **S1** |
| 7 | `zarr` must be 3.x to read this format-v3 data; the Kaggle image version is unverified. `numba`/`numpy 2.4` coupling is a live compatibility risk. | **S2** |
| 8 | Peak RSS was never isolated — only whole-container `docker stats` samples, and those were taken on the least demanding of the three movies. | **S2** |
| 9 | Division scoring is entirely unmeasured: 30 predicted forks against a movie with no annotated GT division. | **S2** |
| 10 | `sample_submission.csv` (890 B) has never been downloaded, so the header is verified against the organisers' converter and the local precedent artefact but not against the competition's own file. | **S3** |

