from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from biohub.benchmark_race.contracts import SampleSpec
from biohub.detector_fixed_race.cache import load_detector_cache
from biohub.detector_fixed_race.upstream_adapter import (
    CaptureConfig,
    _assign_features,
    materialize_detector_cache,
)


@pytest.fixture
def sample_spec() -> SampleSpec:
    return SampleSpec(
        sample_id="fake-01",
        image_stem=Path("images/fake-01.zarr"),
        shape=(2, 2, 4, 4),
        scale=(1.5, 0.5, 0.5),
        quantiles={"0.001": 0.0, "0.999": 1.0},
    )


@pytest.fixture
def fake_upstream(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(predict_video_calls=0, encode_calls=0, forward_calls=0, reverse_calls=0)
    module_holder: dict[str, SimpleNamespace] = {}

    class FakePredictConfig:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    class FakeModel:
        def predict_edges(
            self,
            feat_source: torch.Tensor,
            feat_target: torch.Tensor,
            coords_source: torch.Tensor,
            coords_target: torch.Tensor,
            pos_source: torch.Tensor,
            pos_target: torch.Tensor,
            mask_source: torch.Tensor,
            mask_target: torch.Tensor,
        ) -> torch.Tensor:
            del coords_source, coords_target, pos_source, pos_target, mask_source, mask_target
            state.forward_calls += 1
            # A deterministic matrix with the expected (B, N_source, N_target) shape.
            return torch.arange(
                feat_source.shape[1] * feat_target.shape[1], dtype=torch.float32
            ).reshape(1, feat_source.shape[1], feat_target.shape[1])

    model = FakeModel()

    def load_model(weights_path: Path, device: torch.device) -> tuple[FakeModel, int, tuple[int, ...]]:
        del weights_path, device
        return model, 2, (1, 1, 1)

    def detect_cells_pooled(
        det_logits: torch.Tensor,
        t: int,
        det_threshold: float,
        pool_kernel: tuple[int, ...],
    ) -> np.ndarray:
        del det_logits, det_threshold, pool_kernel
        return np.array([[t, 0, 0, 0], [t, 0, 1, 1]], dtype=np.int16)

    def predict_video(
        model_arg: FakeModel,
        ds_path: Path,
        device: torch.device,
        cfg: FakePredictConfig,
        *,
        window_size: int,
        max_frames: int | None,
        unet_batch_size: int,
        downsample: tuple[int, ...],
    ) -> tuple[np.ndarray, list[tuple[int, int, float, float]]]:
        del ds_path, device, cfg, window_size, max_frames, unet_batch_size, downsample
        state.predict_video_calls += 1
        # The adapter must observe detector output through the patched helper.
        det = torch.zeros((1, 2, 2, 2), dtype=torch.float32)
        module_holder["module"]._detect_cells_pooled(det, 0, 0.99, (3, 3, 3))
        module_holder["module"]._detect_cells_pooled(det, 1, 0.99, (3, 3, 3))
        feat_source = torch.ones((1, 2, 2), dtype=torch.float32)
        feat_target = torch.full((1, 2, 2), 2.0, dtype=torch.float32)
        coords_source = torch.tensor([[[0.0, 0.0, 0.0], [0.0, 1.0, 1.0]]])
        coords_target = coords_source.clone()
        positions = torch.zeros((1, 2, 4), dtype=torch.float32)
        mask = torch.ones((1, 2), dtype=torch.bool)
        model_arg.predict_edges(
            feat_source,
            feat_target,
            coords_source,
            coords_target,
            positions,
            positions,
            mask,
            mask,
        )
        coords = np.array(
            [[0, 0, 0, 0], [0, 0, 1, 1], [1, 0, 0, 0], [1, 0, 1, 1]], dtype=np.int16
        )
        return coords, []

    fake = SimpleNamespace(
        load_model=load_model,
        PredictConfig=FakePredictConfig,
        _detect_cells_pooled=detect_cells_pooled,
        predict_video=predict_video,
        build_graph=lambda coords, edges: (coords, edges),
        state=state,
        model=model,
    )
    module_holder["module"] = fake

    monkeypatch.setattr(
        "biohub.detector_fixed_race.upstream_adapter._load_upstream_predictor",
        lambda upstream_root: fake,
    )
    return fake


def test_materializer_calls_predict_video_once_and_writes_forward_reverse_logits(
    tmp_path: Path,
    sample_spec: SampleSpec,
    fake_upstream: SimpleNamespace,
) -> None:
    image_path = tmp_path / "fake.zarr"
    image_path.mkdir()
    (image_path / "zarr.json").write_text("{}", encoding="utf-8")
    checkpoint = tmp_path / "checkpoint.pth"
    checkpoint.write_bytes(b"checkpoint")

    receipt = materialize_detector_cache(
        image_path=image_path,
        upstream_root=tmp_path / "upstream",
        checkpoint=checkpoint,
        output_root=tmp_path / "output",
        sample=sample_spec,
        config=CaptureConfig(),
        expected_device="cpu",
    )
    cache = load_detector_cache(receipt.root)
    assert fake_upstream.state.predict_video_calls == 1
    assert fake_upstream.state.forward_calls == 2
    assert cache.edges.forward_logit.shape == cache.edges.reverse_logit.shape
    assert cache.nodes.length == 4
    assert cache.manifest["ground_truth_included"] is False
    assert cache.manifest["provenance"]["reverse_edge_call_count"] == 1


def test_assign_features_keeps_first_contextual_observation_and_counts_conflict() -> None:
    target = np.full((1, 2), np.nan, dtype=np.float32)
    seen = np.zeros((1,), dtype=bool)

    assert _assign_features(
        target,
        seen,
        np.array([0], dtype=np.int64),
        np.array([[1.0, 2.0]], dtype=np.float32),
    ) == 0
    assert _assign_features(
        target,
        seen,
        np.array([0], dtype=np.int64),
        np.array([[1.01, 2.0]], dtype=np.float32),
    ) == 1
    np.testing.assert_array_equal(target, np.array([[1.0, 2.0]], dtype=np.float32))
