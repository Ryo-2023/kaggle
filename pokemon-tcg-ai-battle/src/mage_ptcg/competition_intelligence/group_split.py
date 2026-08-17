"""Hard-identity connected-component train/validation/test split (O1-4 §2).

Independent-audit remediation: the previous implementation combined several
grouping dimensions (opponent, agent version, deck fingerprint, temporal
bucket, source) into a single **AND**-composite key -- two episodes shared a
group only if *every* dimension matched. Two episodes that shared only one
*hard* identity (e.g. the same opponent, but a different deck) therefore
produced *different* composite keys and could be dispersed across different
splits: this module's own checks would pass while a real leakage-audit
hard-invariant (``leakage_audit.py``'s ``opponent_leakage_count``) still
failed later, destructively, after a full snapshot build.

This version instead builds an undirected graph over episodes, connecting any
two episodes that share a **hard** identity dimension, and computes connected
components via union-find (disjoint set). Every episode in a connected
component is transitively linked (A-B share an opponent, B-C share an
opponent -> A, B, C form one component) and is assigned to the same split as
an indivisible whole, so hard-identity leakage across splits is prevented
*by construction* at split-assignment time, not merely detected afterward.

Hard connectivity dimension: ``opponent_identity`` (``EpisodeRecord.agent_b``)
only -- the same dimension ``leakage_audit._HARD_INVARIANT_FIELDS`` already
hard-gates. ``episode_id`` uniqueness needs no edge: each episode is already
exactly one graph node, so distinct episodes are never merged based on
identity alone, and ``leakage_audit.py``'s existing episode/duplicate checks
independently confirm no episode id is ever double-assigned.

Deck fingerprint, agent/model version (``agent_a``), and source lineage
(``source_id``) remain **report-only** dimensions and are deliberately *not*
used for connectivity, matching ``leakage_audit.py``'s own documented
rationale for deck: the same fixed 60-card deck, the same Rule Agent
version, or the same single ingestion source recurring across nearly every
episode in typical single-deck/single-source usage is expected and is not
itself an opponent-identity-style information leak. Making source lineage a
hard connectivity dimension was considered (an independent audit raised it)
and rejected: it would make splitting structurally impossible for the
overwhelmingly common case of one archived source per ingestion run (every
episode would collapse into a single component), for the same reason deck
was already excluded. ``leakage_audit.py``'s ``source_leakage_count`` and
``deck_fingerprint_leakage_count`` continue to be computed and reported
so operators can see the (non-gating) overlap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .canonical import digest
from .contracts import EpisodeRecord

GROUP_SPLIT_METHOD = "competition-intelligence-hard-identity-component-v2"
MINIMUM_GROUPS_FOR_SPLIT = 3

# The only dimension used for connectivity; kept as a tuple (not a bare
# constant) so a future, deliberately-reviewed hard-invariant addition has an
# obvious single place to extend, with the same "must match
# leakage_audit._HARD_INVARIANT_FIELDS" discipline documented above.
HARD_CONNECTIVITY_DIMENSIONS = ("opponent_identity",)


class GroupSplitError(ValueError):
    """Raised when a requested split cannot be honored (too few components).

    Carries structured diagnostics (not just a message) so callers can record
    *why* splitting failed -- component count, size distribution, and the
    largest (blocking) component -- in an audit report, per the O1 dataset
    audit's diagnostic requirement.
    """

    def __init__(
        self, message: str, *, component_count: int, component_sizes: Sequence[int], largest_component_id: str | None
    ) -> None:
        super().__init__(message)
        self.component_count = component_count
        self.component_sizes = tuple(sorted(component_sizes, reverse=True))
        self.largest_component_id = largest_component_id

    def to_dict(self) -> dict[str, object]:
        return {
            "message": str(self),
            "component_count": self.component_count,
            "component_sizes": list(self.component_sizes),
            "largest_component_id": self.largest_component_id,
        }


@dataclass(frozen=True, slots=True)
class SplitResult:
    train_episode_ids: tuple[str, ...]
    validation_episode_ids: tuple[str, ...]
    test_episode_ids: tuple[str, ...]
    manifest: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ComponentAssignment:
    """The hard-identity connected components computed over a set of episodes."""

    components_by_id: Mapping[str, tuple[str, ...]]  # component_id -> sorted episode_ids
    episode_component_id: Mapping[str, str]  # episode_id -> component_id

    @property
    def component_count(self) -> int:
        return len(self.components_by_id)

    @property
    def component_sizes(self) -> tuple[int, ...]:
        return tuple(sorted((len(ids) for ids in self.components_by_id.values()), reverse=True))

    @property
    def largest_component_id(self) -> str | None:
        if not self.components_by_id:
            return None
        return max(self.components_by_id, key=lambda cid: (len(self.components_by_id[cid]), cid))


class _DisjointSet:
    """Union-find over arbitrary hashable items, with path compression."""

    def __init__(self, items: Iterable[str]) -> None:
        self._parent: dict[str, str] = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        # Deterministic tie-break (lexicographically smaller root wins) so the
        # resulting component identity never depends on union-call order,
        # which in turn never depends on input iteration order.
        if root_a > root_b:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a


def _hard_identity_values(episode: EpisodeRecord) -> Mapping[str, str]:
    values: dict[str, str] = {}
    if episode.agent_b is not None:
        values["opponent_identity"] = episode.agent_b
    return values


def build_hard_identity_components(episodes: Sequence[EpisodeRecord]) -> ComponentAssignment:
    """Compute hard-identity connected components over ``episodes``.

    Two episodes are connected (directly or transitively) iff they share at
    least one non-``None`` value on a dimension in
    ``HARD_CONNECTIVITY_DIMENSIONS``. An episode whose hard-identity fields
    are all ``None`` (nothing known) is its own singleton component -- it is
    never silently merged into an unrelated component for lack of a value to
    match on. Component ids and membership are fully determined by episode
    content, independent of input order (see the docstring for why: root
    tie-breaking is deterministic, and final ids are content-hash derived,
    not derived from arbitrary insertion order).
    """
    episode_ids = [episode.episode_id for episode in episodes]
    dsu = _DisjointSet(episode_ids)

    first_episode_for_value: dict[tuple[str, str], str] = {}
    for episode in episodes:
        for dimension, value in _hard_identity_values(episode).items():
            key = (dimension, value)
            existing = first_episode_for_value.get(key)
            if existing is None:
                first_episode_for_value[key] = episode.episode_id
            else:
                dsu.union(existing, episode.episode_id)

    members_by_root: dict[str, list[str]] = {}
    for episode_id in episode_ids:
        root = dsu.find(episode_id)
        members_by_root.setdefault(root, []).append(episode_id)

    components_by_id: dict[str, tuple[str, ...]] = {}
    episode_component_id: dict[str, str] = {}
    for member_ids in members_by_root.values():
        sorted_members = tuple(sorted(member_ids))
        # Content-derived component id: independent of dict/root iteration
        # order, and stable across repeated builds from the same episode set.
        component_id = digest({"members": list(sorted_members)}, domain="group-split-component")
        components_by_id[component_id] = sorted_members
        for member_id in sorted_members:
            episode_component_id[member_id] = component_id

    return ComponentAssignment(components_by_id=components_by_id, episode_component_id=episode_component_id)


def split_by_composite_group(
    episodes: Sequence[EpisodeRecord],
    *,
    seed: int,
    validation_fraction: float = 0.15,
    test_fraction: float = 0.15,
    temporal_buckets: Mapping[str, str] | None = None,
) -> SplitResult:
    """Split ``episodes`` into train/validation/test by hard-identity component.

    Raises ``GroupSplitError`` (with structured diagnostics -- see its
    ``to_dict()``) rather than emitting a silently-reduced or leakage-unsafe
    split when there are too few components to honor a 3-way split
    (fewer than ``MINIMUM_GROUPS_FOR_SPLIT``), or when a single component is
    so large no valid split remains once it is placed. Callers must not
    fall back to a row-random split in that situation; a genuinely
    unsplittable dataset (e.g. constant single-opponent self-play data) has
    no leakage-safe split, full stop.

    ``temporal_buckets`` (``episode_id -> bucket label``) no longer creates
    connectivity (see module docstring for why that was the bug): it is
    folded into each component's ranking hash only as a deterministic,
    order-independent tie-breaker, never as a way to manufacture additional
    splittable groups out of a single hard-identity component.
    """
    if not (0.0 < validation_fraction < 1.0) or not (0.0 < test_fraction < 1.0):
        raise GroupSplitError(
            "validation_fraction and test_fraction must each be within (0, 1)",
            component_count=0, component_sizes=(), largest_component_id=None,
        )
    if validation_fraction + test_fraction >= 1.0:
        raise GroupSplitError(
            "validation_fraction + test_fraction must be < 1.0",
            component_count=0, component_sizes=(), largest_component_id=None,
        )

    temporal_buckets = temporal_buckets or {}
    assignment = build_hard_identity_components(episodes)
    components_by_id = assignment.components_by_id

    if len(components_by_id) < MINIMUM_GROUPS_FOR_SPLIT:
        raise GroupSplitError(
            f"hard-identity component split requires at least {MINIMUM_GROUPS_FOR_SPLIT} distinct components, "
            f"got {len(components_by_id)} (largest component: {assignment.largest_component_id!r} with "
            f"{assignment.component_sizes[0] if assignment.component_sizes else 0} episodes); this data has no "
            "leakage-safe split -- reduce the requested split arity or acquire more hard-identity diversity "
            "(e.g. genuinely distinct opponents) rather than falling back to a row-random split",
            component_count=len(components_by_id),
            component_sizes=assignment.component_sizes,
            largest_component_id=assignment.largest_component_id,
        )

    def _rank_key(component_id: str) -> str:
        temporal_signature = tuple(sorted({temporal_buckets[eid] for eid in components_by_id[component_id] if eid in temporal_buckets}))
        return digest({"seed": seed, "component_id": component_id, "temporal_signature": list(temporal_signature)}, domain="group-split-rank")

    ranked = sorted(components_by_id, key=_rank_key)
    total = len(ranked)
    n_test = min(max(1, round(total * test_fraction)), total - 2)
    n_validation = min(max(1, round(total * validation_fraction)), total - n_test - 1)

    test_components = set(ranked[:n_test])
    validation_components = set(ranked[n_test:n_test + n_validation])
    train_components = set(ranked[n_test + n_validation:])

    train_ids = sorted(eid for cid in train_components for eid in components_by_id[cid])
    validation_ids = sorted(eid for cid in validation_components for eid in components_by_id[cid])
    test_ids = sorted(eid for cid in test_components for eid in components_by_id[cid])

    manifest = {
        "split_method": GROUP_SPLIT_METHOD,
        "seed": seed,
        "group_count": total,
        "train_group_count": len(train_components),
        "validation_group_count": len(validation_components),
        "test_group_count": len(test_components),
        "train_episode_count": len(train_ids),
        "validation_episode_count": len(validation_ids),
        "test_episode_count": len(test_ids),
        "component_sizes": list(assignment.component_sizes),
        "split_hash": digest({"train": train_ids, "validation": validation_ids, "test": test_ids}, domain="group-split-result"),
    }
    return SplitResult(
        train_episode_ids=tuple(train_ids),
        validation_episode_ids=tuple(validation_ids),
        test_episode_ids=tuple(test_ids),
        manifest=manifest,
    )


__all__ = [
    "GROUP_SPLIT_METHOD",
    "HARD_CONNECTIVITY_DIMENSIONS",
    "MINIMUM_GROUPS_FOR_SPLIT",
    "ComponentAssignment",
    "GroupSplitError",
    "SplitResult",
    "build_hard_identity_components",
    "split_by_composite_group",
]
