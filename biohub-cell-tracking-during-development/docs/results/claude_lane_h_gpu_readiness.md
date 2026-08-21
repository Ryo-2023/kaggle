# Lane H — GPU / Container Readiness Audit

Scope: audit whether `--device auto` would actually put the model on CUDA on
the planned lab RTX box, whether the Docker/uv packaging would actually
install a CUDA `torch` wheel there, ship a verification procedure that is
safe to run today and meaningful on the lab box, and give a defensible
order-of-magnitude GPU payoff estimate. This is an audit-and-prepare lane:
nothing here touches the running `biohub-dev` container's build, and no
GPU claim below is stated as measured unless a command and its real output
are shown.

Sources read: `src/biohub/device.py`, `src/biohub/detector_fixed_race/`,
`src/biohub/strong_baseline/runner.py`, `src/biohub/benchmark_race/`,
the pinned vendored upstream at `artifacts/strong_baseline_v1/upstream/`,
`docker/Dockerfile`, `docker-compose.yml`, `docker-compose.nvidia.yml`,
`pyproject.toml`, `uv.lock`, `setup.sh`, `README.md`, and
`docs/results/{strong_baseline_v1,detector_fixed_association_race,chatgpt_submission_report_ja}.md`,
all read live from the read-only worktrees (MAIN =
`biohub-cell-tracking-during-development`, CODEX =
`scratch/strong-baseline-v1/biohub-cell-tracking-during-development` at
commit `f6f9ea3`+uncommitted, per BRIEF.md ADDENDUM A1).

---

## 1. Device-selection code audit

`src/biohub/device.py:17-51` (`resolve_torch_device`) is the one correct,
well-tested implementation of CUDA→MPS→CPU auto-resolution in the repo
(`tests/test_device_selection.py`, 4 tests, all pass via monkeypatching).
The question is which pipelines actually call it.

| # | Location | What it does | Would it silently stay on CPU on a CUDA box? |
|---|---|---|---|
| 1 | `src/biohub/detector_fixed_race/upstream_adapter.py:459,469,543,561-568` | `resolve_torch_device(expected_device)` → `upstream.load_model(checkpoint, device)` (`map_location=device` + `model.to(device)`, confirmed in the vendored `predict_unet_transformer.py:196-198`) → `upstream.predict_video(model, image_path, device, ...)`; the manually-replayed reverse-pass tensors are each explicitly `.to(device)` (lines 561-568). | **No.** Verified correct end to end: every tensor that reaches the model is placed with the resolved `device`, not a default. This is the pipeline that produced the ADDENDUM A3 100-frame result. |
| 2 | `src/biohub/detector_fixed_race/cli.py:118-120,180` | Only the `materialize` subcommand exposes `--device` (default `"auto"`), wired straight to `expected_device=args.device`. `associate`/`evaluate`/`dev-race` have **no** device flag at all. | N/A by design — those subcommands only replay an existing cache (numpy/ILP/GEFF), never touch the model, so there is nothing to place on a device. Confirms the Task-4 "floor" claim from the code side, independent of the timing evidence. |
| 3 | **`src/biohub/strong_baseline/runner.py:73,291-297,640-644,832-836`; `scripts/run_strong_baseline_v1.py:36,45`** | A **second, separate, duplicated** device-resolution path for the older `strong_baseline` pipeline — the one that produced the "current best" `harmonic_ilp` score BRIEF.md §2 cites (0.9211). It does **not** call `resolve_torch_device`. It does `device = torch.device(request.expected_device)` directly, where `InferenceRequest.expected_device: str = "cpu"` (line 73) and the CLI flag is `--expected-device` (not `--device`), `default="cpu"` (`run_strong_baseline_v1.py:36`). `_devices_match()` (line 291) has cases for `"cpu"` and `"cuda"` only — **no `"auto"` case exists anywhere in this module.** | **Yes — S1.** If anyone re-runs `run_official_inference` / `run_harmonic_inference` on the lab box without remembering to pass `--expected-device cuda` explicitly, it silently keeps running on CPU: no error, no warning, `torch.device("cpu")` is a perfectly valid construction. `torch.device("auto")` would in fact *raise*, so this pipeline can't even opt into the portable convention the newer one uses. Once `cuda` *is* passed explicitly, the actual compute path (`upstream.load_model`/`upstream.predict_video`) is the same audited-correct code as row 1 — the bug is "no auto, CPU-by-default", not "device ignored after being set." |
| 4 | `artifacts/strong_baseline_v1/upstream/scripts/predict_unet_transformer.py:537`; `train_unet_transformer.py:1122` | The upstream script's own standalone `main()`/`predict()`/training entrypoints hardcode `device = torch.device("cuda" if torch.cuda.is_available() else "cpu")` — no MPS branch, no override. | **Dead code on the exercised path (S3).** Confirmed neither adapter calls `predict()`/`main()`; both `_load_upstream_predictor` (project code) and `run_official_smoke`/`run_harmonic_inference` (strong_baseline) import the module and call `load_model`/`predict_video`/`_detect_cells_pooled` directly. Only matters if someone invokes the vendored script directly instead of through the project's adapters — worth a one-line comment in the vendoring notes so nobody "fixes" this file by hand later expecting it to matter. |
| 5 | `artifacts/strong_baseline_v1/upstream/src/tracking_cellmot/io.py:41,199` (`open_dataset`/`_process_on_gpu`); `img_proc.py:128` (`resample_image_to_isotropic`) | `open_dataset(..., device: str = "cuda", ...)` — hardcoded literal default, not `"auto"`, not injected. Feeds `_process_on_gpu`'s `tensor = torch.from_numpy(image).pin_memory().to(torch_device, non_blocking=True)` (io.py:199) whenever `load_image=True` and no explicit `device=` kwarg. `resample_image_to_isotropic` does an unconditional `torch.from_numpy(image).cuda()` (img_proc.py:128), no fallback at all. | **Confirmed dead on the exercised path (S3), but a real landmine if touched.** Both real call sites the project uses — `predict_unet_transformer.py:319` and `evaluate.py:46` — pass `load_image=False`, which hits `open_dataset`'s early `if not load_image: return Dataset(...)` *before* reaching `_process_on_gpu`. Only the upstream's own `visualize/visualize_ground_truth.py:55` and `visualize/visualize_predictions.py:145` call `open_dataset(ds_path, require_tracks=True)` with `load_image` defaulted `True` and no `device=` — those two vendored, unused-by-us scripts would crash immediately on this CPU/MPS box (bare `.cuda()`) and would only "work" on the lab box by accident of the hardcoded default, ignoring whatever the project's own `--device` selection says. `resample_image_to_isotropic` is unreferenced anywhere in the pinned upstream repo or this project — genuinely dead, confirmed by grep. |
| 6 | `artifacts/strong_baseline_v1/upstream/src/tracking_cellmot/models/{temporal_unet,simple_node_transformer}.py` | No `device`/`.cuda()`/`.to(` anywhere in either model file. | Clean — all placement flows correctly from the caller's `model.to(device)`; nothing hardcoded inside the model classes. |
| 7 | `predict_unet_transformer.py:291` (`_detect_cells_pooled`), `:456,458` (per-pair edge probabilities), mirrored in `upstream_adapter.py`'s capture wrappers | `.cpu().numpy()` conversions **are** inside the real hot loop: once per frame for peak detection (100/movie) and once per consecutive-frame pair for forward+reverse edge probabilities (99+99 = 198/movie). | Not a device-selection bug — these conversions build the numpy arrays the Python-side thresholding/graph code needs and are architecturally required. They do impose ~298 forced host↔device syncs per 100-frame movie that will **not** shrink on a faster GPU; folded into the Task 4 estimate below, not treated as a defect. |
| 8 | `src/biohub/benchmark_race/{cc_flow,motion,blob_lap}.py:875-876,788-789,576-577` | Each explicitly `raise ValueError(...)` if `expected_device != "cpu"` ("currently supports only the CPU device"). | Correct and intentional — classical (non-neural) association baselines. Confirms, from the code side, that these methods are permanently CPU-bound regardless of migration. |
| 9 | Dataloader / `pin_memory` / `num_workers` | Grepped the whole exercised inference path: **none.** `DataLoader`/`pin_memory`/`num_workers` only appear in the upstream's own `train_unet_transformer.py` (training script, not used by either adapter). | No dataloader-related device bug exists in the live inference pipeline — there is no dataloader in it at all; frames are pulled directly from zarr per-window (`_load_frame`, CPU tensor, moved to `device` once per batched window, not per frame). |

**Bottom line for Task 1:** the pipeline that produced ADDENDUM A3's
verified 100-frame result (`detector_fixed_race`) is device-correct
end-to-end. The pipeline that produced BRIEF §2's cited "current best"
score (`strong_baseline`) is a **separate, silently-CPU-by-default**
implementation with no `auto` support — row 3 is the single highest-value
fix for Codex (§6 below).

---

## 2. CUDA container audit

### 2.1 Would `uv sync` on the CUDA lab box actually install a CUDA torch wheel?

**On MAIN as it stands today: no, guaranteed.** `pyproject.toml:31-37`:

```toml
[tool.uv.sources]
torch = { index = "pytorch-cpu" }

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true
```

pins `torch` to the CPU wheel index unconditionally — no platform marker,
no CUDA alternative declared anywhere in `pyproject.toml`. `uv.lock:1396-1418`
resolved `torch==2.13.0+cpu` for `resolution-markers = ["sys_platform !=
'darwin'"]` (i.e. **all** Linux, Windows — this covers x86_64, so the "ARM-only
lockfile" risk BRIEF flagged as a possible S1 does **not** apply: a
`manylinux_2_28_x86_64` wheel is present at line 1415). `grep -c '^name =
"nvidia'  uv.lock` returns zero — there is no CUDA-runtime pip package
(`nvidia-cublas-cu12` etc.) anywhere in the lockfile; it has never resolved
a CUDA build, on any platform. `MAIN/docker/Dockerfile` has **zero** `ARG`
declarations (`grep -c '^ARG' docker/Dockerfile` = 0) and ends in a bare
`RUN uv sync` — there is no mechanism in MAIN by which any build-time input
could change what gets installed.

**On CODEX's branch (`codex/biohub-multi-method-race`, not yet merged to
MAIN): the missing mechanism already exists**, added in the same commit as
`docker-compose.nvidia.yml` (`fa58471 Add optional NVIDIA Compose
environment`):

```dockerfile
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
...
RUN if [ "${TORCH_INDEX_URL}" = "https://download.pytorch.org/whl/cpu" ]; then \
        uv sync; \
    else \
        uv sync --no-install-package torch \
        && uv pip install --index-url "${TORCH_INDEX_URL}" "torch>=2.9.1"; \
    fi
```

(`scratch/strong-baseline-v1/.../docker/Dockerfile:3,62-67`), matched by
`docker-compose.nvidia.yml`'s `build.args.TORCH_INDEX_URL:
${BIOHUB_TORCH_INDEX_URL:?Set BIOHUB_TORCH_INDEX_URL to the official CUDA
PyTorch wheel index}` and `README.md:124-127`'s documented invocation. This
is plausible and reasonably designed — **but has never been built or run by
anyone.** No CUDA box has existed to build it against; `docker compose
build` is forbidden to every lane under BRIEF §0.1 right now; and it is
not on MAIN, which BRIEF's repo map calls the tracked source for docker
infra. Two concrete, unverified risks in this branch even once merged:

- **Pairing risk (S1):** `docker-compose.nvidia.yml`'s `TORCH_INDEX_URL`
  build arg only does anything if it reaches a Dockerfile that declares
  `ARG TORCH_INDEX_URL`. If the lab-box migration starts from MAIN (the
  documented canonical source) and only `docker-compose.nvidia.yml` is
  copied over without also replacing `docker/Dockerfile`, the arg is
  silently unconsumed (Docker emits a build warning, not an error) and the
  build falls straight back to `uv sync` → `torch+cpu`, exactly the failure
  this whole lane exists to catch, at the packaging layer instead of the
  code layer. **Both files must move together** — either merge Codex's
  branch, or port `docker/Dockerfile` and `docker-compose.nvidia.yml`
  as a pair, never one without the other.
- **Reproducibility risk (S2):** the else-branch's `uv pip install
  --index-url ... "torch>=2.9.1"` is a `uv pip` (pip-compatible interface)
  install, not `uv sync` — it resolves a fresh, unpinned, un-hashed torch
  version at build time instead of using `uv.lock`'s pinned `2.13.0`,
  and it is unverified whether it reliably targets the same
  `UV_PROJECT_ENVIRONMENT=/opt/venv` that `uv sync` just populated in the
  same `RUN` layer. This whole branch of the Dockerfile has literally
  never executed. Recommend printing `torch.__version__` /
  `torch.version.cuda` immediately after the first real build (this is
  exactly what `scripts/verify_gpu_readiness.sh` step 3 does) before
  trusting it, and pinning a specific torch version (matching
  `uv.lock`'s `2.13.0`) in the `uv pip install` line rather than the
  open `>=2.9.1` range, to avoid silent version drift from the rest of
  the locked environment.

### 2.2 Does `docker-compose.nvidia.yml` correctly request GPUs and inherit the base service?

Yes to both, with one documentation caveat. `docker-compose.nvidia.yml`:

```yaml
services:
  biohub:
    build:
      context: .
      dockerfile: docker/Dockerfile
      args:
        TORCH_INDEX_URL: ${BIOHUB_TORCH_INDEX_URL:?Set BIOHUB_TORCH_INDEX_URL to the official CUDA PyTorch wheel index}
    gpus: all
    environment:
      NVIDIA_VISIBLE_DEVICES: all
      NVIDIA_DRIVER_CAPABILITIES: compute,utility
```

- `gpus: all` is the modern, top-level Compose Specification key (the
  simpler alternative to the long-form `deploy.resources.reservations.devices`
  that BRIEF's task list mentioned) — valid for the Docker Compose CLI v2
  plugin (`docker compose`, not the legacy standalone `docker-compose`
  v1 binary). The `NVIDIA_VISIBLE_DEVICES`/`NVIDIA_DRIVER_CAPABILITIES`
  env vars are redundant with `gpus: all` under a current
  nvidia-container-toolkit but harmless, and are a reasonable
  belt-and-suspenders for older toolkit/runtime combinations.
- It correctly **omits** `container_name`, `volumes`, `working_dir`,
  `command`, `healthcheck`, and the named-volume declarations — this is
  only correct because it is designed as a Compose multi-file overlay
  (`docker compose -f docker-compose.yml -f docker-compose.nvidia.yml ...`,
  documented at `README.md:124-127`), where those keys are inherited from
  the base file. Running `docker-compose.nvidia.yml` standalone would
  silently drop every volume mount and persistence path. This is fine as
  long as the two-file invocation is always used — flagged only because
  the base `docker-compose.yml` differs cosmetically between MAIN and
  CODEX's copy (Kaggle-credential mount read-write vs `:ro`,
  `biohub-codex-home`/`biohub-claude-home` volumes present only in MAIN's)
  and the overlay has only ever been eyeballed against CODEX's copy, not
  built against either.

### 2.3 Host prerequisites (not documented anywhere in the repo today)

1. **NVIDIA driver + `nvidia-smi` working on the bare host**, verified
   *before* touching Docker at all — this is step 1 of
   `scripts/verify_gpu_readiness.sh` below.
2. **`nvidia-container-toolkit` installed and configured** on the host
   (`nvidia-ctk runtime configure --runtime=docker`, then restart the
   Docker daemon) so the Docker Engine honors `gpus: all` / exposes
   `libcuda.so` into the container. The image is bare `ubuntu:24.04`
   (`docker/Dockerfile:1`, no `nvidia/cuda:*` base) — that's fine given
   the plan is a self-contained CUDA pip wheel (bundles its own CUDA
   runtime libs), but it means the *only* thing bridging host and
   container is the toolkit-injected driver library; don't expect
   `nvcc`/system CUDA inside the container, and don't skip this step.
3. **Docker Compose CLI recent enough to support the top-level `gpus:`
   key.** If the lab box only has the legacy standalone `docker-compose`
   v1 binary, this key is not recognized and the long-form
   `deploy.resources.reservations.devices` (or a plain `docker run
   --gpus all`) would be needed instead.
4. **Driver version matching the chosen CUDA wheel's floor.** `README.md`
   already tells the operator not to guess the index
   (`export BIOHUB_TORCH_INDEX_URL=https://download.pytorch.org/whl/cuXXX`)
   — the minimum driver version is specific to whichever `cuXXX` is
   picked and must be checked against the actual lab GPU/driver at
   provisioning time; this document does not assert a specific number
   because no lab box exists yet to verify one against.

---

## 3. Verification procedure

Two files, committed to this worktree:

- **`scripts/verify_gpu_readiness.sh`** — host-level steps 1-2
  (`nvidia-smi` on host, then inside the container), then invokes the
  pytest file for steps 3-9. Skips (not fails) steps 1-2 when no GPU
  tooling is present, so it is safe to run on this Mac dev box today.
- **`tests/test_gpu_readiness.py`** — pytest, steps 3-9. Skip/run is
  gated on `_host_has_nvidia_gpu()`, which shells out to `nvidia-smi`
  directly — **deliberately not on `torch.cuda.is_available()`**, because
  gating on the latter would make the single most important failure mode
  (a GPU box whose `uv sync` silently reinstalled `torch+cpu`, so
  `torch.cuda.is_available()` is `False` despite real hardware) look like
  an ordinary skip instead of a loud, diagnosable failure. `test_step0`
  runs unconditionally and asserts `torch.version.cuda is not None`
  whenever a GPU *is* detected by `nvidia-smi` — that is the actual S1
  regression test for §2.1.

| Step | What | Where |
|---|---|---|
| 1 | `nvidia-smi` on host | `verify_gpu_readiness.sh` |
| 2 | `nvidia-smi` inside container | `verify_gpu_readiness.sh` |
| 3 | `torch.version.cuda is not None` | `test_step3_torch_reports_a_cuda_build` |
| 4 | `torch.cuda.is_available()` | `test_step4_cuda_is_available` |
| 5 | `device_count >= 1` | `test_step5_at_least_one_cuda_device` |
| 6 | real tensor matmul on cuda | `test_step6_real_matmul_on_cuda` |
| 7 | actual `TemporalUNet3D` forward pass on cuda, finite output | `test_step7_temporal_unet3d_forward_pass_on_cuda` |
| 8 | device the run receipt would record matches cuda | `test_step8_auto_resolution_matches_what_a_run_receipt_would_record` |
| 9 | timing comparison vs CPU | `test_step9_cuda_matmul_is_not_slower_than_cpu` |

Step 7 loads the real, pinned `TemporalUNet3D` class
(`artifacts/strong_baseline_v1/upstream/src/tracking_cellmot/models/temporal_unet.py`)
directly from its file — it only imports `torch`/stdlib, so this needs no
checkpoint, no zarr volume, and no GT — and runs a real forward pass with
random weights on a shape-correct synthetic `(1,2,1,16,32,32)` window,
asserting the output is on `cuda`, finite, and that **every** parameter
was actually moved (`for p in model.parameters(): assert p.device.type ==
"cuda"`). This is the check that catches "auto resolves to cuda but the
model is left on CPU," per the brief's framing — not just that torch sees
a GPU, but that the project's own model runs on it. Step 8 doesn't re-run
the real ~80-minute `materialize` step just to read one receipt field —
it asserts `resolve_torch_device("auto") == torch.device("cuda")` and
`str(...) == "cuda"`, the exact function and exact string the real receipt
writer (`upstream_adapter.py`'s `"device": str(device)`) would use. The
doc recommends a real receipt spot-check after this suite passes once
(§migration procedure below).

### Verified today, on this CPU box, under the resource protocol

```
$ docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}'
biohub-dev 103.94% 3.299GiB / 7.651GiB          # < 6.2 GiB — safe to proceed

$ docker compose exec -T biohub sh -lc \
  'cd /workspace/biohub-cell-tracking-during-development/scratch/claude/h-gpu/biohub-cell-tracking-during-development \
   && uv run pytest tests/test_gpu_readiness.py -q'
ssssssss                                                                 [100%]
8 skipped in 2.07s
```

Re-run with `-v -rs` for the individual reasons — all 8 skip cleanly and
distinctly ("no nvidia-smi-visible GPU in this environment -- expected on
the CPU dev container, must NOT be skipped on the CUDA lab box"), no
errors, no import failures masquerading as skips. This is the concrete
confirmation that the skip machinery itself works, requested by the
coordinator before this file could be considered done.

### Migration procedure (for whoever runs this on the lab box)

1. Confirm host prerequisites (§2.3): driver + `nvidia-smi`,
   `nvidia-container-toolkit`, Compose CLI v2.
2. Bring the Dockerfile-with-`ARG` and `docker-compose.nvidia.yml` over
   **together** (merge Codex's branch, or port both files as a pair —
   see the pairing risk in §2.1).
3. `export BIOHUB_TORCH_INDEX_URL=https://download.pytorch.org/whl/cuXXX`
   (pick `cuXXX` for the installed driver — do not guess).
4. `docker compose -f docker-compose.yml -f docker-compose.nvidia.yml build`
5. `docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d`
6. `./scripts/verify_gpu_readiness.sh` — all 9 steps should now PASS, not skip.
7. Spot-check a real receipt:
   `uv run python scripts/run_detector_fixed_race.py materialize --sample <s> --device auto --max-frames 4 ...`
   then confirm the printed receipt JSON's `"device"` field reads `"cuda"`
   (this is the one step the pytest suite deliberately does not
   automate, to keep the suite itself fast — see step 8 above).
8. Only then consider a real timed 100-frame run (QUEUED-HEAVY today; see
   `reports/H.md` §5).

---

## 4. Payoff estimate

All numbers in this section are either (a) real measurements with a cited
source, or (b) explicitly labeled estimates with the reasoning shown — none
are fabricated. Per BRIEF ADDENDUM, the authoritative CPU baseline is the
**detector-fixed** run, not the earlier `strong_baseline_v1` number:

| Stage | Time | Device | Source |
|---|---:|---|---|
| Detector materialize (TemporalUNet3D encode × TTA + transformer edge scoring), 100 frames, 1 movie | **4,841.27 s** (~80.7 min) | `auto`→`cpu` | ADDENDUM A3; `docs/results/chatgpt_submission_report_ja.md` §14 ("detector elapsed `4,841.270636372006 s`") |
| Cache-only association × 4 methods + GEFF write + official metric, same movie, same cache | **116.29 s** total (~29 s/method) | cpu (no model involved at all) | ADDENDUM A4 |
| **One full race (detect once, score 4 methods)** | **4,957.56 s** (82.6 min) | — | sum of the above |

The detector is **97.7%** of one movie's wall time; association + GEFF +
metric is **2.3%**. A corroborating, independent prior measurement
(`strong_baseline_v1`, older single-method in-process runner, ~4,459.7 s)
lands in the same 74–81 minute band — two pipeline generations agree the
detector forward pass, not association, is what costs an hour-plus per
movie on this CPU container.

### What will NOT speed up on GPU at all (confirmed at the code level, §1 row 8-9, not just by timing)

- GEFF graph I/O (pure Python/`geff`, no torch).
- ILP/SCIP solving (`official_ilp` and the ILP portion of the others) —
  SCIP has no GPU path.
- The official metric (`official_metrics/metrics.py`) — graph
  matching/Jaccard, no torch.
- The classical association baselines (`benchmark_race/{cc_flow,motion,blob_lap}.py`)
  — hardcoded to `raise ValueError` for any non-`cpu` device.
- All four `detector_fixed_race` association methods themselves — confirmed
  cache-replay only (§1 row 2): no model, no device parameter on that CLI
  subcommand.

This class of work **is** the measured 116.29 s floor above, and it is a
hard floor — no GPU reduces it. It gets *relatively* more important, not
less, as the detector speeds up (classic Amdahl's law), and it **scales
with methods × samples** while the detector cost is paid once per sample
and reused — which is exactly why Codex's detector-fixed caching
architecture is the right one independent of this migration.

### Order-of-magnitude GPU speedup estimate (explicitly an estimate, not a measurement)

The 4,841.27 s is dominated by 3D convolutions: `TemporalUNet3D` has 3
encoder stages (channels 32→64→128), run over 99 sliding windows with 3×
TTA flips computed **sequentially** (not batched —
`predict_unet_transformer.py` ~380-388), so ≈396 UNet forward evaluations
per movie, plus a much smaller `SimpleNodeTransformer` edge-scoring cost
(attention over ~269 nodes/frame on average — 26,887 nodes / 100 frames).
3D convolutions are a workload class where CPU (even with oneDNN/MKL, and
this container's `torch 2.13.0+cpu` wheel does bundle it) is at its worst
disadvantage relative to GPU cuDNN kernels — a 10-30x range is a
defensible order-of-magnitude band for a model this size (8 MB checkpoint,
so not huge) on a modern lab RTX-class card. Applying that band only to the
detector time, keeping the 116.29 s floor fixed:

| Assumed detector speedup | Detector time | + fixed floor | Total pipeline time | **Overall speedup** |
|---:|---:|---:|---:|---:|
| 8× (conservative) | 605.2 s (10.1 min) | 116.3 s | 721.4 s (12.0 min) | **6.9×** |
| 15× (mid estimate) | 322.8 s (5.4 min) | 116.3 s | 439.0 s (7.3 min) | **11.3×** |
| 30× (optimistic) | 161.4 s (2.7 min) | 116.3 s | 277.7 s (4.6 min) | **17.9×** |

Even at an optimistic 30× raw detector speedup, the *overall* pipeline
speedup caps at ~18×, not 30× — the fixed CPU-only floor is why. The ~298
forced host↔device syncs per movie found in §1 row 7 (`.cpu().numpy()` per
frame/pair) add fixed per-sync latency that also does not shrink with a
faster GPU; at typical sync costs this is on the order of ~1 s total,
negligible against a multi-hundred-second detector time even in the
optimistic scenario, so it does not change the band above — but it is the
first place to look if a real lab-box measurement lands worse than this
estimate.

**Is the migration worth the disruption?** Yes, directionally: even the
conservative scenario turns an 82.6-minute single-movie race into a
12-minute one, and the gain compounds across the panel's 5 samples and
however many association methods the team ends up racing. But the
*specific* multiplier is not yet known — that is exactly what
`tests/test_gpu_readiness.py` step 9 and a real timed run on the lab box
are for.

### Memory: host RAM and VRAM, not just CUDA availability

Per the coordinator's 13:20 JST update, the CPU pipeline is **already**
memory-constrained on this 7.651 GiB container — Codex just landed two
memory-driven commits (`19feb13` "Stream detector pair captures to disk",
`8b03cd6` "Use chunked memmap edge cache validation") plus
`scripts/build_detector_cache_mmap.py`, specifically because one movie's
candidate edge set (7,240,938 edges / 198 MB compressed) would otherwise
risk exceeding the container's RAM budget alongside the ~4-5.6 GiB Codex's
own job has been observed holding. **This bottleneck is entirely
host-RAM-side and independent of which device the model runs on** —
moving the model to CUDA does not touch it, and the lab box should keep
the streaming/memmap architecture regardless of device, with system RAM
comfortably above the current container's 7.651 GiB ceiling (a real
target should be set once the lab box is known — this doc does not assert
one, since none is measured yet).

VRAM has no direct measurement (nobody has run this model on a GPU) — the
sliding-window architecture processes exactly one `(B=1, T=2)`-frame
window at a time, and TTA flips are sequential rather than batched, so
peak activation memory should be bounded by a single 2-frame-window
forward pass through a 3-stage, ≤128-channel 3D UNet — a reasoned estimate
of low-to-mid single-digit GB, likely comfortable on any modern 8GB+ card,
but **unverified**. `tests/test_gpu_readiness.py` step 7 exercises the
real model shape on real hardware the moment a GPU is available; it does
not yet record `torch.cuda.max_memory_allocated()` — a one-line follow-up
worth adding once there is a GPU to run it on (noted in `reports/H.md` §7).

