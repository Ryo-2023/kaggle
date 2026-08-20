"""Image-only contracts shared by the benchmark-race adapters.

The race deliberately keeps the inference request smaller than an evaluation
request: a method receives an image sample and output locations, never a
ground-truth graph.  Validation lives at this boundary so that an accidental
``.geff`` input or a non-``(T, Z, Y, X)`` sample fails before an adapter runs.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_GROUND_TRUTH_KEY_RE = re.compile(
    r"(?:^|[_\-.])(ground[_\-.]?truth|gt|truth|annotation|annotations|label|labels)(?:$|[_\-.])",
    re.IGNORECASE,
)


def _is_ground_truth_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
    return bool(
        _GROUND_TRUTH_KEY_RE.search(key)
        or normalized in {
            "groundtruth",
            "ground_truth",
            "ground_truth_path",
            "ground_truth_digest",
            "gt",
            "gt_path",
            "gt_digest",
            "truth",
            "truth_path",
            "annotation",
            "annotation_path",
            "annotations",
            "label",
            "labels",
        }
    )


def _contains_ground_truth(value: object, *, key: object | None = None) -> bool:
    """Return whether *value* contains a likely ground-truth reference.

    Config is untrusted input at the image-only boundary.  We reject rather
    than strip suspicious values, because silently removing a GT setting can
    turn a caller mistake into an incomparable benchmark run.
    """

    if _is_ground_truth_key(key):
        return True
    if isinstance(value, Path):
        return value.suffix.lower() == ".geff" or "ground_truth" in value.as_posix().lower()
    if isinstance(value, str):
        lowered = value.lower()
        return lowered.endswith(".geff") or "ground_truth" in lowered or "groundtruth" in lowered
    if isinstance(value, Mapping):
        return any(_contains_ground_truth(item, key=item_key) for item_key, item in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_ground_truth(item) for item in value)
    return False


def _normalise_json_value(value: Any) -> Any:
    """Convert supported contract values to deterministic JSON-compatible data."""

    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        normalised: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("config mapping keys must be strings")
            normalised[key] = _normalise_json_value(item)
        return {key: normalised[key] for key in sorted(normalised)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalise_json_value(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("config values must be finite")
        return value
    raise TypeError(f"unsupported config value type: {type(value).__name__}")


def _require_nonempty_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _validate_image_stem(value: str | Path) -> Path:
    image_stem = Path(value)
    if not image_stem.name:
        raise ValueError("image stem must not be empty")
    if image_stem.suffix.lower() == ".geff" or _contains_ground_truth(image_stem):
        raise ValueError("image stem must identify an image, not a .geff ground-truth graph")
    return image_stem


@dataclass(frozen=True, slots=True)
class SampleSpec:
    """Metadata for one image-only sample in ``(T, Z, Y, X)`` order."""

    sample_id: str
    image_stem: str | Path
    shape: tuple[int, int, int, int]
    scale: tuple[float, float, float]
    quantiles: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sample_id", _require_nonempty_text("sample_id", self.sample_id))
        if self.sample_id.lower().endswith(".geff") or _contains_ground_truth(self.sample_id):
            raise ValueError("sample_id must identify an image, not a .geff ground-truth graph")
        object.__setattr__(self, "image_stem", _validate_image_stem(self.image_stem))

        if not isinstance(self.shape, Sequence) or isinstance(self.shape, (str, bytes)) or len(self.shape) != 4:
            raise ValueError("shape must contain exactly 4 dimensions in (T, Z, Y, X) order")
        shape = tuple(self.shape)
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in shape):
            raise ValueError("shape must contain positive integer dimensions in (T, Z, Y, X) order")
        object.__setattr__(self, "shape", shape)

        if not isinstance(self.scale, Sequence) or isinstance(self.scale, (str, bytes)) or len(self.scale) != 3:
            raise ValueError("scale must contain exactly 3 values in (Z, Y, X) order")
        scale = tuple(self.scale)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0.0
            for value in scale
        ):
            raise ValueError("scale must contain positive finite values in (Z, Y, X) order")
        object.__setattr__(self, "scale", tuple(float(value) for value in scale))

        if not isinstance(self.quantiles, Mapping) or not self.quantiles:
            raise ValueError("quantiles must be a non-empty mapping")
        if _contains_ground_truth(self.quantiles):
            raise ValueError("quantiles must not contain a ground-truth reference")
        quantiles = _normalise_json_value(self.quantiles)
        if not isinstance(quantiles, dict):  # pragma: no cover - guarded by Mapping check above
            raise TypeError("quantiles must be a mapping")
        for key, value in quantiles.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("quantile keys must be non-empty strings")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError("quantile values must be finite numbers")
        object.__setattr__(self, "quantiles", quantiles)


@dataclass(frozen=True, slots=True)
class RaceRequest:
    """Image-only request passed to a benchmark-race method adapter."""

    sample: SampleSpec
    cache_root: Path
    output_root: Path
    expected_device: str
    config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.sample, SampleSpec):
            raise TypeError("sample must be a SampleSpec")
        cache_root = Path(self.cache_root)
        output_root = Path(self.output_root)
        if cache_root.suffix.lower() == ".geff" or output_root.suffix.lower() == ".geff":
            raise ValueError("cache_root and output_root must be directories, not .geff graphs")
        object.__setattr__(self, "cache_root", cache_root)
        object.__setattr__(self, "output_root", output_root)
        object.__setattr__(self, "expected_device", _require_nonempty_text("expected_device", self.expected_device))
        if not isinstance(self.config, Mapping):
            raise TypeError("config must be a mapping")
        if _contains_ground_truth(self.config):
            raise ValueError("image-only race request must not contain a ground-truth reference")
        object.__setattr__(self, "config", _normalise_json_value(self.config))


@dataclass(frozen=True, slots=True)
class MethodSpec:
    """Provenance identifiers for one detector/linker method family."""

    method_id: str
    family: str
    detector_id: str
    linker_id: str
    version: str
    requires: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in ("method_id", "family", "detector_id", "linker_id", "version"):
            object.__setattr__(self, name, _require_nonempty_text(name, getattr(self, name)))
        if isinstance(self.requires, (str, bytes)) or not isinstance(self.requires, Sequence):
            raise TypeError("requires must be a sequence of package names")
        requirements = tuple(_require_nonempty_text("requirement", item) for item in self.requires)
        object.__setattr__(self, "requires", requirements)


__all__ = ["MethodSpec", "RaceRequest", "SampleSpec"]
