"""Full relational behavior-cloning trainer and episode-group splitter."""

from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
import re

import torch
from torch.nn import functional as F

from mage_ptcg.meta_specialist.neural_model_v3 import SpecialistModelV3
from mage_ptcg.meta_specialist.representation_v3 import (
    RelationalStateV3,
    representation_v3_from_step_input_v1,
    stable_action_id_v3,
)


@dataclass(frozen=True, slots=True)
class SplitManifestV3:
    """Deterministic, auditable two-way assignment over leak components."""

    schema: str
    source_dataset_sha256: str
    ubiquitous_keys: tuple[str, ...]
    ubiquitous_metadata: Mapping[str, object]
    assignments: tuple[Mapping[str, str], ...]
    counts: Mapping[str, int]
    overlap_counters: Mapping[str, int]
    manifest_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema, "source_dataset_sha256": self.source_dataset_sha256,
            "ubiquitous_keys": list(self.ubiquitous_keys), "ubiquitous_metadata": dict(self.ubiquitous_metadata),
            "assignments": [dict(row) for row in self.assignments], "counts": dict(self.counts),
            "overlap_counters": dict(self.overlap_counters), "manifest_sha256": self.manifest_sha256,
        }

    def write_json(self, path: str | Path) -> None:
        Path(path).write_bytes(_canonical_json_v3(self.to_dict()))

    @classmethod
    def read_json(cls, path: str | Path) -> "SplitManifestV3":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        required = {"schema", "source_dataset_sha256", "ubiquitous_keys", "ubiquitous_metadata", "assignments", "counts", "overlap_counters", "manifest_sha256"}
        if type(payload) is not dict or set(payload) != required or payload["schema"] != "meta-specialist-split-manifest-v3":
            raise ValueError("split manifest has an invalid closed schema")
        core = {key: payload[key] for key in required - {"manifest_sha256"}}
        if _sha256_v3(core) != payload["manifest_sha256"]:
            raise ValueError("split manifest self hash does not verify")
        if type(payload["assignments"]) is not list or not payload["assignments"]:
            raise ValueError("split manifest assignments are invalid")
        assignments = tuple(dict(row) for row in payload["assignments"] if type(row) is dict)
        if len(assignments) != len(payload["assignments"]) or any(set(row) != {"record_id", "component_id", "partition"} or row["partition"] not in {"train", "validation"} for row in assignments):
            raise ValueError("split manifest assignments are malformed")
        if type(payload["counts"]) is not dict or payload["counts"] != {"train": sum(row["partition"] == "train" for row in assignments), "validation": sum(row["partition"] == "validation" for row in assignments)}:
            raise ValueError("split manifest counts do not match assignments")
        if payload["overlap_counters"] != {"episode_overlap": 0, "near_duplicate_overlap": 0}:
            raise ValueError("split manifest has nonzero leakage overlap")
        return cls(payload["schema"], payload["source_dataset_sha256"], tuple(payload["ubiquitous_keys"]), dict(payload["ubiquitous_metadata"]), assignments, dict(payload["counts"]), dict(payload["overlap_counters"]), payload["manifest_sha256"])


def _canonical_json_v3(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_v3(value: object) -> str:
    return hashlib.sha256(_canonical_json_v3(value)).hexdigest()


def build_split_manifest_v3(
    records: Sequence[Mapping[str, object]], *, validation_fraction: float, ubiquitous_threshold: int,
    ubiquitous_keys: Sequence[str] | None = None, ubiquitous_rule_version: str = "gate1-authoritative-v1",
) -> SplitManifestV3:
    """Assign whole episode/near-duplicate connected components to train/valid.

    ``ubiquitous_threshold`` is explicit because Gate 1 must pin this judgement
    in its artifact instead of silently deriving it from a changing corpus size.
    """
    if not records or not 0 < validation_fraction < 1 or type(ubiquitous_threshold) is not int or ubiquitous_threshold < 1:
        raise ValueError("records, validation_fraction, and ubiquitous_threshold are invalid")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("split records must be mappings")
        try:
            row = {name: record[name] for name in ("record_id", "episode_id_hash", "near_duplicate_id")}
        except KeyError as exc:
            raise ValueError("split records need record_id, episode_id_hash, and near_duplicate_id") from exc
        if any(type(value) is not str or not value for value in row.values()) or row["record_id"] in seen:
            raise ValueError("split record identities must be nonempty and unique")
        seen.add(row["record_id"])
        # Bind the selected record's entire target-bearing content, not merely
        # its grouping identity.  Formal Gate 1 provides exact raw records here.
        normalized.append({**row, "record_content_sha256": _sha256_v3(record)})  # type: ignore[arg-type]
    normalized.sort(key=lambda row: row["record_id"])
    episodes_by_near: dict[str, set[str]] = defaultdict(set)
    for row in normalized:
        episodes_by_near[row["near_duplicate_id"]].add(row["episode_id_hash"])
    frequencies = {key: len(episodes) for key, episodes in episodes_by_near.items()}
    if ubiquitous_keys is None:
        # This is deliberately conservative for ad-hoc callers. Formal Gate 1
        # must pass the authoritative list it pins in its input manifest.
        ubiquitous = ()
    else:
        if any(type(key) is not str or key not in frequencies for key in ubiquitous_keys):
            raise ValueError("authoritative ubiquitous keys are invalid")
        ubiquitous = tuple(sorted(set(ubiquitous_keys)))
    ubiquitous_set = set(ubiquitous)

    parent = {row["record_id"]: row["record_id"] for row in normalized}
    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item
    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)
    episode_anchor: dict[str, str] = {}
    near_anchor: dict[str, str] = {}
    for row in normalized:
        record_id = row["record_id"]
        episode = row["episode_id_hash"]
        near = row["near_duplicate_id"]
        if episode in episode_anchor:
            union(record_id, episode_anchor[episode])
        else:
            episode_anchor[episode] = record_id
        if near not in ubiquitous_set:
            if near in near_anchor:
                union(record_id, near_anchor[near])
            else:
                near_anchor[near] = record_id
    components: dict[str, list[str]] = defaultdict(list)
    for row in normalized:
        components[find(row["record_id"])].append(row["record_id"])
    if len(components) < 2:
        raise ValueError("split would produce fewer than two leak components")
    ordered_components = sorted((tuple(sorted(items)) for items in components.values()), key=lambda items: _sha256_v3(items))
    target = max(1, int(round(len(normalized) * validation_fraction)))
    # Choose a whole-component validation set that is exactly the requested
    # cardinality when possible.  Greedy hash order can overshoot (11 + 1
    # singletons selected for a target of six), despite a six-singleton split
    # being available.  This bounded DP is deterministic and preserves the
    # component order as its sole tie-breaker.
    reachable: dict[int, tuple[int, ...]] = {0: ()}
    for component_index, members in enumerate(ordered_components):
        for size, chosen in tuple(reachable.items()):
            total = size + len(members)
            if total < len(normalized) and total not in reachable:
                reachable[total] = chosen + (component_index,)
    candidate_sizes = [size for size in reachable if 0 < size < len(normalized)]
    if not candidate_sizes:
        raise ValueError("split would produce an empty partition")
    chosen_size = min(candidate_sizes, key=lambda size: (abs(size - target), size > target, size))
    validation_ids = {
        record_id
        for component_index in reachable[chosen_size]
        for record_id in ordered_components[component_index]
    }
    if not validation_ids or len(validation_ids) == len(normalized):
        raise ValueError("split would produce an empty partition")
    component_id_by_record = {
        record_id: _sha256_v3(list(members))
        for members in ordered_components for record_id in members
    }
    assignments = tuple({
        "record_id": row["record_id"], "component_id": component_id_by_record[row["record_id"]],
        "partition": "validation" if row["record_id"] in validation_ids else "train",
    } for row in normalized)
    train = {row["record_id"] for row in assignments if row["partition"] == "train"}
    valid = {row["record_id"] for row in assignments if row["partition"] == "validation"}
    episode_overlap = 0
    near_overlap = 0
    for episode, anchor in episode_anchor.items():
        members = {row["record_id"] for row in normalized if row["episode_id_hash"] == episode}
        episode_overlap += int(bool(members & train) and bool(members & valid))
    for near, episodes in episodes_by_near.items():
        if near in ubiquitous_set:
            continue
        members = {row["record_id"] for row in normalized if row["near_duplicate_id"] == near}
        near_overlap += int(bool(members & train) and bool(members & valid))
    core = {
        "schema": "meta-specialist-split-manifest-v3",
        "source_dataset_sha256": _sha256_v3(normalized), "ubiquitous_keys": list(ubiquitous),
        "ubiquitous_metadata": {"rule_version": ubiquitous_rule_version, "threshold": ubiquitous_threshold, "episode_frequency": frequencies},
        "assignments": list(assignments), "counts": {"train": len(train), "validation": len(valid)},
        "overlap_counters": {"episode_overlap": episode_overlap, "near_duplicate_overlap": near_overlap},
    }
    return SplitManifestV3(
        schema=core["schema"], source_dataset_sha256=core["source_dataset_sha256"], ubiquitous_keys=ubiquitous, ubiquitous_metadata=core["ubiquitous_metadata"],
        assignments=assignments, counts=core["counts"], overlap_counters=core["overlap_counters"],
        manifest_sha256=_sha256_v3(core),
    )


@dataclass(frozen=True, slots=True)
class BCExampleV3:
    state: RelationalStateV3
    target_index: int
    episode_group: str
    quality_weight: float = 1.0
    model_input: object | None = None
    step_input: object | None = None
    target_masses: tuple[float, ...] = ()
    episode_start: bool = True
    component_id: str = ""
    partition: str = ""

    def __post_init__(self) -> None:
        if type(self.state) is not RelationalStateV3 or type(self.target_index) is not int or self.target_index < 0:
            raise ValueError("BC example state/target is invalid")
        if type(self.episode_group) is not str or not self.episode_group or type(self.quality_weight) not in {int, float} or not math.isfinite(self.quality_weight) or self.quality_weight <= 0:
            raise ValueError("episode_group/quality_weight is invalid")
        if type(self.target_masses) is not tuple:
            raise ValueError("BC example target masses must be a tuple")
        sealed_fields = (self.model_input, self.step_input, self.component_id, self.partition)
        if any(value is not None and value != "" for value in sealed_fields):
            if self.model_input is None or self.step_input is None or type(self.component_id) is not str or not self.component_id or self.partition not in {"train", "validation"}:
                raise ValueError("sealed recurrent BC example metadata is invalid")
            expected_tokens = len(self.state.candidates) + int(getattr(self.step_input, "stop_available", False))
            if (len(self.target_masses) != expected_tokens
                    or any(type(mass) not in {int, float} or not math.isfinite(mass) or mass < 0 for mass in self.target_masses)
                    or not math.isclose(math.fsum(self.target_masses), 1.0, rel_tol=0.0, abs_tol=1e-12)
                    or self.target_index >= expected_tokens):
                raise ValueError("sealed recurrent BC example soft target is invalid")
        elif self.target_masses or self.target_index >= len(self.state.candidates):
            raise ValueError("unsealed BC example target is invalid")
        if type(self.episode_start) is not bool:
            raise ValueError("BC example episode_start is invalid")


@dataclass(frozen=True, slots=True)
class BCTrainingResultV3:
    best_epoch: int
    best_validation_nll: float
    train_history: tuple[Mapping[str, float], ...]
    checkpoint_state: Mapping[str, torch.Tensor]


@dataclass(frozen=True, slots=True)
class RecurrentBCSequenceV3:
    """One ordered sealed episode; no hidden state may cross this boundary."""

    lane: str
    episode_id: str
    component_id: str
    partition: str
    steps: tuple[BCExampleV3, ...]
    burn_in: int

    def __post_init__(self) -> None:
        if (type(self.lane) is not str or not self.lane or type(self.episode_id) is not str
                or not self.episode_id or type(self.component_id) is not str
                or not self.component_id or self.partition not in {"train", "validation"}
                or type(self.steps) is not tuple or not self.steps
                or any(type(step) is not BCExampleV3 for step in self.steps)
                or type(self.burn_in) is not int or self.burn_in < 0):
            raise ValueError("recurrent BC sequence metadata is invalid")
        if not self.steps[0].episode_start or any(step.episode_start for step in self.steps[1:]):
            raise ValueError("recurrent BC sequence must reset only at its first step")
        if any(
            step.episode_group != self.episode_id
            or step.component_id != self.component_id
            or step.partition != self.partition
            or not step.target_masses
            for step in self.steps
        ):
            raise ValueError("recurrent BC sequence crosses a sealed metadata boundary")


@dataclass(frozen=True, slots=True)
class RecurrentBCBatchV3:
    """Padded sequence metadata; ``True`` in ``padding_mask`` means a real step."""

    sequences: tuple[RecurrentBCSequenceV3, ...]
    padding_mask: torch.Tensor
    loss_mask: torch.Tensor


def make_recurrent_batch_v3(
    sequences: Sequence[RecurrentBCSequenceV3], *, burn_in: int,
) -> RecurrentBCBatchV3:
    """Create masks without materializing synthetic padding examples.

    Later recurrent code must use ``padding_mask`` to skip hidden updates and
    ``loss_mask`` to exclude burn-in/padding from loss aggregation.
    """
    if type(burn_in) is not int or burn_in < 0 or not sequences:
        raise ValueError("recurrent sequences and burn_in are invalid")
    normalized = tuple(sequences)
    if any(type(sequence) is not RecurrentBCSequenceV3 or sequence.burn_in != burn_in for sequence in normalized):
        raise ValueError("recurrent sequences must share the requested burn_in")
    width = max(len(sequence.steps) for sequence in normalized)
    padding = torch.zeros((len(normalized), width), dtype=torch.bool)
    loss = torch.zeros_like(padding)
    for row, sequence in enumerate(normalized):
        length = len(sequence.steps)
        padding[row, :length] = True
        loss[row, burn_in:length] = True
    return RecurrentBCBatchV3(normalized, padding, loss)


def materialize_recurrent_gate_sequences_v3(
    input_path: str | Path, *, burn_in: int, expected_input_file_sha256: str,
) -> tuple[RecurrentBCSequenceV3, ...]:
    """Revalidate a sealed Gate input, then group its pinned loss rows in order."""
    if type(burn_in) is not int or burn_in < 0:
        raise ValueError("burn_in must be a nonnegative integer")
    if type(expected_input_file_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", expected_input_file_sha256) is None:
        raise ValueError("expected_input_file_sha256 must be a lowercase SHA-256 digest")
    target = Path(input_path)
    if hashlib.sha256(target.read_bytes()).hexdigest() != expected_input_file_sha256:
        raise ValueError("sealed Gate input file SHA-256 does not match its external anchor")
    from mage_ptcg.meta_specialist.representation_benchmark_v3 import (
        _GateStepV3, _gate_steps_from_input_v3, _read_gate_input_v3,
    )

    payload = _read_gate_input_v3(target)
    sealed_steps = _gate_steps_from_input_v3(payload)
    if not sealed_steps:
        raise ValueError("sealed Gate input materialized no loss rows")
    sequences: list[RecurrentBCSequenceV3] = []
    completed_episodes: set[str] = set()
    current_episode: str | None = None
    current_record: str | None = None
    current_lane: str | None = None
    current_component: str | None = None
    current_partition: str | None = None
    current_steps: list[BCExampleV3] = []

    def close_current() -> None:
        if current_episode is None:
            return
        assert current_lane is not None and current_component is not None and current_partition is not None
        sequences.append(RecurrentBCSequenceV3(
            current_lane, current_episode, current_component, current_partition,
            tuple(current_steps), burn_in,
        ))
        completed_episodes.add(current_episode)

    for sealed in sealed_steps:
        if type(sealed) is not _GateStepV3:
            raise ValueError("sealed Gate step loader returned an untrusted step")
        episode_id = sealed.episode_id
        if (type(episode_id) is not str or not episode_id or type(sealed.record_id) is not str
                or not sealed.record_id or sealed.lane != payload["lane"]
                or sealed.partition not in {"train", "validation"}
                or type(sealed.component_id) is not str or not sealed.component_id):
            raise ValueError("sealed Gate step has invalid episode/split metadata")
        if current_episode != episode_id:
            close_current()
            if episode_id in completed_episodes:
                raise ValueError("sealed Gate steps reorder a completed episode")
            current_episode = episode_id
            current_record = sealed.record_id
            current_lane = sealed.lane
            current_component = sealed.component_id
            current_partition = sealed.partition
            current_steps = []
        elif (sealed.record_id != current_record or sealed.lane != current_lane
                or sealed.component_id != current_component or sealed.partition != current_partition):
            raise ValueError("sealed Gate steps cross a record/component/split boundary")
        current_steps.append(BCExampleV3(
            state=sealed.state, target_index=sealed.target_index,
            episode_group=episode_id, quality_weight=1.0,
            model_input=sealed.model_input, step_input=sealed.step_input,
            target_masses=sealed.target_masses,
            episode_start=not current_steps, component_id=sealed.component_id,
            partition=sealed.partition,
        ))
    close_current()
    return tuple(sequences)


def split_episode_groups_v3(examples: Sequence[BCExampleV3], *, validation_fraction: float = 0.2) -> tuple[tuple[BCExampleV3, ...], tuple[BCExampleV3, ...]]:
    if not examples or not 0 < validation_fraction < 1:
        raise ValueError("examples must be nonempty and validation_fraction in (0,1)")
    groups: dict[str, list[BCExampleV3]] = defaultdict(list)
    for example in examples:
        groups[example.episode_group].append(example)
    target = max(1, int(round(len(examples) * validation_fraction)))
    validation_groups: list[str] = []
    count = 0
    for group in sorted(groups, reverse=True):
        if count >= target and validation_groups:
            break
        validation_groups.append(group)
        count += len(groups[group])
    validation_set = set(validation_groups)
    train = tuple(example for example in examples if example.episode_group not in validation_set)
    valid = tuple(example for example in examples if example.episode_group in validation_set)
    if not train or not valid:
        raise ValueError("episode split would produce an empty partition")
    return train, valid


def _episode_loss(model: SpecialistModelV3, examples: Sequence[BCExampleV3]) -> tuple[torch.Tensor, float, float]:
    grouped: dict[str, list[torch.Tensor]] = defaultdict(list)
    correct = 0
    total = 0
    for example in examples:
        output = model.forward_v3(example.state, episode_start=True)
        loss = F.cross_entropy(output.logits.view(1, -1), torch.tensor([example.target_index])) * example.quality_weight
        grouped[example.episode_group].append(loss)
        correct += int(output.logits.argmax().item() == example.target_index)
        total += 1
    per_episode = torch.stack([torch.stack(losses).mean() for losses in grouped.values()]).mean()
    return per_episode, float(per_episode.detach().item()), correct / max(1, total)


def train_bc_v3(
    model: SpecialistModelV3, train_examples: Sequence[BCExampleV3], validation_examples: Sequence[BCExampleV3], *,
    epochs: int = 10, learning_rate: float = 1e-4, gradient_clip_norm: float = 1.0,
) -> BCTrainingResultV3:
    if type(model) is not SpecialistModelV3 or not train_examples or not validation_examples or epochs < 1 or learning_rate <= 0:
        raise ValueError("model, datasets, epochs, and learning rate are invalid")
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    best_nll = float("inf")
    best_epoch = -1
    best_state: dict[str, torch.Tensor] = {}
    history: list[Mapping[str, float]] = []
    for epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        loss, train_nll, train_accuracy = _episode_loss(model, train_examples)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip_norm)
        optimizer.step()
        model.eval()
        with torch.no_grad():
            _valid_loss, valid_nll, valid_accuracy = _episode_loss(model, validation_examples)
        row = {"epoch": float(epoch), "train_nll": train_nll, "validation_nll": valid_nll, "train_top1": train_accuracy, "validation_top1": valid_accuracy}
        history.append(row)
        if valid_nll < best_nll:
            best_nll, best_epoch = valid_nll, epoch
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    if best_epoch < 0:
        raise RuntimeError("BC training produced no checkpoint")
    return BCTrainingResultV3(best_epoch, best_nll, tuple(history), best_state)


def load_bc_examples_from_teacher_records_v3(root: str | Path, *, limit: int = 1024) -> tuple[BCExampleV3, ...]:
    """Retired unsafe loader.

    A root-global scan cannot prove selection bytes, qualification authority,
    split membership, or canonical soft STOP targets.  Formal BC/Gate callers
    must materialize the closed Gate-1 manifest via ``_gate_steps_from_input``.
    This explicit failure prevents legacy callers from silently training on a
    shortened or hard-label-only record stream.
    """
    del root, limit
    raise RuntimeError("legacy teacher-record BC loader is retired; use the sealed Gate-1 input manifest")


__all__ = [
    "BCExampleV3", "BCTrainingResultV3", "RecurrentBCBatchV3", "RecurrentBCSequenceV3",
    "SplitManifestV3", "build_split_manifest_v3", "make_recurrent_batch_v3",
    "materialize_recurrent_gate_sequences_v3", "split_episode_groups_v3", "train_bc_v3",
]
