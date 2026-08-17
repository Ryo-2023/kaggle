"""Prioritized sequence replay with an explicit demonstration mixture."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import random
from typing import Iterable

from .sequence import SequenceBatch


class _FenwickEpisodeSampler:
    """Weighted removal sampler with O(log n) draws.

    Episode-first PER previously rebuilt every remaining episode's cumulative
    weight for every draw.  A production batch therefore performed millions
    of Python additions while the GPU waited.  The Fenwick tree preserves the
    same sequential weighted-without-replacement distribution and consumes one
    RNG draw per selected episode.
    """

    def __init__(self, weights: list[float]) -> None:
        self.values = list(weights)
        self.tree = [0.0, *weights]
        for index in range(1, len(self.tree)):
            parent = index + (index & -index)
            if parent < len(self.tree):
                self.tree[parent] += self.tree[index]
        # Exhaustion is tracked by entry count, never by ``total``: draining the
        # tree subtracts floats, so the running total can settle on a tiny
        # positive residue and make an emptied sampler look non-empty.
        self.remaining = sum(1 for value in weights if value > 0.0)
        self.total = self.prefix(len(weights))

    def prefix(self, count: int) -> float:
        result = 0.0
        while count:
            result += self.tree[count]
            count -= count & -count
        return result

    def pop(self, rng: random.Random) -> int:
        if self.remaining <= 0:
            raise RuntimeError("episode sampler is empty")
        target = rng.random() * self.total
        index = 0
        bit = 1 << (len(self.values).bit_length() - 1)
        while bit:
            candidate = index + bit
            if candidate < len(self.tree) and self.tree[candidate] <= target:
                index = candidate
                target -= self.tree[candidate]
            bit >>= 1
        selected = min(index, len(self.values) - 1)
        value = self.values[selected]
        if value <= 0.0:
            raise RuntimeError("episode sampler selected a removed entry")
        self.values[selected] = 0.0
        self.remaining -= 1
        cursor = selected + 1
        while cursor < len(self.tree):
            self.tree[cursor] -= value
            cursor += cursor & -cursor
        # Re-read the total from the tree instead of subtracting separately, so
        # the value scaling the next draw always matches the tree being walked.
        self.total = self.prefix(len(self.values))
        return selected


@dataclass(frozen=True, slots=True)
class ReplaySample:
    sequences: tuple[SequenceBatch, ...]
    indices: tuple[int, ...]
    weights: tuple[float, ...]
    demonstrations: int


class PrioritizedSequenceReplay:
    def __init__(self, capacity: int, *, alpha: float = 0.6, demonstration_bonus: float = 1.0) -> None:
        if capacity < 1 or alpha < 0 or demonstration_bonus < 0: raise ValueError("invalid replay configuration")
        self.capacity, self.alpha, self.demonstration_bonus = capacity, alpha, demonstration_bonus; self._items: list[SequenceBatch] = []; self._priorities: list[float] = []
        self._window_refs: list[tuple[int, int]] | None = None
        # Sampling tables over every stored entry.  Rebuilding them per call
        # made sample() cost O(entries) in Python and dominated the learner
        # step; only the k sampled priorities change between calls.
        self._alpha_weight_cache: list[float] | None = None
        self._demonstration_indices: list[int] | None = None
        self._demonstration_lookup: frozenset[int] | None = None
        self._episode_group_cache: dict[str, tuple[tuple[int, ...], ...]] = {}
        self._episode_flat_cache: dict[str, tuple[object, object]] = {}
        self._source_group_cache: dict[str, tuple[int, ...]] | None = None
        self._priority_index_identity: str | None = None
    def __len__(self) -> int: return len(self._window_refs) if self._window_refs is not None else len(self._items)
    @property
    def is_windowed(self) -> bool: return self._window_refs is not None
    def _invalidate_membership(self) -> None:
        self._alpha_weight_cache = None; self._demonstration_indices = None; self._demonstration_lookup = None
        self._episode_group_cache = {}
        self._episode_flat_cache = {}
        self._source_group_cache = None
        self._priority_index_identity = None
    def add(self, sequence: SequenceBatch, *, priority: float | None = None) -> None:
        if self.is_windowed: raise RuntimeError("cannot append directly to a window-indexed replay")
        base = float(priority if priority is not None else sequence.priority)
        if base <= 0: raise ValueError("priority must be positive")
        bonus = self.demonstration_bonus if any(step.demonstration for step in sequence.learner) else 0.0
        if len(self._items) >= self.capacity: self._items.pop(0); self._priorities.pop(0)
        self._items.append(sequence); self._priorities.append(base + bonus); self._invalidate_membership()
    def fork(self) -> "PrioritizedSequenceReplay":
        """Share immutable transition payloads but isolate mutable priorities."""
        replay = type(self)(
            self.capacity, alpha=self.alpha, demonstration_bonus=self.demonstration_bonus
        )
        replay._items = list(self._items)
        replay._window_refs = list(self._window_refs) if self._window_refs is not None else None
        replay._priorities = list(self._priorities)
        replay._demonstration_indices = self._demonstration_indices
        replay._demonstration_lookup = self._demonstration_lookup
        replay._episode_group_cache = dict(self._episode_group_cache)
        replay._episode_flat_cache = dict(self._episode_flat_cache)
        replay._source_group_cache = self._source_group_cache
        replay._priority_index_identity = self._priority_index_identity
        return replay
    @classmethod
    def windowed(cls, source: "PrioritizedSequenceReplay", *, stride: int) -> "PrioritizedSequenceReplay":
        """Reference overlapping starts without duplicating transition payloads."""
        if source.is_windowed: raise ValueError("cannot create nested replay windows")
        if stride < 1: raise ValueError("replay window stride must be positive")
        references = [(source_index, offset) for source_index, item in enumerate(source._items)
                      for offset in range(0, len(item.learner), stride)]
        if len(references) > source.capacity: raise ValueError("windowed replay exceeds capacity")
        replay = cls(source.capacity, alpha=source.alpha, demonstration_bonus=source.demonstration_bonus)
        replay._items, replay._window_refs = list(source._items), references
        replay._priorities = [source._priorities[source_index] for source_index, _ in references]
        replay._invalidate_membership()
        return replay
    def _sequence_at(self, index: int) -> SequenceBatch:
        if not 0 <= index < len(self): raise IndexError("replay index out of range")
        if self._window_refs is None: return self._items[index]
        source_index, offset = self._window_refs[index]; source = self._items[source_index]
        values = [*source.burn_in, *source.learner, *source.lookahead]; start = len(source.burn_in) + offset
        learner = tuple(values[start:start + 20]); burn_in = tuple(values[max(0, start - 8):start])
        lookahead = tuple(values[start + 20:start + 25])
        if not learner or any(step.terminal for step in burn_in): raise RuntimeError("invalid replay window reference")
        return SequenceBatch(burn_in, learner, source.priority, f"{source.sequence_id}-window-{offset}",
                             source.episode_id or source.sequence_id, lookahead)
    def sequence_at(self, index: int) -> SequenceBatch:
        """Return one immutable replay sequence by stable priority index."""
        return self._sequence_at(index)
    def sequences(self) -> tuple[SequenceBatch, ...]:
        """Immutable sequence view for replay version sealing/migration."""
        return tuple(self.sequence_at(index) for index in range(len(self)))
    def _is_demonstration(self, index: int) -> bool:
        if self._window_refs is None: item = self._items[index]
        else: item = self._items[self._window_refs[index][0]]
        return any(step.demonstration for step in item.learner)
    def _alpha_weights(self) -> list[float]:
        """``priority ** alpha`` per entry, maintained instead of rebuilt.

        A learner step re-prioritises only the sampled entries, so raising
        every stored priority to ``alpha`` again on each call was pure repeat
        work.  The list holds exactly the values the previous per-call list
        comprehension produced, in the same order, so ``sum`` and the derived
        probabilities are unchanged bit for bit.
        """
        if self._alpha_weight_cache is None:
            self._alpha_weight_cache = [value ** self.alpha for value in self._priorities]
        return self._alpha_weight_cache
    def _demonstration_table(self) -> tuple[list[int], frozenset[int]]:
        """Demonstration membership is fixed by the stored sequences.

        Recomputing it per call scanned every sequence's learner transitions,
        and the subsequent ``index in demos`` test walked that list once per
        sampled entry.  Both are cached; the ascending index order the
        demonstration draw consumes is preserved.
        """
        if self._demonstration_indices is None or self._demonstration_lookup is None:
            self._demonstration_indices = [index for index in range(len(self)) if self._is_demonstration(index)]
            self._demonstration_lookup = frozenset(self._demonstration_indices)
        return self._demonstration_indices, self._demonstration_lookup
    def _episode_key(self, index: int) -> str:
        item = self._items[index] if self._window_refs is None else self._items[self._window_refs[index][0]]
        return item.episode_id or item.sequence_id
    def _source_groups(self) -> dict[str, tuple[int, ...]]:
        """Cache immutable replay membership by behavior source.

        Source-balanced training happens for every learner update.  Discovering
        groups by materialising every window at every update turned that option
        into an O(replay_size * updates) CPU bottleneck.
        """
        if self._source_group_cache is None:
            groups: dict[str, list[int]] = {}
            for index in range(len(self)):
                sequence = self._sequence_at(index)
                steps = (*sequence.burn_in, *sequence.learner, *sequence.lookahead)
                source = steps[0].behavior_source if steps else "UNKNOWN"
                groups.setdefault(source, []).append(index)
            self._source_group_cache = {
                source: tuple(indices) for source, indices in sorted(groups.items())
            }
        return self._source_group_cache
    def _episode_groups(self, candidates: Iterable[int], *, cache_key: str) -> tuple[tuple[int, ...], ...]:
        cached = self._episode_group_cache.get(cache_key)
        if cached is not None:
            return cached
        by_episode: dict[str, list[int]] = {}
        for index in candidates:
            by_episode.setdefault(self._episode_key(index), []).append(index)
        cached = tuple(tuple(group) for group in by_episode.values())
        self._episode_group_cache[cache_key] = cached
        return cached

    def _episode_first_draw(self, candidates: Iterable[int], probabilities: list[float], count: int,
                            rng: random.Random, *, cache_key: str) -> list[int]:
        import numpy

        groups = self._episode_groups(candidates, cache_key=cache_key)
        if count and not groups:
            raise RuntimeError("episode-first sampling has no candidates")
        flat = self._episode_flat_cache.get(cache_key)
        if flat is None:
            flat_indices = numpy.fromiter(
                (index for group in groups for index in group),
                dtype=numpy.int64,
            )
            offsets = numpy.cumsum(
                numpy.asarray(
                    [0, *(len(group) for group in groups)],
                    dtype=numpy.int64,
                )
            )[:-1]
            flat = (flat_indices, offsets)
            self._episode_flat_cache[cache_key] = flat
        flat_indices, offsets = flat
        group_weights = numpy.add.reduceat(
            probabilities[flat_indices], offsets
        ).tolist()
        picker = _FenwickEpisodeSampler(group_weights)
        selected: list[int] = []
        while len(selected) < count:
            if picker.remaining <= 0:
                picker = _FenwickEpisodeSampler(group_weights)
            group = groups[picker.pop(rng)]
            selected.append(rng.choices(group, weights=[probabilities[index] for index in group], k=1)[0])
        return selected
    def sample(self, batch_size: int, *, beta: float, demonstration_ratio: float = 0.0,
               seed: int | None = None, episode_first: bool = False,
               source_balanced: bool = False) -> ReplaySample:
        import numpy

        if not len(self) or batch_size < 1 or not 0 <= beta <= 1 or not 0 <= demonstration_ratio <= 1: raise ValueError("invalid replay sample request")
        rng = random.Random(seed); weights = self._alpha_weights(); total = sum(weights)
        probabilities = numpy.asarray(weights, dtype=numpy.float64) / total
        demos, demonstration_lookup = self._demonstration_table(); requested = min(batch_size, round(batch_size * demonstration_ratio))
        if source_balanced:
            groups = self._source_groups()
            if not groups:
                raise RuntimeError("source-balanced replay has no source groups")
            selected: list[int] = []
            proposal: list[float] = []
            if demos and requested:
                demo_mass = float(probabilities[demos].sum())
                for index in rng.choices(demos, weights=[probabilities[index] for index in demos], k=requested):
                    selected.append(index)
                    proposal.append(float(probabilities[index]) / demo_mass)
            remaining = batch_size - len(selected); names = tuple(groups)
            group_draws = {name: sum(offset % len(names) == position for offset in range(remaining))
                           for position, name in enumerate(names)}
            for offset in range(remaining):
                group = groups[names[offset % len(names)]]
                group_mass = float(probabilities[list(group)].sum())
                index = rng.choices(group, weights=[probabilities[index] for index in group], k=1)[0]
                selected.append(index)
                proposal.append((group_draws[names[offset % len(names)]] / remaining) * float(probabilities[index]) / group_mass)
        elif episode_first:
            selected = self._episode_first_draw(
                demos, probabilities, requested, rng, cache_key="demonstrations"
            ) if demos and requested else []
            selected.extend(self._episode_first_draw(
                range(len(self)), probabilities, batch_size - len(selected), rng, cache_key="all"
            ))
        else:
            selected = rng.choices(demos, k=requested) if demos else []
            selected.extend(rng.choices(range(len(self)), weights=probabilities, k=batch_size - len(selected)))
        if source_balanced:
            correction = [(len(self) * probability) ** (-beta) for probability in proposal]
        else:
            correction = [(len(self) * float(probabilities[index])) ** (-beta) for index in selected]
        maximum = max(correction)
        return ReplaySample(tuple(self._sequence_at(index) for index in selected), tuple(selected), tuple(value / maximum for value in correction), len([index for index in selected if index in demonstration_lookup]))
    def update_priorities(self, indices: Iterable[int], priorities: Iterable[float],
                          *, importance: Iterable[float] | None = None) -> list[dict[str, object]]:
        index_values = list(indices); priority_values = list(priorities)
        if len(index_values) != len(priority_values):
            raise ValueError("priority update lengths differ")
        importance_values = list(importance) if importance is not None else [None] * len(index_values)
        if len(importance_values) != len(index_values):
            raise ValueError("importance update lengths differ")
        weights = self._alpha_weights()
        updates: list[dict[str, object]] = []
        for index, value, correction in zip(index_values, priority_values, importance_values, strict=True):
            if not 0 <= index < len(self) or not math.isfinite(float(value)) or value <= 0:
                raise ValueError("invalid replay priority update")
            if correction is not None and not math.isfinite(float(correction)):
                raise ValueError("invalid replay importance weight")
            old = self._priorities[index]
            self._priorities[index] = float(value); weights[index] = float(value) ** self.alpha
            updates.append({"sample_id": index, "sequence_id": self._sequence_at(index).sequence_id,
                            "old_priority": old, "new_priority": float(value), "importance_weight": correction})
        return updates
    def priority_state(self) -> dict[str, object]:
        """Serializable mutable PER state bound to the replay's index layout."""
        if self._priority_index_identity is None:
            identities: object
            if self._window_refs is None:
                identities = [item.sequence_id for item in self._items]
            else:
                identities = [
                    (self._items[source_index].sequence_id, offset)
                    for source_index, offset in self._window_refs
                ]
            self._priority_index_identity = hashlib.sha256(
                json.dumps(identities, ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest()
        return {
            "schema": "r2d3-replay-priority-state-v1",
            "entries": len(self),
            "index_identity": self._priority_index_identity,
            "priorities": list(self._priorities),
        }
    def load_priority_state(self, state: object) -> None:
        """Restore PER priorities only when every replay index is identical."""
        if not isinstance(state, dict) or state.get("schema") != "r2d3-replay-priority-state-v1":
            raise ValueError("unsupported replay priority state")
        expected = self.priority_state()
        values = state.get("priorities")
        if (
            int(state.get("entries", -1)) != len(self)
            or state.get("index_identity") != expected["index_identity"]
            or not isinstance(values, list)
            or len(values) != len(self)
        ):
            raise ValueError("replay priority state identity mismatch")
        priorities = [float(value) for value in values]
        if any(not math.isfinite(value) or value <= 0.0 for value in priorities):
            raise ValueError("replay priority state contains invalid values")
        self._priorities = priorities
        self._alpha_weight_cache = [value ** self.alpha for value in priorities]
    def reset_priorities(self, value: float | None = None) -> None:
        """Population rollover 用に全 priority を同じ有限正値へ戻す。

        strict resume ではこの API を呼ばず ``load_priority_state`` を使う。
        """
        if not len(self):
            raise ValueError("cannot reset priorities of an empty replay")
        target = float(value) if value is not None else max(
            1.0, sum(self._priorities) / len(self._priorities)
        )
        if not math.isfinite(target) or target <= 0.0:
            raise ValueError("reset priority must be finite and positive")
        self._priorities = [target] * len(self)
        self._alpha_weight_cache = [target ** self.alpha] * len(self)
    def save(self, path: str | Path) -> dict[str, object]:
        payload = {"schema": "r2d3-prioritized-replay-v1", "capacity": self.capacity, "alpha": self.alpha,
                   "demonstration_bonus": self.demonstration_bonus, "priorities": self._priorities,
                   "items": [asdict(item) for item in self._items]} if self._window_refs is None else {
                       "schema": "r2d3-prioritized-replay-v2", "storage": "window_refs", "capacity": self.capacity,
                       "alpha": self.alpha, "demonstration_bonus": self.demonstration_bonus, "priorities": self._priorities,
                       "source_items": [asdict(item) for item in self._items], "window_refs": self._window_refs}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        destination = Path(path); destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(canonical + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {"sequences": len(self), "sha256": hashlib.sha256((canonical + "\n").encode()).hexdigest()}
    @classmethod
    def load(cls, path: str | Path) -> "PrioritizedSequenceReplay":
        from .sequence import R2D3Transition, SequenceBatch
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if value.get("schema") not in {"r2d3-prioritized-replay-v1", "r2d3-prioritized-replay-v2"}: raise ValueError("unsupported replay schema")
        replay = cls(int(value["capacity"]), alpha=float(value["alpha"]), demonstration_bonus=float(value["demonstration_bonus"]))
        rows = value["items"] if value.get("schema") == "r2d3-prioritized-replay-v1" else value["source_items"]
        for row in rows:
            burn_in = tuple(R2D3Transition(**step) for step in row["burn_in"]); learner = tuple(R2D3Transition(**step) for step in row["learner"])
            lookahead = tuple(R2D3Transition(**step) for step in row.get("lookahead", []))
            replay._items.append(SequenceBatch(burn_in, learner, float(row["priority"]), str(row["sequence_id"]),
                                               str(row.get("episode_id", "")), lookahead))
        replay._priorities = [float(priority) for priority in value["priorities"]]
        if value.get("schema") == "r2d3-prioritized-replay-v2":
            refs = [tuple(int(part) for part in pair) for pair in value["window_refs"]]
            if len(refs) != len(replay._priorities) or len(refs) > replay.capacity: raise ValueError("invalid replay window references")
            replay._window_refs = refs
        elif len(replay._items) != len(replay._priorities): raise ValueError("invalid replay item priorities")
        return replay
