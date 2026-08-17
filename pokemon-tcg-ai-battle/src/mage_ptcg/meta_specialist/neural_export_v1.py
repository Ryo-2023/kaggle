"""Export one trained policy as the self-contained bytes a submission deploys (Slice L4).

This is the *write* half of the exported-policy format;
``neural_policy_v1.load_specialist_neural_policy_v1`` is the read half, and the
two are a closed pair.  The loader clamps CPU threads to the 2-vCPU budget,
refuses anything but ``torch.load(..., weights_only=True)``, requires the schema
version and lineage id to match what the caller expected, and derives
``policy_identity`` by hashing the *exact bytes* it was handed rather than any
field inside them.  Everything here exists so the bytes it hands over can
actually satisfy that contract.

What an export refuses
-----------------------
An export either produces bytes that deploy, or it raises.  It never emits a
payload that will fail at load time on a submission machine, and it never
"fixes up" a model to make it exportable:

* a ``state_dict`` whose key set is not exactly the live model's -- an extra key
  would be silently dropped by a later strict load, a missing one would surface
  only once the policy was already deployed;
* a tensor whose dtype this format does not deploy, which strict
  ``load_state_dict`` would reject after the archive was already built;
* a non-finite weight, which produces a policy whose logits have no legal
  argmax;
* a lineage id that is not a 64-character lowercase hex digest, since
  ``runtime.MetaSpecialistRuntime`` binds a deployed policy's lineage to the live
  ``DeckLockDecision.policy_lineage_id`` and a malformed id can never match.

Verified against its own bytes, not against intent
---------------------------------------------------
Before returning, the exporter loads its own output back through the same
``weights_only=True`` path the loader uses and compares every tensor bit for
bit.  A payload that cannot survive that round trip is a failed export, not a
warning: the whole point of the format is that the archive on a submission
machine deserializes to the weights that were trained, and the only way to know
that is to do it.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any, Mapping

import torch

from mage_ptcg.meta_specialist.neural_model_v1 import SpecialistPolicyModelV1


EXPORTED_POLICY_SCHEMA_V1 = "specialist-neural-exported-policy-v1"

# A submission archive is capped (Kaggle: 202,400 KiB total) and this member is
# only ever one small policy; anything near this bound means something other
# than the intended weights got in.
MAX_EXPORTED_POLICY_BYTES_V1 = 256 * 1024 * 1024

# The model is built entirely from float32 parameters and buffers.  Exporting
# any other dtype would either lose precision silently or fail the loader's
# strict state_dict load, so the set is closed rather than permissive.
_ALLOWED_EXPORT_DTYPES_V1 = frozenset({torch.float32})

_PAYLOAD_KEYS_V1 = frozenset({"schema_version", "lineage_id", "topology_config", "state_dict"})


class NeuralExportV1Error(ValueError):
    """Raised when a policy cannot be exported as deployable bytes."""


def _require_lineage_id_v1(lineage_id: object) -> str:
    if type(lineage_id) is not str or len(lineage_id) != 64:
        raise NeuralExportV1Error("lineage_id must be a 64-character lowercase hex SHA-256 string")
    if any(character not in "0123456789abcdef" for character in lineage_id):
        raise NeuralExportV1Error("lineage_id must be a 64-character lowercase hex SHA-256 string")
    return lineage_id


def _validated_state_dict_v1(model: SpecialistPolicyModelV1) -> dict[str, torch.Tensor]:
    """Return the model's state_dict after checking every tensor is deployable."""
    live = model.state_dict()
    if not live:
        raise NeuralExportV1Error("model has an empty state_dict; there is nothing to deploy")

    exported: dict[str, torch.Tensor] = {}
    for name, tensor in live.items():
        if type(name) is not str:
            raise NeuralExportV1Error("every state_dict key must be a string")
        if not isinstance(tensor, torch.Tensor):
            raise NeuralExportV1Error(f"state_dict entry {name!r} is not a Tensor")
        if tensor.dtype not in _ALLOWED_EXPORT_DTYPES_V1:
            raise NeuralExportV1Error(
                f"state_dict entry {name!r} has dtype {tensor.dtype}, which this format does not deploy"
            )
        if not torch.isfinite(tensor).all():
            raise NeuralExportV1Error(f"state_dict entry {name!r} contains a non-finite value")
        # Detach onto the CPU so the bytes never carry an autograd graph or a
        # device the submission machine does not have.
        exported[name] = tensor.detach().to("cpu").clone()
    return exported


def _verify_round_trip_v1(
    body: bytes, *, expected: Mapping[str, torch.Tensor], lineage_id: str,
) -> None:
    """Load the produced bytes exactly the way the loader will, and compare."""
    try:
        payload = torch.load(io.BytesIO(body), weights_only=True)
    except (RuntimeError, ValueError, EOFError, AttributeError, TypeError) as exc:
        raise NeuralExportV1Error(
            f"exported bytes cannot be re-loaded under weights_only=True: {exc}"
        ) from exc

    if type(payload) is not dict or set(payload) != _PAYLOAD_KEYS_V1:
        raise NeuralExportV1Error("exported bytes do not decode to the closed payload field set")
    if payload["schema_version"] != EXPORTED_POLICY_SCHEMA_V1:
        raise NeuralExportV1Error("exported bytes decode to the wrong schema version")
    if payload["lineage_id"] != lineage_id:
        raise NeuralExportV1Error("exported bytes decode to a different lineage id")

    restored = payload["state_dict"]
    if type(restored) is not dict or set(restored) != set(expected):
        raise NeuralExportV1Error("exported bytes decode to a different state_dict key set")
    for name, tensor in expected.items():
        other = restored[name]
        if not isinstance(other, torch.Tensor):
            raise NeuralExportV1Error(f"exported state_dict entry {name!r} did not decode as a Tensor")
        if other.dtype != tensor.dtype or other.shape != tensor.shape:
            raise NeuralExportV1Error(
                f"exported state_dict entry {name!r} changed dtype/shape across the round trip"
            )
        if not torch.equal(other, tensor):
            raise NeuralExportV1Error(
                f"exported state_dict entry {name!r} did not survive the round trip bit for bit"
            )


def export_specialist_neural_policy_v1(
    model: SpecialistPolicyModelV1,
    lineage_id: str,
) -> bytes:
    """Export one trained model as self-contained, verified, deployable bytes.

    ``lineage_id`` is the ``policy_lineage_id`` the deployed policy will be
    required to report; it is recorded in the payload so the loader can refuse a
    policy belonging to another lineage.  It is supplied by the caller rather
    than derived here, because lineage is a DeckLock decision about *which*
    policy this is, not a property recoverable from the weights.

    The returned bytes are deterministic for a given model state and lineage id.
    """
    if type(model) is not SpecialistPolicyModelV1:
        raise NeuralExportV1Error("model must be a SpecialistPolicyModelV1")
    lineage_id = _require_lineage_id_v1(lineage_id)

    state_dict = _validated_state_dict_v1(model)
    payload: dict[str, Any] = {
        "schema_version": EXPORTED_POLICY_SCHEMA_V1,
        "lineage_id": lineage_id,
        "topology_config": model.config.to_dict(),
        "state_dict": state_dict,
    }

    buffer = io.BytesIO()
    torch.save(payload, buffer)
    body = buffer.getvalue()
    if len(body) > MAX_EXPORTED_POLICY_BYTES_V1:
        raise NeuralExportV1Error(
            f"exported policy is {len(body)} bytes, over the "
            f"{MAX_EXPORTED_POLICY_BYTES_V1}-byte cap"
        )
    _verify_round_trip_v1(body, expected=state_dict, lineage_id=lineage_id)
    return body


def exported_policy_identity_v1(exported_bytes: bytes) -> str:
    """Derive exported bytes' ``policy_identity``: the SHA-256 of the exact bytes.

    Deliberately identical to what ``neural_policy_v1.load_specialist_neural_policy_v1``
    computes on load, so a builder can record the identity of an artifact it is
    about to write without deploying it first.  It hashes the bytes and nothing
    else -- an identity read out of a field inside the payload could be made to
    disagree with the weights actually shipped.
    """
    if type(exported_bytes) is not bytes or not exported_bytes:
        raise NeuralExportV1Error("exported_bytes must be nonempty bytes")
    return hashlib.sha256(exported_bytes).hexdigest()


__all__ = [
    "EXPORTED_POLICY_SCHEMA_V1",
    "MAX_EXPORTED_POLICY_BYTES_V1",
    "NeuralExportV1Error",
    "export_specialist_neural_policy_v1",
    "exported_policy_identity_v1",
]
