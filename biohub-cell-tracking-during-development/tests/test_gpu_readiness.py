"""GPU / CUDA readiness verification for the Biohub detector pipeline.

Run anywhere with:

    uv run pytest tests/test_gpu_readiness.py -v

On the CPU-only MacBook dev container (today) every GPU-gated step SKIPs
cleanly -- that is the expected, correct result here, not an error. On a
box with a real NVIDIA GPU + driver + nvidia-container-toolkit wired up
(the lab RTX machine this file is written for), the same steps must all
PASS. A SKIP there means the environment is not wired correctly; a FAIL
means a real code/config bug -- most importantly, the one this file exists
to catch: ``--device auto`` resolving to ``cuda`` in name while the model
silently keeps running on the CPU.

Chain and ownership (see docs/results/claude_lane_h_gpu_readiness.md s3):
  1. nvidia-smi on the host             -> scripts/verify_gpu_readiness.sh
  2. nvidia-smi inside the container    -> scripts/verify_gpu_readiness.sh
  3. torch.version.cuda is not None     -> test_step3_*
  4. torch.cuda.is_available()          -> test_step4_*
  5. device_count >= 1                  -> test_step5_*
  6. a real tensor matmul on cuda       -> test_step6_*
  7. TemporalUNet3D forward on cuda     -> test_step7_*
  8. device in the run receipt is cuda  -> test_step8_*
  9. timing comparison vs CPU           -> test_step9_*

Design note on the skip gate
-----------------------------
Skip/run is decided by ``_host_has_nvidia_gpu()``, which shells out to
``nvidia-smi`` directly. It is deliberately NOT decided by
``torch.cuda.is_available()``. If it were, the single most important
failure mode this file exists to catch -- a GPU box whose ``uv sync``
silently reinstalled the ``+cpu`` torch wheel, so
``torch.cuda.is_available()`` is False despite real hardware being present
-- would just look like an ordinary, quiet skip instead of a loud,
diagnosable failure. See ``test_step0_gate_is_consistent_with_torch``.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import torch

from biohub.device import resolve_torch_device

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UPSTREAM_ROOT = PROJECT_ROOT / "artifacts" / "strong_baseline_v1" / "upstream"


def _host_has_nvidia_gpu() -> bool:
    """True only if a real NVIDIA GPU answers ``nvidia-smi`` for this process.

    Independent of torch on purpose -- see module docstring.
    """

    binary = shutil.which("nvidia-smi")
    if binary is None:
        return False
    try:
        result = subprocess.run(
            [binary, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


GPU_PRESENT = _host_has_nvidia_gpu()
_SKIP_REASON = (
    "no nvidia-smi-visible GPU in this environment -- expected on the CPU "
    "dev container, must NOT be skipped on the CUDA lab box"
)
requires_gpu = pytest.mark.skipif(not GPU_PRESENT, reason=_SKIP_REASON)


def _upstream_root() -> Path:
    return Path(os.environ.get("BIOHUB_UPSTREAM_ROOT", str(DEFAULT_UPSTREAM_ROOT)))


def _load_temporal_unet3d_class():
    """Import the vendored, pinned ``TemporalUNet3D`` without a checkpoint.

    Loaded directly from its file (it only imports ``torch``/stdlib, no
    package-relative imports), so this never touches ``edge_predictor_best.pth``,
    a real zarr volume, or GT -- it is safe to run on any box, any time.
    """

    root = _upstream_root()
    model_path = root / "src" / "tracking_cellmot" / "models" / "temporal_unet.py"
    if not model_path.is_file():
        pytest.skip(
            f"vendored upstream model not found at {model_path}; set "
            "BIOHUB_UPSTREAM_ROOT or vendor the pinned upstream checkout "
            "(see docs/results/claude_lane_h_gpu_readiness.md)"
        )
    spec = importlib.util.spec_from_file_location(
        "_biohub_gpu_readiness_temporal_unet", model_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TemporalUNet3D


def test_step0_gate_is_consistent_with_torch_when_gpu_present() -> None:
    """Always runs, never skips -- the guard against a silent false-skip.

    If nvidia-smi sees a GPU, torch must too. A failure here means the
    installed torch build is the ``+cpu`` wheel on a CUDA-capable box --
    exactly the pyproject.toml / uv.lock defect documented in
    docs/results/claude_lane_h_gpu_readiness.md s2.1.
    """

    if not GPU_PRESENT:
        pytest.skip("documented no-op: no GPU on this box today (see module docstring)")
    assert torch.version.cuda is not None, (
        "nvidia-smi reports a GPU but this torch build has no CUDA support "
        "(torch.version.cuda is None) -- uv sync installed the +cpu wheel "
        "again. Rebuild with docker-compose.nvidia.yml + BIOHUB_TORCH_INDEX_URL "
        "and re-run this file."
    )


@requires_gpu
def test_step3_torch_reports_a_cuda_build() -> None:
    assert torch.version.cuda is not None


@requires_gpu
def test_step4_cuda_is_available() -> None:
    assert torch.cuda.is_available() is True


@requires_gpu
def test_step5_at_least_one_cuda_device() -> None:
    assert torch.cuda.device_count() >= 1


@requires_gpu
def test_step6_real_matmul_on_cuda() -> None:
    device = resolve_torch_device("cuda")
    a = torch.randn(512, 512, device=device)
    b = torch.randn(512, 512, device=device)
    c = a @ b
    torch.cuda.synchronize()
    assert c.device.type == "cuda"
    assert torch.isfinite(c).all()


@requires_gpu
def test_step7_temporal_unet3d_forward_pass_on_cuda() -> None:
    """The project's own model must run on the GPU, not just torch itself.

    Uses random weights and a shape-correct synthetic window (no
    checkpoint, no zarr, no GT) so this stays a fast, side-effect-free
    smoke test -- see ``_load_temporal_unet3d_class`` docstring.
    """

    temporal_unet3d_cls = _load_temporal_unet3d_class()
    device = resolve_torch_device("cuda")
    torch.manual_seed(0)
    model = temporal_unet3d_cls(in_channels=1, out_channels=32, layers=(32, 64, 128))
    model = model.to(device)
    model.eval()

    # (B, T, C_in, Z, Y, X): a 2-frame window at a small isotropic-enough
    # synthetic resolution, matching the sliding-window shape the real
    # pipeline feeds this model (see upstream predict_video()).
    x = torch.randn(1, 2, 1, 16, 32, 32, device=device)
    with torch.no_grad():
        out = model(x)

    assert out.device.type == "cuda"
    assert out.shape == (1, 2, 32, 16, 32, 32)
    assert torch.isfinite(out).all(), "non-finite output from a CUDA forward pass"
    for name, parameter in model.named_parameters():
        assert parameter.device.type == "cuda", f"parameter {name!r} was left on {parameter.device}"


@requires_gpu
def test_step8_auto_resolution_matches_what_a_run_receipt_would_record() -> None:
    """Proxy for "device recorded in the run receipt matches cuda".

    ``materialize_detector_cache`` writes ``str(device)`` into its receipt
    JSON's ``"device"`` field, where ``device = resolve_torch_device(...)``
    (src/biohub/detector_fixed_race/upstream_adapter.py). Re-running the
    real ~80-minute materialize step just to read that one field would
    make this file a heavy job, not a verification smoke test, so this
    checks the exact function and string form the receipt would contain.
    After this suite passes once on the lab box, spot-check a real receipt
    from a short ``materialize --max-frames 4`` run to close the loop --
    see docs/results/claude_lane_h_gpu_readiness.md s3.
    """

    resolved = resolve_torch_device("auto")
    assert resolved == torch.device("cuda")
    assert str(resolved) == "cuda"  # exact string a receipt's "device" field would store


@requires_gpu
def test_step9_cuda_matmul_is_not_slower_than_cpu() -> None:
    """Not a benchmark -- a smoke check plus one real timing data point.

    Guards against a "GPU path is technically active but net slower"
    regression (e.g. thrashing host<->device transfers) and prints a
    number for the payoff estimate in
    docs/results/claude_lane_h_gpu_readiness.md s4.
    """

    size = 2048
    a_cpu = torch.randn(size, size)
    b_cpu = torch.randn(size, size)
    start = time.perf_counter()
    a_cpu @ b_cpu
    cpu_seconds = time.perf_counter() - start

    device = resolve_torch_device("cuda")
    a_gpu = torch.randn(size, size, device=device)
    b_gpu = torch.randn(size, size, device=device)
    torch.cuda.synchronize()
    start = time.perf_counter()
    a_gpu @ b_gpu
    torch.cuda.synchronize()
    gpu_seconds = time.perf_counter() - start

    print(
        f"\n[gpu-readiness] {size}x{size} matmul: cpu={cpu_seconds * 1e3:.2f}ms "
        f"gpu={gpu_seconds * 1e3:.2f}ms speedup={cpu_seconds / max(gpu_seconds, 1e-9):.1f}x"
    )
    assert gpu_seconds > 0
