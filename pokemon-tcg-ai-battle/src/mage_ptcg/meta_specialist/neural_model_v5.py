"""Research-only V5 SetContext sidecar for the closed V4 specialist model.

V5 deliberately keeps the V4 encoder, recurrent transition, and STOP path
unchanged.  A zero-initialized candidate-set head adds only a permutation
equivariant semantic-logit residual, while a dedicated manifest records the
exact V4 artifact used for the transfer.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import io
import json
import os
from pathlib import Path
import stat
from types import MappingProxyType

import torch
from torch import nn

from mage_ptcg.meta_specialist import neural_model_v4 as neural_v4
from mage_ptcg.meta_specialist.neural_model_v4 import (
    ActionCandidateV4,
    NeuralModelV4Error,
    PolicyOutputV4,
    SpecialistModelV4,
    StateEncodingV4,
)
from mage_ptcg.meta_specialist.representation_v4 import (
    REPRESENTATION_V4_SCHEMA,
    RelationalStateV4,
)


NEURAL_MODEL_SCHEMA_V5 = "specialist-neural-model-v5-set-context-sidecar"
CHECKPOINT_SCHEMA_V5 = "specialist-neural-checkpoint-v5-set-context-sidecar"
SET_CONTEXT_HEAD_VERSION_V5 = "candidate-mean-count-residual-v1"
STOP_POLICY_V5 = "base-global-v4"
_SCHEMA_MARKER_V5 = hashlib.sha256(
    f"{REPRESENTATION_V4_SCHEMA}\0{NEURAL_MODEL_SCHEMA_V5}\0{SET_CONTEXT_HEAD_VERSION_V5}".encode("ascii")
).digest()
_IMPLEMENTATION_DIGEST_PREFIX_V5 = b"mage_ptcg:specialist-implementation-closure:v5-set-context\0"
_TRANSFER_ALLOWLIST_PREFIX_V5 = b"mage_ptcg:specialist-v4-transfer-allowlist:v5-set-context\0"
_TENSOR_STATE_PREFIX_V5 = b"mage_ptcg:specialist-neural-state:v5-set-context\0"


class NeuralModelV5Error(ValueError):
    """Raised when a V5 model or sidecar artifact violates its closed contract."""


def _seeded_v5(seed: int, factory) -> None:
    if type(seed) is not int:
        raise NeuralModelV5Error("seed must be int")
    rng = torch.random.get_rng_state()
    try:
        torch.manual_seed(seed)
        factory()
    finally:
        torch.random.set_rng_state(rng)


def _require_sha256_v5(value: object, *, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise NeuralModelV5Error(f"{name} must be a lowercase SHA-256")
    return value


def _canonical_json_bytes_v5(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NeuralModelV5Error("v5 manifest value must be canonical JSON") from exc


def _allowlist_sha256_v5(allowlist: tuple[str, ...] | list[str]) -> str:
    if type(allowlist) not in (tuple, list) or any(type(value) is not str for value in allowlist):
        raise NeuralModelV5Error("v5 transfer allowlist must contain strings")
    ordered = tuple(sorted(allowlist))
    if len(ordered) != len(set(ordered)):
        raise NeuralModelV5Error("v5 transfer allowlist must not contain duplicate keys")
    digest = hashlib.sha256(_TRANSFER_ALLOWLIST_PREFIX_V5)
    for name in ordered:
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big") + encoded)
    return digest.hexdigest()


def _tensor_state_sha256_v5(state_dict: Mapping[str, torch.Tensor]) -> str:
    digest = hashlib.sha256(_TENSOR_STATE_PREFIX_V5)
    for name in sorted(state_dict):
        tensor = state_dict[name]
        if type(name) is not str or type(tensor) is not torch.Tensor or tensor.layout != torch.strided:
            raise NeuralModelV5Error("v5 checkpoint state must contain dense named tensors")
        value = tensor.detach().cpu().contiguous()
        if (value.is_floating_point() or value.is_complex()) and not torch.isfinite(value).all():
            raise NeuralModelV5Error("v5 checkpoint state contains nonfinite tensors")
        raw = value.reshape(-1).view(torch.uint8).numpy().tobytes()
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(str(value.dtype).encode("ascii") + b"\0")
        digest.update(str(tuple(value.shape)).encode("ascii") + b"\0")
        digest.update(len(raw).to_bytes(8, "big") + raw)
    return digest.hexdigest()


def _stable_source_bytes_v5(path: Path) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink():
        raise NeuralModelV5Error("v5 implementation source identity is invalid")
    try:
        if path.resolve(strict=True) != path:
            raise NeuralModelV5Error("v5 implementation source identity is invalid")
    except OSError as exc:
        raise NeuralModelV5Error("v5 implementation source cannot be resolved") from exc
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NeuralModelV5Error("v5 implementation source cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise NeuralModelV5Error("v5 implementation source must be a regular file")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise NeuralModelV5Error("v5 implementation source cannot be read") from exc
    finally:
        os.close(descriptor)
    identity_fields = ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in identity_fields):
        raise NeuralModelV5Error("v5 implementation source changed while reading")
    payload = b"".join(chunks)
    if len(payload) != before.st_size:
        raise NeuralModelV5Error("v5 implementation source changed while reading")
    return payload


def _implementation_digest_v5() -> str:
    live_path = Path(__file__).resolve(strict=True)
    v4_path = Path(neural_v4.__file__).resolve(strict=True)
    representation_path = Path(neural_v4.representation_v4_module.__file__).resolve(strict=True)
    sources = (
        ("representation_v4.py", representation_path),
        ("neural_model_v4.py", v4_path),
        ("neural_model_v5.py", live_path),
    )
    digest = hashlib.sha256(_IMPLEMENTATION_DIGEST_PREFIX_V5)
    for name, path in sources:
        payload = _stable_source_bytes_v5(path)
        encoded_name = name.encode("ascii")
        digest.update(len(encoded_name).to_bytes(4, "big") + encoded_name)
        digest.update(len(payload).to_bytes(8, "big") + payload)
    return digest.hexdigest()


def _checkpoint_snapshot_bytes_v5(path: Path, *, expected_file_sha256: str) -> bytes:
    expected = _require_sha256_v5(expected_file_sha256, name="expected_file_sha256")
    if not hasattr(os, "O_NOFOLLOW"):
        raise NeuralModelV5Error("v5 checkpoint cannot be opened safely without O_NOFOLLOW")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NeuralModelV5Error("v5 checkpoint cannot be opened safely; symlinks are forbidden") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise NeuralModelV5Error("v5 checkpoint must be a regular file")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            chunks.append(block)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise NeuralModelV5Error("v5 checkpoint cannot be read safely") from exc
    finally:
        os.close(descriptor)
    identity_fields = ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in identity_fields):
        raise NeuralModelV5Error("v5 checkpoint changed while reading")
    raw = b"".join(chunks)
    if len(raw) != before.st_size:
        raise NeuralModelV5Error("v5 checkpoint changed while reading")
    if digest.hexdigest() != expected:
        raise NeuralModelV5Error("v5 checkpoint external file SHA-256 does not match")
    return raw


def _model_config_v5(model: SpecialistModelV4) -> dict[str, int]:
    expected = dict(model._model_config)
    if set(expected) != {"card_vocabulary_size", "hidden_dim", "embedding_dim", "state_scalar_dim"}:
        raise NeuralModelV5Error("v5 base model config is invalid")
    if any(type(value) is not int or value < 1 for value in expected.values()):
        raise NeuralModelV5Error("v5 model dimensions must be positive integers")
    return expected


def _head_config_v5() -> dict[str, str]:
    return {
        "version": SET_CONTEXT_HEAD_VERSION_V5,
        "stop_policy": STOP_POLICY_V5,
        "pool": "valid-candidate-mean",
        "count": "valid-candidate-count-div-512",
        "residual": "candidate-context-elementwise-product",
    }


def _v4_transfer_state_keys_v5(model: "SpecialistModelV5") -> tuple[str, ...]:
    """Return only the keys owned by the inherited V4 topology.

    ``nn.Module.state_dict`` is dynamic: calling the inherited V4 method on a
    V5 instance still walks the V5 head.  The explicit exclusions below keep
    the transfer allowlist closed to the state that a pure V4 instance owns.
    """
    state_keys = nn.Module.state_dict(model).keys()
    v5_owned = (
        "_schema_marker_v5",
        "candidate_context_projection.",
        "candidate_residual_head.",
    )
    return tuple(sorted(
        name for name in state_keys
        if name != v5_owned[0] and not any(name.startswith(prefix) for prefix in v5_owned[1:])
    ))


class SpecialistModelV5(SpecialistModelV4):
    """V4 base plus a zero-initialized permutation-equivariant set head."""

    def __init__(
        self,
        *,
        card_vocabulary_size: int,
        hidden_dim: int = 256,
        embedding_dim: int = 192,
        seed: int = 0,
        state_scalar_dim: int = 41,
    ) -> None:
        super().__init__(
            card_vocabulary_size=card_vocabulary_size,
            hidden_dim=hidden_dim,
            embedding_dim=embedding_dim,
            seed=seed,
            state_scalar_dim=state_scalar_dim,
        )

        def build_head() -> None:
            self.candidate_context_projection = nn.Sequential(
                nn.Linear(hidden_dim * 2 + 1, hidden_dim),
                nn.GELU(),
                nn.Linear(hidden_dim, hidden_dim),
            )
            self.candidate_residual_head = nn.Sequential(
                nn.Linear(hidden_dim * 3, hidden_dim),
                nn.GELU(),
                nn.Linear(hidden_dim, 1),
            )
            # The new path is an exact identity until the sidecar is trained.
            nn.init.zeros_(self.candidate_context_projection[-1].weight)
            nn.init.zeros_(self.candidate_context_projection[-1].bias)
            nn.init.zeros_(self.candidate_residual_head[-1].weight)
            nn.init.zeros_(self.candidate_residual_head[-1].bias)

        _seeded_v5(seed + 100_003, build_head)
        self._head_config = MappingProxyType(_head_config_v5())
        self.register_buffer(
            "_schema_marker_v5",
            torch.tensor(list(_SCHEMA_MARKER_V5), dtype=torch.uint8),
            persistent=True,
        )

    def _candidate_set_context_v5(
        self,
        candidate_tokens: torch.Tensor,
        valid_mask: torch.Tensor,
        *,
        base_global: torch.Tensor,
    ) -> torch.Tensor:
        if candidate_tokens.ndim != 2 or candidate_tokens.shape[1] != self.hidden_dim:
            raise NeuralModelV5Error("v5 candidate tokens have an invalid shape")
        if valid_mask.ndim != 1 or valid_mask.shape[0] != candidate_tokens.shape[0] or valid_mask.dtype is not torch.bool:
            raise NeuralModelV5Error("v5 candidate mask has an invalid shape")
        if valid_mask.any():
            pooled = candidate_tokens[valid_mask].mean(0)
            count = candidate_tokens.new_tensor([float(valid_mask.sum().item()) / 512.0])
        else:
            pooled = candidate_tokens.new_zeros(self.hidden_dim)
            count = candidate_tokens.new_zeros(1)
        return self.candidate_context_projection(torch.cat([pooled, base_global, count]))

    def _record_head_output_v5(
        self,
        state: RelationalStateV4,
        *,
        encoding: StateEncodingV4,
        recurrent_token: torch.Tensor,
        next_hidden: torch.Tensor,
    ) -> PolicyOutputV4:
        """Score one decoder prefix while keeping V4's base global/STOP path."""
        base_global = recurrent_token + self._prefix_embedding(state, encoding)
        if not state.candidates:
            logits = base_global.new_zeros((0,))
        else:
            candidates = torch.stack([
                self.encode_candidate_v4(item, state_encoding=encoding)
                for item in state.candidates
            ])
            invalid_mask = torch.tensor(
                [item.excludes_selected_duplicate for item in state.candidates],
                dtype=torch.bool,
                device=self._device,
            )
            valid_mask = ~invalid_mask
            context = self._candidate_set_context_v5(
                candidates,
                valid_mask,
                base_global=base_global,
            )
            base_logits = self.candidate_bias(torch.tanh(candidates + base_global)).squeeze(-1)
            expanded_context = context.expand(candidates.shape[0], -1)
            residual_input = torch.cat([
                candidates,
                expanded_context,
                candidates * expanded_context,
            ], dim=-1)
            residual = self.candidate_residual_head(residual_input).squeeze(-1)
            logits = (base_logits + residual).masked_fill(invalid_mask, float("-inf"))
        # Do not return context-augmented global_token: recurrent BC derives
        # STOP from this field, and STOP must remain exactly the V4 base path.
        return PolicyOutputV4(logits, base_global, next_hidden)

    def forward_record_group_v5(
        self,
        states: tuple[RelationalStateV4, ...],
        *,
        hidden_state: torch.Tensor | None = None,
        episode_start: bool = True,
    ) -> tuple[PolicyOutputV4, ...]:
        if type(states) is not tuple or not states or any(type(state) is not RelationalStateV4 for state in states):
            raise NeuralModelV5Error("record group must be a nonempty tuple of RelationalStateV4")
        first = states[0]
        if any(state.state_scalars != first.state_scalars or state.entities != first.entities for state in states[1:]):
            raise NeuralModelV5Error("record group must share state scalars and entities")
        encoding = self.encode_state_v4(first)
        recurrent, next_hidden = self.memory(
            encoding.global_token.view(1, 1, -1), None if episode_start else hidden_state,
        )
        return tuple(
            self._record_head_output_v5(
                state,
                encoding=encoding,
                recurrent_token=recurrent[0, 0],
                next_hidden=next_hidden,
            )
            for state in states
        )

    def forward_v5(
        self,
        state: RelationalStateV4,
        *,
        hidden_state: torch.Tensor | None = None,
        episode_start: bool = True,
    ) -> PolicyOutputV4:
        return self.forward_record_group_v5(
            (state,), hidden_state=hidden_state, episode_start=episode_start,
        )[0]

    def step_logits_v5(
        self,
        state: RelationalStateV4,
        *,
        stop_available: bool,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        output = self.forward_v5(state)
        stop = self.stop_vector @ output.global_token + self.stop_bias if stop_available else None
        return output.logits, stop

    def load_state_dict(self, state_dict: Mapping[str, torch.Tensor], strict: bool = True, assign: bool = False):
        if strict is not True:
            raise NeuralModelV5Error("load_state_dict requires the exact closed v5 state_dict")
        expected_state = nn.Module.state_dict(self)
        if set(state_dict) != set(expected_state):
            raise NeuralModelV5Error("load_state_dict requires the exact closed v5 state_dict")
        for name, expected_tensor in expected_state.items():
            actual_tensor = state_dict[name]
            if (
                type(actual_tensor) is not torch.Tensor
                or actual_tensor.layout != torch.strided
                or actual_tensor.dtype != expected_tensor.dtype
                or actual_tensor.shape != expected_tensor.shape
            ):
                raise NeuralModelV5Error("load_state_dict requires the exact closed v5 state_dict")
            if (actual_tensor.is_floating_point() or actual_tensor.is_complex()) and not torch.isfinite(actual_tensor).all():
                raise NeuralModelV5Error("load_state_dict refuses nonfinite v5 tensors")
        for marker_name, expected in (
            ("_schema_marker_v4", self._schema_marker_v4.detach().cpu()),
            ("_schema_marker_v5", self._schema_marker_v5.detach().cpu()),
        ):
            marker = state_dict.get(marker_name)
            if (
                type(marker) is not torch.Tensor
                or marker.dtype != torch.uint8
                or marker.shape != expected.shape
                or not torch.equal(marker.detach().cpu(), expected)
            ):
                raise NeuralModelV5Error(f"state_dict does not carry the {marker_name} schema marker")
        return nn.Module.load_state_dict(self, state_dict, strict=True, assign=assign)


def _descriptor_v5(
    model: SpecialistModelV5,
    state_dict: Mapping[str, torch.Tensor],
    *,
    base_provenance: Mapping[str, object],
) -> dict[str, object]:
    if type(model) is not SpecialistModelV5:
        raise NeuralModelV5Error("checkpoint model must be SpecialistModelV5")
    expected_base_keys = {"path", "file_sha256", "tensor_state_sha256", "checkpoint_schema"}
    if type(base_provenance) is not dict or set(base_provenance) != expected_base_keys:
        raise NeuralModelV5Error("v5 base provenance is incomplete")
    base_file_sha = _require_sha256_v5(base_provenance["file_sha256"], name="base file_sha256")
    base_tensor_sha = _require_sha256_v5(base_provenance["tensor_state_sha256"], name="base tensor_state_sha256")
    if type(base_provenance["path"]) is not str or not base_provenance["path"]:
        raise NeuralModelV5Error("v5 base provenance path is invalid")
    if base_provenance["checkpoint_schema"] != neural_v4.CHECKPOINT_SCHEMA_V4:
        raise NeuralModelV5Error("v5 base provenance schema is not v4")
    # No V5 head key is allowed to enter the transfer map.
    v4_keys = _v4_transfer_state_keys_v5(model)
    allowlist = tuple(sorted(name for name in state_dict if name in v4_keys))
    if allowlist != v4_keys:
        raise NeuralModelV5Error("v5 transfer allowlist does not match V4 base keys")
    transfer = {
        "source_schema": neural_v4.CHECKPOINT_SCHEMA_V4,
        "allowlist": list(v4_keys),
        "allowlist_sha256": _allowlist_sha256_v5(v4_keys),
    }
    return {
        "checkpoint_schema": CHECKPOINT_SCHEMA_V5,
        "representation_schema": REPRESENTATION_V4_SCHEMA,
        "neural_model_schema": NEURAL_MODEL_SCHEMA_V5,
        "implementation_digest_sha256": _implementation_digest_v5(),
        "model_config": _model_config_v5(model),
        "head_config": dict(model._head_config),
        "base_provenance": {
            "path": base_provenance["path"],
            "file_sha256": base_file_sha,
            "tensor_state_sha256": base_tensor_sha,
            "checkpoint_schema": neural_v4.CHECKPOINT_SCHEMA_V4,
        },
        "transfer": transfer,
        "tensor_state_sha256": _tensor_state_sha256_v5(state_dict),
    }


def save_specialist_checkpoint_v5(
    path: str | os.PathLike[str],
    model: SpecialistModelV5,
    *,
    base_provenance: Mapping[str, object],
) -> dict[str, object]:
    """Atomically write one closed V5 sidecar artifact."""
    target = Path(path)
    if not target.parent.is_dir():
        raise NeuralModelV5Error("v5 checkpoint parent directory does not exist")
    state = {name: tensor.detach().cpu().clone() for name, tensor in model.state_dict().items()}
    descriptor = _descriptor_v5(model, state, base_provenance=base_provenance)
    payload = {"descriptor": descriptor, "state_dict": state}
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        torch.save(payload, temporary)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return descriptor


def _validate_descriptor_v5(descriptor: object, model: SpecialistModelV5) -> dict[str, object]:
    expected_keys = {
        "checkpoint_schema",
        "representation_schema",
        "neural_model_schema",
        "implementation_digest_sha256",
        "model_config",
        "head_config",
        "base_provenance",
        "transfer",
        "tensor_state_sha256",
    }
    if type(descriptor) is not dict or set(descriptor) != expected_keys:
        raise NeuralModelV5Error("artifact is not a closed v5 checkpoint descriptor")
    if (
        descriptor["checkpoint_schema"] != CHECKPOINT_SCHEMA_V5
        or descriptor["representation_schema"] != REPRESENTATION_V4_SCHEMA
        or descriptor["neural_model_schema"] != NEURAL_MODEL_SCHEMA_V5
    ):
        raise NeuralModelV5Error("v5 checkpoint descriptor schema binding failed")
    _require_sha256_v5(descriptor["implementation_digest_sha256"], name="implementation_digest_sha256")
    _require_sha256_v5(descriptor["tensor_state_sha256"], name="tensor_state_sha256")
    expected_config = _model_config_v5(model)
    if descriptor["model_config"] != expected_config:
        raise NeuralModelV5Error("v5 checkpoint descriptor model_config binding failed")
    expected_head = _head_config_v5()
    if descriptor["head_config"] != expected_head:
        raise NeuralModelV5Error("v5 checkpoint descriptor head_config binding failed")
    provenance = descriptor["base_provenance"]
    if type(provenance) is not dict or set(provenance) != {"path", "file_sha256", "tensor_state_sha256", "checkpoint_schema"}:
        raise NeuralModelV5Error("v5 checkpoint base provenance is incomplete")
    _require_sha256_v5(provenance["file_sha256"], name="base file_sha256")
    _require_sha256_v5(provenance["tensor_state_sha256"], name="base tensor_state_sha256")
    if type(provenance["path"]) is not str or provenance["checkpoint_schema"] != neural_v4.CHECKPOINT_SCHEMA_V4:
        raise NeuralModelV5Error("v5 checkpoint base provenance binding failed")
    transfer = descriptor["transfer"]
    expected_allowlist = list(_v4_transfer_state_keys_v5(model))
    if type(transfer) is not dict or set(transfer) != {"source_schema", "allowlist", "allowlist_sha256"}:
        raise NeuralModelV5Error("v5 checkpoint transfer provenance is incomplete")
    if (
        transfer["source_schema"] != neural_v4.CHECKPOINT_SCHEMA_V4
        or transfer["allowlist"] != expected_allowlist
        or transfer["allowlist_sha256"] != _allowlist_sha256_v5(expected_allowlist)
    ):
        raise NeuralModelV5Error("v5 checkpoint transfer provenance binding failed")
    return descriptor


def load_specialist_checkpoint_v5(
    path: str | os.PathLike[str],
    model: SpecialistModelV5,
    *,
    expected_file_sha256: str,
    expected_tensor_state_sha256: str,
) -> dict[str, object]:
    """Strictly validate and load a V5 sidecar artifact."""
    if type(model) is not SpecialistModelV5:
        raise NeuralModelV5Error("v5 checkpoint target must be SpecialistModelV5")
    expected_file = _require_sha256_v5(expected_file_sha256, name="expected_file_sha256")
    expected_state = _require_sha256_v5(expected_tensor_state_sha256, name="expected_tensor_state_sha256")
    raw = _checkpoint_snapshot_bytes_v5(Path(path), expected_file_sha256=expected_file)
    try:
        payload = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
    except (OSError, RuntimeError, ValueError, TypeError, EOFError) as exc:
        raise NeuralModelV5Error("v5 checkpoint cannot be read") from exc
    if type(payload) is not dict or set(payload) != {"descriptor", "state_dict"}:
        raise NeuralModelV5Error("artifact is not a closed v5 checkpoint")
    descriptor = _validate_descriptor_v5(payload["descriptor"], model)
    if descriptor["implementation_digest_sha256"] != _implementation_digest_v5():
        raise NeuralModelV5Error("v5 checkpoint implementation digest does not match live source closure")
    state = payload["state_dict"]
    if (
        type(state) is not dict
        or descriptor["tensor_state_sha256"] != expected_state
        or descriptor["tensor_state_sha256"] != _tensor_state_sha256_v5(state)
    ):
        raise NeuralModelV5Error("v5 checkpoint schema/config/state binding failed")
    model.load_state_dict(state, strict=True)
    return dict(descriptor)


def _read_v4_descriptor_for_transfer_v5(
    base_path: Path,
    *,
    expected_file_sha256: str,
) -> dict[str, object]:
    expected_file = _require_sha256_v5(expected_file_sha256, name="expected_base_file_sha256")
    try:
        raw = neural_v4._checkpoint_snapshot_bytes_v4(base_path, expected_file_sha256=expected_file)
        payload = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
    except (OSError, RuntimeError, ValueError, TypeError, EOFError, NeuralModelV4Error) as exc:
        raise NeuralModelV5Error("v4 base checkpoint cannot be inspected for transfer") from exc
    if type(payload) is not dict or set(payload) != {"descriptor", "state_dict"}:
        raise NeuralModelV5Error("v4 base checkpoint is not a closed artifact")
    descriptor = payload["descriptor"]
    if type(descriptor) is not dict or descriptor.get("checkpoint_schema") != neural_v4.CHECKPOINT_SCHEMA_V4:
        raise NeuralModelV5Error("v5 transfer requires a v4 base checkpoint")
    config = descriptor.get("model_config")
    expected_names = {"card_vocabulary_size", "hidden_dim", "embedding_dim", "state_scalar_dim"}
    if (
        type(config) is not dict
        or set(config) != expected_names
        or any(type(value) is not int or value < 1 for value in config.values())
    ):
        raise NeuralModelV5Error("v4 base checkpoint model_config is invalid")
    return dict(descriptor)


def transfer_specialist_checkpoint_v4_to_v5(
    base_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    expected_base_file_sha256: str,
    expected_base_tensor_state_sha256: str,
    head_seed: int = 0,
) -> dict[str, object]:
    """Strictly load V4, copy its allowlisted state, and write a V5 sidecar."""
    source_path = Path(base_path)
    descriptor = _read_v4_descriptor_for_transfer_v5(
        source_path,
        expected_file_sha256=expected_base_file_sha256,
    )
    config = descriptor["model_config"]
    base_model = SpecialistModelV4(**config)
    loaded_base_descriptor = neural_v4.load_specialist_checkpoint_v4(
        source_path,
        base_model,
        expected_file_sha256=expected_base_file_sha256,
        expected_tensor_state_sha256=expected_base_tensor_state_sha256,
    )
    model = SpecialistModelV5(**config, seed=head_seed)
    base_state = base_model.state_dict()
    v5_state = model.state_dict()
    allowlist = tuple(sorted(base_state))
    if any(name not in v5_state for name in allowlist):
        raise NeuralModelV5Error("v5 model is missing a V4 transfer key")
    for name in allowlist:
        v5_state[name] = base_state[name].detach().cpu().clone()
    model.load_state_dict(v5_state, strict=True)
    provenance = {
        "path": str(source_path.resolve()),
        "file_sha256": expected_base_file_sha256,
        "tensor_state_sha256": loaded_base_descriptor["tensor_state_sha256"],
        "checkpoint_schema": neural_v4.CHECKPOINT_SCHEMA_V4,
    }
    return save_specialist_checkpoint_v5(
        output_path,
        model,
        base_provenance=provenance,
    )


__all__ = [
    "CHECKPOINT_SCHEMA_V5",
    "NEURAL_MODEL_SCHEMA_V5",
    "NeuralModelV5Error",
    "SET_CONTEXT_HEAD_VERSION_V5",
    "STOP_POLICY_V5",
    "SpecialistModelV5",
    "load_specialist_checkpoint_v5",
    "save_specialist_checkpoint_v5",
    "transfer_specialist_checkpoint_v4_to_v5",
]
