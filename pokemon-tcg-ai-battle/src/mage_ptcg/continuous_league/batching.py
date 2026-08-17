"""Sequence replay を recurrent learner tensor へ変換する公開実装。"""

from __future__ import annotations

import hashlib
import sys
import time
from dataclasses import dataclass
from typing import Any

from mage_ptcg.policy_learning.r2d3.sequence import n_step_returns


@dataclass(frozen=True, slots=True)
class _PackedSequence:
    states: Any
    burn_states: Any
    actions: Any
    legal_mask: Any
    selected: Any
    rewards: Any
    discounts: Any
    bootstrap_indices: Any
    demonstration: Any
    sequence_mask: Any
    opponent_class_target: Any
    deck_family_target: Any
    next_action_type_target: Any

    @property
    def length(self) -> int:
        return int(self.states.shape[0])

    @property
    def burn_length(self) -> int:
        return int(self.burn_states.shape[0])

    @property
    def action_count(self) -> int:
        return int(self.actions.shape[1])


class PackedReplayBatcher:
    """Replay sequence の不変部分を一度だけ NumPy 配列へ変換する。

    PER priority と抽選結果は従来どおり replay が所有する。このクラスは
    sample された index に対応する不変 tensor payload だけを再利用するため、
    strict resume の sampling 状態を変更しない。
    """

    def __init__(
        self,
        replay: Any,
        *,
        n_step: int = 5,
        opponent_classes: int = 64,
        deck_family_classes: int = 32,
        action_type_classes: int = 32,
        eager: bool = True,
        show_progress: bool = False,
        progress_interval_seconds: float = 10.0,
    ) -> None:
        if n_step < 1 or min(
            opponent_classes, deck_family_classes, action_type_classes
        ) < 1:
            raise ValueError("invalid packed replay batcher configuration")
        self.replay = replay
        self.n_step = n_step
        self.opponent_classes = opponent_classes
        self.deck_family_classes = deck_family_classes
        self.action_type_classes = action_type_classes
        self._packed: list[_PackedSequence | None] = [None] * len(replay)
        self._pinned_storage: dict[Any, Any] = {}
        self._resident: dict[str, Any] | None = None
        self._resident_device: Any | None = None
        if eager:
            self.prepack(
                show_progress=show_progress,
                progress_interval_seconds=progress_interval_seconds,
            )

    @staticmethod
    def _class_index(value: str, classes: int) -> int:
        return int(hashlib.sha256(value.encode()).hexdigest()[:16], 16) % classes

    def _pack(self, sequence: Any) -> _PackedSequence:
        import numpy

        burn = sequence.burn_in
        learner_steps = sequence.learner
        steps = (*learner_steps, *sequence.lookahead)
        if not steps:
            raise ValueError("learner batch requires replay sequences")
        maximum = max(len(step.legal_actions) for step in steps)
        state_width = len(learner_steps[0].public_state)
        action_width = len(learner_steps[0].legal_actions[0])
        length = len(steps)
        states = numpy.asarray(
            [step.public_state for step in steps], dtype=numpy.float32
        )
        burn_states = numpy.asarray(
            [step.public_state for step in burn], dtype=numpy.float32
        ).reshape(len(burn), state_width)
        actions = numpy.zeros(
            (length, maximum, action_width), dtype=numpy.float32
        )
        legal_mask = numpy.zeros((length, maximum), dtype=bool)
        selected = numpy.empty(length, dtype=numpy.int64)
        rewards = numpy.empty(length, dtype=numpy.float32)
        discounts = numpy.empty(length, dtype=numpy.float32)
        bootstrap = numpy.empty(length, dtype=numpy.int64)
        demonstration = numpy.zeros(length, dtype=bool)
        opponent_targets = numpy.empty(length, dtype=numpy.int64)
        deck_targets = numpy.empty(length, dtype=numpy.int64)
        action_targets = numpy.empty(length, dtype=numpy.int64)
        returns = n_step_returns(list(steps), n_step=self.n_step)
        for offset, (step, (reward, discount, end)) in enumerate(
            zip(steps, returns, strict=True)
        ):
            count = len(step.legal_actions)
            actions[offset, :count] = step.legal_actions
            legal_mask[offset, :count] = True
            selected[offset] = step.selected_action
            rewards[offset] = reward
            discounts[offset] = discount
            bootstrap[offset] = end + 1 if end + 1 < length else -1
            trainable = offset < len(learner_steps)
            demonstration[offset] = step.demonstration and trainable
            opponent_targets[offset] = self._class_index(
                step.opponent_policy_hash, self.opponent_classes
            )
            deck_targets[offset] = self._class_index(
                step.opponent_family, self.deck_family_classes
            )
            encoded_type = step.legal_actions[step.selected_action][0]
            action_targets[offset] = (
                int(round(encoded_type * self.action_type_classes))
                % self.action_type_classes
            )
        sequence_mask = numpy.zeros(length, dtype=bool)
        sequence_mask[: len(learner_steps)] = True
        return _PackedSequence(
            states=states,
            burn_states=burn_states,
            actions=actions,
            legal_mask=legal_mask,
            selected=selected,
            rewards=rewards,
            discounts=discounts,
            bootstrap_indices=bootstrap,
            demonstration=demonstration,
            sequence_mask=sequence_mask,
            opponent_class_target=opponent_targets,
            deck_family_target=deck_targets,
            next_action_type_target=action_targets,
        )

    def _sequence(self, index: int) -> Any:
        sequence_at = getattr(self.replay, "sequence_at", None)
        if sequence_at is not None:
            return sequence_at(index)
        return self.replay._sequence_at(index)

    def prepack(
        self,
        *,
        show_progress: bool = False,
        progress_interval_seconds: float = 10.0,
    ) -> None:
        if progress_interval_seconds <= 0:
            raise ValueError("progress interval must be positive")
        missing = [
            index for index, packed in enumerate(self._packed) if packed is None
        ]
        if not missing:
            return
        progress_bar: Any | None = None
        if show_progress and sys.stderr.isatty():
            try:
                from tqdm import tqdm

                progress_bar = tqdm(
                    total=len(missing),
                    unit="sequence",
                    dynamic_ncols=True,
                    desc="prepack-replay",
                    leave=False,
                )
            except ImportError:
                progress_bar = None
        started = time.monotonic()
        last_progress = started
        for completed, index in enumerate(missing, start=1):
            self._packed[index] = self._pack(self._sequence(index))
            if progress_bar is not None:
                progress_bar.update(1)
            elif show_progress:
                now = time.monotonic()
                if now - last_progress >= progress_interval_seconds:
                    elapsed = max(now - started, 1e-9)
                    print(
                        f"stage=prepack completed={completed}/{len(missing)} "
                        f"rate={completed / elapsed:.2f}/s fault=0",
                        flush=True,
                    )
                    last_progress = now
        if progress_bar is not None:
            progress_bar.close()

    def _packed_sequence(self, index: int) -> _PackedSequence:
        if not 0 <= index < len(self._packed):
            raise IndexError("replay index out of range")
        packed = self._packed[index]
        if packed is None:
            packed = self._pack(self._sequence(index))
            self._packed[index] = packed
        return packed

    def reserve_pinned(self, batch_size: int) -> None:
        """Reserve three reusable pinned arenas for the largest replay shape."""

        import torch

        if batch_size < 1 or not self._packed:
            raise ValueError("invalid pinned replay batch reservation")
        packed = [item for item in self._packed if item is not None]
        if len(packed) != len(self._packed):
            self.prepack()
            packed = [item for item in self._packed if item is not None]
        length = max(item.length for item in packed)
        burn_length = max(1, max(item.burn_length for item in packed))
        maximum = max(item.action_count for item in packed)
        state_width = int(packed[0].states.shape[1])
        action_width = int(packed[0].actions.shape[2])
        sizes = {
            torch.float32: (
                batch_size * length * state_width
                + batch_size * burn_length * state_width
                + batch_size * length * maximum * action_width
                + 2 * batch_size * length
                + batch_size
            ),
            torch.int64: 5 * batch_size * length,
            torch.bool: (
                batch_size * length * maximum
                + 3 * batch_size * length
                + batch_size * burn_length
            ),
        }
        for dtype, required in sizes.items():
            storage = self._pinned_storage.get(dtype)
            if storage is None or storage.numel() < required:
                self._pinned_storage[dtype] = torch.empty(
                    required, dtype=dtype, pin_memory=True
                )

    def materialize_resident(
        self,
        device: Any,
        *,
        chunk_size: int = 512,
        show_progress: bool = False,
        progress_interval_seconds: float = 10.0,
    ) -> None:
        """Materialize immutable replay tensors once on the learner device."""

        import torch

        if chunk_size < 1 or progress_interval_seconds <= 0:
            raise ValueError("invalid resident replay materialization")
        packed = [item for item in self._packed if item is not None]
        if len(packed) != len(self._packed):
            self.prepack()
            packed = [item for item in self._packed if item is not None]
        count = len(packed)
        length = max(item.length for item in packed)
        burn_length = max(1, max(item.burn_length for item in packed))
        maximum = max(item.action_count for item in packed)
        state_width = int(packed[0].states.shape[1])
        action_width = int(packed[0].actions.shape[2])
        resident = {
            "states": torch.zeros(
                (count, length, state_width),
                dtype=torch.float32,
                device=device,
            ),
            "actions": torch.zeros(
                (count, length, maximum, action_width),
                dtype=torch.float32,
                device=device,
            ),
            "legal_mask": torch.zeros(
                (count, length, maximum), dtype=torch.bool, device=device
            ),
            "selected": torch.zeros(
                (count, length), dtype=torch.int64, device=device
            ),
            "rewards": torch.zeros(
                (count, length), dtype=torch.float32, device=device
            ),
            "discounts": torch.zeros(
                (count, length), dtype=torch.float32, device=device
            ),
            "demonstration": torch.zeros(
                (count, length), dtype=torch.bool, device=device
            ),
            "sequence_mask": torch.zeros(
                (count, length), dtype=torch.bool, device=device
            ),
            "bootstrap_indices": torch.full(
                (count, length), -1, dtype=torch.int64, device=device
            ),
            "burn_in_states": torch.zeros(
                (count, burn_length, state_width),
                dtype=torch.float32,
                device=device,
            ),
            "burn_in_mask": torch.zeros(
                (count, burn_length), dtype=torch.bool, device=device
            ),
            "opponent_class_target": torch.zeros(
                (count, length), dtype=torch.int64, device=device
            ),
            "deck_family_target": torch.zeros(
                (count, length), dtype=torch.int64, device=device
            ),
            "next_action_type_target": torch.zeros(
                (count, length), dtype=torch.int64, device=device
            ),
        }
        progress_bar: Any | None = None
        chunks = (count + chunk_size - 1) // chunk_size
        if show_progress and sys.stderr.isatty():
            try:
                from tqdm import tqdm

                progress_bar = tqdm(
                    total=count,
                    unit="sequence",
                    dynamic_ncols=True,
                    desc="resident-replay",
                    leave=False,
                )
            except ImportError:
                progress_bar = None
        started = time.monotonic()
        last_progress = started
        for chunk in range(chunks):
            start = chunk * chunk_size
            end = min(count, start + chunk_size)
            indices = tuple(range(start, end))
            placeholder = type(
                "_ResidentSample",
                (),
                {
                    "indices": indices,
                    "sequences": (None,) * len(indices),
                    "weights": (1.0,) * len(indices),
                },
            )()
            cpu_batch = self.cpu_batch(
                placeholder,
                pin_memory=getattr(device, "type", None) == "cuda",
            )
            uploaded = self.upload(cpu_batch, device)
            for name, destination in resident.items():
                value = uploaded[name]
                if value is None:
                    continue
                target = (
                    slice(start, end),
                    *(slice(0, size) for size in value.shape[1:]),
                )
                destination[target].copy_(value)
            if getattr(device, "type", None) == "cuda":
                torch.cuda.synchronize(device)
            completed = end
            if progress_bar is not None:
                progress_bar.update(end - start)
            elif show_progress:
                now = time.monotonic()
                if now - last_progress >= progress_interval_seconds:
                    elapsed = max(now - started, 1e-9)
                    print(
                        f"stage=resident-replay completed={completed}/{count} "
                        f"rate={completed / elapsed:.2f}/s fault=0",
                        flush=True,
                    )
                    last_progress = now
        lengths = torch.tensor(
            [item.length for item in packed],
            dtype=torch.int64,
            device=device,
        )
        padded = torch.arange(length, device=device).unsqueeze(0) >= (
            lengths.unsqueeze(1)
        )
        resident["legal_mask"][:, :, 0] |= padded
        if progress_bar is not None:
            progress_bar.close()
        self._resident = resident
        self._resident_device = device

    def resident_batch(self, sample: Any, device: Any) -> dict[str, Any]:
        """Gather one learner batch entirely on the resident replay device."""

        import numpy
        import torch

        if self._resident is None or self._resident_device != device:
            raise ValueError("replay is not resident on the requested device")
        if not sample.indices or len(sample.indices) != len(sample.sequences):
            raise ValueError("resident learner batch requires aligned indices")
        packed = [self._packed_sequence(int(index)) for index in sample.indices]
        length = max(item.length for item in packed)
        burn_length = max(1, max(item.burn_length for item in packed))
        maximum = max(item.action_count for item in packed)
        indices = torch.tensor(sample.indices, dtype=torch.int64, device=device)
        resident = self._resident

        def gather(name: str, *slices: slice) -> Any:
            source = resident[name][(slice(None), *slices)]
            return source.index_select(0, indices)

        demonstrations = gather(
            "demonstration", slice(0, length)
        )
        return {
            "states": gather("states", slice(0, length), slice(None)),
            "actions": gather(
                "actions",
                slice(0, length),
                slice(0, maximum),
                slice(None),
            ),
            "legal_mask": gather(
                "legal_mask", slice(0, length), slice(0, maximum)
            ),
            "selected": gather("selected", slice(0, length)),
            "rewards": gather("rewards", slice(0, length)),
            "discounts": gather("discounts", slice(0, length)),
            "importance": torch.as_tensor(
                numpy.asarray(sample.weights, dtype=numpy.float32),
                device=device,
            ),
            "demonstration": (
                demonstrations
                if any(bool(item.demonstration.any()) for item in packed)
                else None
            ),
            "sequence_mask": gather(
                "sequence_mask", slice(0, length)
            ),
            "bootstrap_indices": gather(
                "bootstrap_indices", slice(0, length)
            ),
            "burn_in_states": gather(
                "burn_in_states",
                slice(0, burn_length),
                slice(None),
            ),
            "burn_in_mask": gather(
                "burn_in_mask", slice(0, burn_length)
            ),
            "opponent_class_target": gather(
                "opponent_class_target", slice(0, length)
            ),
            "deck_family_target": gather(
                "deck_family_target", slice(0, length)
            ),
            "next_action_type_target": gather(
                "next_action_type_target", slice(0, length)
            ),
        }

    def cpu_batch(self, sample: Any, *, pin_memory: bool = False) -> dict[str, Any]:
        import numpy
        import torch

        if not sample.indices or len(sample.indices) != len(sample.sequences):
            raise ValueError("packed learner batch requires aligned replay indices")
        packed = [self._packed_sequence(int(index)) for index in sample.indices]
        count = len(packed)
        length = max(item.length for item in packed)
        burn_length = max(1, max(item.burn_length for item in packed))
        maximum = max(item.action_count for item in packed)
        state_width = int(packed[0].states.shape[1])
        action_width = int(packed[0].actions.shape[2])

        specifications = {
            "states": ((count, length, state_width), torch.float32, 0),
            "burn_states": (
                (count, burn_length, state_width),
                torch.float32,
                0,
            ),
            "actions": (
                (count, length, maximum, action_width),
                torch.float32,
                0,
            ),
            "masks": ((count, length, maximum), torch.bool, 0),
            "sequence_mask": ((count, length), torch.bool, 0),
            "burn_mask": ((count, burn_length), torch.bool, 0),
            "selected": ((count, length), torch.int64, 0),
            "rewards": ((count, length), torch.float32, 0),
            "discounts": ((count, length), torch.float32, 0),
            "bootstrap": ((count, length), torch.int64, -1),
            "demonstrations": ((count, length), torch.bool, 0),
            "opponent_targets": ((count, length), torch.int64, 0),
            "deck_targets": ((count, length), torch.int64, 0),
            "action_targets": ((count, length), torch.int64, 0),
            "importance": ((count,), torch.float32, 0),
        }

        def elements(shape: tuple[int, ...]) -> int:
            result = 1
            for value in shape:
                result *= value
            return result

        tensors: dict[str, Any] = {}
        if pin_memory:
            required: dict[Any, int] = {}
            for shape, dtype, _fill in specifications.values():
                required[dtype] = required.get(dtype, 0) + elements(shape)
            for dtype, count_required in required.items():
                storage = self._pinned_storage.get(dtype)
                if storage is None or storage.numel() < count_required:
                    self._pinned_storage[dtype] = torch.empty(
                        count_required, dtype=dtype, pin_memory=True
                    )
            cursors = {dtype: 0 for dtype in required}
            for name, (shape, dtype, fill) in specifications.items():
                count_required = elements(shape)
                start = cursors[dtype]
                tensor = self._pinned_storage[dtype][
                    start : start + count_required
                ].view(shape)
                tensor.numpy().fill(fill)
                tensors[name] = tensor
                cursors[dtype] += count_required
        else:
            for name, (shape, dtype, fill) in specifications.items():
                tensor = torch.empty(shape, dtype=dtype)
                tensor.numpy().fill(fill)
                tensors[name] = tensor

        states = tensors["states"]
        burn_states = tensors["burn_states"]
        actions = tensors["actions"]
        masks = tensors["masks"]
        sequence_mask = tensors["sequence_mask"]
        burn_mask = tensors["burn_mask"]
        selected = tensors["selected"]
        rewards = tensors["rewards"]
        discounts = tensors["discounts"]
        bootstrap = tensors["bootstrap"]
        demonstrations = tensors["demonstrations"]
        opponent_targets = tensors["opponent_targets"]
        deck_targets = tensors["deck_targets"]
        action_targets = tensors["action_targets"]
        views = {
            "states": states.numpy(),
            "burn_states": burn_states.numpy(),
            "actions": actions.numpy(),
            "masks": masks.numpy(),
            "sequence_mask": sequence_mask.numpy(),
            "burn_mask": burn_mask.numpy(),
            "selected": selected.numpy(),
            "rewards": rewards.numpy(),
            "discounts": discounts.numpy(),
            "bootstrap": bootstrap.numpy(),
            "demonstrations": demonstrations.numpy(),
            "opponent_targets": opponent_targets.numpy(),
            "deck_targets": deck_targets.numpy(),
            "action_targets": action_targets.numpy(),
        }
        for batch_index, item in enumerate(packed):
            row = slice(0, item.length)
            action_row = slice(0, item.action_count)
            views["states"][batch_index, row] = item.states
            views["actions"][batch_index, row, action_row] = item.actions
            views["masks"][batch_index, row, action_row] = item.legal_mask
            views["sequence_mask"][batch_index, row] = item.sequence_mask
            views["selected"][batch_index, row] = item.selected
            views["rewards"][batch_index, row] = item.rewards
            views["discounts"][batch_index, row] = item.discounts
            views["bootstrap"][batch_index, row] = item.bootstrap_indices
            views["demonstrations"][batch_index, row] = item.demonstration
            views["opponent_targets"][batch_index, row] = (
                item.opponent_class_target
            )
            views["deck_targets"][batch_index, row] = item.deck_family_target
            views["action_targets"][batch_index, row] = (
                item.next_action_type_target
            )
            if item.burn_length:
                burn_row = slice(0, item.burn_length)
                views["burn_states"][batch_index, burn_row] = item.burn_states
                views["burn_mask"][batch_index, burn_row] = True
            if item.length < length:
                views["masks"][batch_index, item.length :, 0] = True
        importance = tensors["importance"]
        importance.numpy()[:] = numpy.asarray(
            sample.weights, dtype=numpy.float32
        )
        return {
            "states": states,
            "actions": actions,
            "legal_mask": masks,
            "selected": selected,
            "rewards": rewards,
            "discounts": discounts,
            "importance": importance,
            "demonstration": demonstrations
            if bool(views["demonstrations"].any())
            else None,
            "sequence_mask": sequence_mask,
            "bootstrap_indices": bootstrap,
            "burn_in_states": burn_states,
            "burn_in_mask": burn_mask,
            "opponent_class_target": opponent_targets,
            "deck_family_target": deck_targets,
            "next_action_type_target": action_targets,
        }

    @staticmethod
    def upload(cpu_batch: dict[str, Any], device: Any) -> dict[str, Any]:
        non_blocking = getattr(device, "type", None) == "cuda"
        return {
            key: (
                value.to(device, non_blocking=non_blocking)
                if value is not None
                else None
            )
            for key, value in cpu_batch.items()
        }

    def learner_batch(
        self, sample: Any, device: Any, *, pin_memory: bool = False
    ) -> dict[str, Any]:
        return self.upload(
            self.cpu_batch(sample, pin_memory=pin_memory),
            device,
        )


def learner_batch(
    sample: Any,
    device: Any,
    *,
    n_step: int = 5,
    opponent_classes: int = 64,
    deck_family_classes: int = 32,
    action_type_classes: int = 32,
) -> dict[str, Any]:
    import numpy
    import torch

    sequences = sample.sequences
    if not sequences:
        raise ValueError("learner batch requires replay sequences")
    count = len(sequences)
    length = max(len(item.learner) + len(item.lookahead) for item in sequences)
    burn_length = max(1, max(len(item.burn_in) for item in sequences))
    maximum = max(
        len(step.legal_actions)
        for item in sequences
        for step in (*item.learner, *item.lookahead)
    )
    state_width = len(sequences[0].learner[0].public_state)
    action_width = len(sequences[0].learner[0].legal_actions[0])
    states = numpy.zeros((count, length, state_width), dtype=numpy.float32)
    burn_states = numpy.zeros((count, burn_length, state_width), dtype=numpy.float32)
    actions = numpy.zeros(
        (count, length, maximum, action_width), dtype=numpy.float32
    )
    masks = numpy.zeros((count, length, maximum), dtype=bool)
    sequence_mask = numpy.zeros((count, length), dtype=bool)
    burn_mask = numpy.zeros((count, burn_length), dtype=bool)
    selected = numpy.zeros((count, length), dtype=numpy.int64)
    rewards = numpy.zeros((count, length), dtype=numpy.float32)
    discounts = numpy.zeros((count, length), dtype=numpy.float32)
    bootstrap = numpy.full((count, length), -1, dtype=numpy.int64)
    demonstrations = numpy.zeros((count, length), dtype=bool)
    opponent_targets = numpy.zeros((count, length), dtype=numpy.int64)
    deck_targets = numpy.zeros((count, length), dtype=numpy.int64)
    action_targets = numpy.zeros((count, length), dtype=numpy.int64)

    def class_index(value: str, classes: int) -> int:
        return int(hashlib.sha256(value.encode()).hexdigest()[:16], 16) % classes

    for batch_index, sequence in enumerate(sequences):
        burn = list(sequence.burn_in)
        learner_steps = list(sequence.learner)
        steps = [*learner_steps, *sequence.lookahead]
        if burn:
            burn_states[batch_index, : len(burn)] = [
                step.public_state for step in burn
            ]
            burn_mask[batch_index, : len(burn)] = True
        returns = n_step_returns(steps, n_step=n_step)
        for offset, (step, (reward, discount, end)) in enumerate(
            zip(steps, returns, strict=True)
        ):
            states[batch_index, offset] = step.public_state
            actions[batch_index, offset, : len(step.legal_actions)] = (
                step.legal_actions
            )
            masks[batch_index, offset, : len(step.legal_actions)] = True
            trainable = offset < len(learner_steps)
            sequence_mask[batch_index, offset] = trainable
            selected[batch_index, offset] = step.selected_action
            rewards[batch_index, offset] = reward
            discounts[batch_index, offset] = discount
            bootstrap[batch_index, offset] = (
                end + 1 if end + 1 < len(steps) else -1
            )
            demonstrations[batch_index, offset] = step.demonstration and trainable
            opponent_targets[batch_index, offset] = class_index(
                step.opponent_policy_hash, opponent_classes
            )
            deck_targets[batch_index, offset] = class_index(
                step.opponent_family, deck_family_classes
            )
            encoded_type = step.legal_actions[step.selected_action][0]
            action_targets[batch_index, offset] = (
                int(round(encoded_type * action_type_classes)) % action_type_classes
            )
        if len(steps) < length:
            masks[batch_index, len(steps) :, 0] = True

    return {
        "states": torch.from_numpy(states).to(device),
        "actions": torch.from_numpy(actions).to(device),
        "legal_mask": torch.from_numpy(masks).to(device),
        "selected": torch.from_numpy(selected).to(device),
        "rewards": torch.from_numpy(rewards).to(device),
        "discounts": torch.from_numpy(discounts).to(device),
        "importance": torch.tensor(
            sample.weights, dtype=torch.float32, device=device
        ),
        "demonstration": (
            torch.from_numpy(demonstrations).to(device)
            if bool(demonstrations.any())
            else None
        ),
        "sequence_mask": torch.from_numpy(sequence_mask).to(device),
        "bootstrap_indices": torch.from_numpy(bootstrap).to(device),
        "burn_in_states": torch.from_numpy(burn_states).to(device),
        "burn_in_mask": torch.from_numpy(burn_mask).to(device),
        "opponent_class_target": torch.from_numpy(opponent_targets).to(device),
        "deck_family_target": torch.from_numpy(deck_targets).to(device),
        "next_action_type_target": torch.from_numpy(action_targets).to(device),
    }
