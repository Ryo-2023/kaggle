import dataclasses
import json
from pathlib import Path

import pytest

from biohub.benchmark_race.cache import build_cache_manifest
from biohub.benchmark_race.contracts import MethodSpec, RaceRequest, SampleSpec


def _sample(**overrides: object) -> SampleSpec:
    values: dict[str, object] = {
        "sample_id": "44b6_0113de3b",
        "image_stem": "44b6_0113de3b",
        "shape": (100, 64, 256, 256),
        "scale": (1.625, 0.40625, 0.40625),
        "quantiles": {"0.001": 0.0, "0.999": 1.0},
    }
    values.update(overrides)
    return SampleSpec(**values)


def test_sample_spec_rejects_geff_image_path() -> None:
    with pytest.raises(ValueError, match=r"image.*\.geff"):
        _sample(image_stem=Path("44b6_0113de3b.geff"))


def test_sample_spec_requires_tzyx_shape() -> None:
    with pytest.raises(ValueError, match=r"shape.*4"):
        _sample(shape=(64, 256, 256))


def test_sample_spec_rejects_nonpositive_or_nonfinite_scale() -> None:
    with pytest.raises(ValueError, match="scale"):
        _sample(scale=(1.625, 0.0, 0.40625))

    with pytest.raises(ValueError, match="scale"):
        _sample(scale=(1.625, float("nan"), 0.40625))


def test_race_request_has_image_only_fields_and_rejects_gt_config() -> None:
    field_names = {field.name for field in dataclasses.fields(RaceRequest)}
    assert not field_names.intersection({"gt_path", "ground_truth", "ground_truth_path"})

    with pytest.raises(ValueError, match=r"ground.?truth"):
        RaceRequest(
            sample=_sample(),
            cache_root=Path("cache"),
            output_root=Path("output"),
            expected_device="cpu",
            config={"ground_truth_path": "/data/44b6_0113de3b.geff"},
        )


def test_race_request_rejects_geff_image() -> None:
    with pytest.raises(ValueError, match=r"image.*\.geff"):
        RaceRequest(
            sample=_sample(image_stem="44b6_0113de3b.geff"),
            cache_root=Path("cache"),
            output_root=Path("output"),
            expected_device="cpu",
            config={},
        )


@pytest.mark.parametrize(
    "config",
    [
        {"path": "/data/gt.json"},
        {"path": "annotations/labels.json"},
        {"annotation_file": "labels.json"},
        {"groundTruthPath": "metadata.json"},
        {"gtPath": "metadata.json"},
        {"path": "gtdata.json"},
        {"path": "groundtruth_v1.json"},
    ],
)
def test_race_request_rejects_generic_ground_truth_config_paths(config: dict[str, str]) -> None:
    with pytest.raises(ValueError, match=r"ground.?truth|GT|annotation|label"):
        RaceRequest(
            sample=_sample(),
            cache_root=Path("artifacts/cache"),
            output_root=Path("artifacts/output"),
            expected_device="cpu",
            config=config,
        )


def test_sample_spec_rejects_ground_truth_json_image_name() -> None:
    with pytest.raises(ValueError, match=r"ground.?truth|\.json"):
        _sample(image_stem="ground-truth.json")


def test_cache_manifest_rejects_ground_truth_schema_version() -> None:
    with pytest.raises(ValueError, match=r"ground.?truth"):
        build_cache_manifest(
            sample=_sample(),
            image_digest="image-sha256",
            detector_config={},
            source_commit="source-commit",
            schema_version="ground-truth",
        )


@pytest.mark.parametrize(
    "root_name",
    ["cache.geff/cache", "ground-truth/cache", "groundTruthCache/cache", "gtcache/cache"],
)
def test_race_request_rejects_ground_truth_cache_root_components(root_name: str) -> None:
    with pytest.raises(ValueError, match=r"path|ground.?truth|\.geff"):
        RaceRequest(
            sample=_sample(),
            cache_root=Path(root_name),
            output_root=Path("artifacts/output"),
            expected_device="cpu",
            config={},
        )


@pytest.mark.parametrize("root_name", ["predictions.geff/output", "ground-truth/output"])
def test_race_request_rejects_ground_truth_output_root_components(root_name: str) -> None:
    with pytest.raises(ValueError, match=r"path|ground.?truth|\.geff"):
        RaceRequest(
            sample=_sample(),
            cache_root=Path("artifacts/cache"),
            output_root=Path(root_name),
            expected_device="cpu",
            config={},
        )


def test_sample_spec_rejects_absolute_image_stem() -> None:
    with pytest.raises(ValueError, match=r"absolute|relative|image"):
        _sample(image_stem=Path("/data/44b6_0113de3b.zarr"))


def test_race_request_rejects_absolute_artifact_roots() -> None:
    with pytest.raises(ValueError, match=r"absolute|relative|path"):
        RaceRequest(
            sample=_sample(),
            cache_root=Path("/tmp/cache"),
            output_root=Path("artifacts/output"),
            expected_device="cpu",
            config={},
        )

    with pytest.raises(ValueError, match=r"absolute|relative|path"):
        RaceRequest(
            sample=_sample(),
            cache_root=Path("artifacts/cache"),
            output_root=Path("/tmp/output"),
            expected_device="cpu",
            config={},
        )


def test_relative_zarr_image_and_artifact_paths_remain_valid() -> None:
    request = RaceRequest(
        sample=_sample(image_stem="images/44b6_0113de3b.zarr"),
        cache_root=Path("artifacts/cache"),
        output_root=Path("artifacts/output"),
        expected_device="cpu",
        config={"normalization": "config.json"},
    )

    assert request.sample.image_stem == Path("images/44b6_0113de3b.zarr")


def test_cache_manifest_rejects_host_dependent_absolute_image_path() -> None:
    with pytest.raises(ValueError, match=r"absolute|relative|image"):
        _sample(image_stem=Path("/host/data/sample.zarr"))


def test_method_spec_normalizes_requirements() -> None:
    method = MethodSpec(
        method_id="blob_lap",
        family="classical",
        detector_id="blob",
        linker_id="lap",
        version="v1",
        requires=["numpy", "scipy"],
    )

    assert method.requires == ("numpy", "scipy")


def test_cache_manifest_is_deterministic_and_excludes_ground_truth() -> None:
    sample = _sample()
    first = build_cache_manifest(
        sample=sample,
        image_digest="image-sha256",
        detector_config={"sigma": 1.0, "threshold": 0.9},
        source_commit="source-commit",
        checkpoint_sha256="checkpoint-sha256",
    )
    second = build_cache_manifest(
        sample=sample,
        image_digest="image-sha256",
        detector_config={"threshold": 0.9, "sigma": 1.0},
        source_commit="source-commit",
        checkpoint_sha256="checkpoint-sha256",
    )

    assert first == second
    assert first["ground_truth_included"] is False
    assert "ground_truth_path" not in first
    assert "ground_truth_digest" not in first
    encoded = json.dumps(first, sort_keys=True)
    assert "ground_truth_path" not in encoded
    assert "ground_truth_digest" not in encoded


def test_cache_manifest_rejects_ground_truth_text_in_detector_config() -> None:
    with pytest.raises(ValueError, match=r"ground.?truth"):
        build_cache_manifest(
            sample=_sample(),
            image_digest="image-sha256",
            detector_config={"annotation_path": "ground_truth.geff"},
            source_commit="source-commit",
        )
