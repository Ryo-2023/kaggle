"""Candidate-scoring policy model over the frozen serial-free semantic features.

The model never sees a local action ID, card serial, CABT index, or private
binding: it consumes only :class:`SpecialistModelInputV1` and
:class:`SpecialistStepInputV1`.  Variable candidate sets are scored by a
bilinear query against per-candidate encodings, so the parameter count does not
depend on how many classes are legal at a step and the emitted order follows the
caller's class order exactly.

Every closed string domain is a frozen vocabulary.  An unseen zone or visibility
fails closed rather than collapsing into a shared bucket, because a silent
collision would merge two distinct semantic classes into one logit.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any, Mapping, Sequence

import torch
from torch import nn

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    PokemonEntityV1,
    SemanticActionV1,
    SemanticEndpointV1,
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    SpecialistStepLogitsV1,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2


NEURAL_MODEL_SCHEMA_V1 = "specialist-neural-model-v2"

# Every zone the feature layer can emit, including the compound attachment zones
# it produces for tool/energy sources.  A missing entry would fail closed on a
# legitimate decision, so `test_zone_vocabulary_covers_every_emitted_zone` pins
# this against the emitting module.
SEMANTIC_ZONE_VOCABULARY_V1 = (
    "active", "active-energy", "active-tool", "attached-energy", "attached-tool",
    "bench", "bench-energy", "bench-tool", "context-card", "deck", "deck-reveal",
    "discard", "energy", "hand", "hidden", "looking", "not-applicable", "player",
    "pre-evolution", "prize", "stadium", "tool",
)
VISIBILITY_VOCABULARY_V1 = (
    "actor-visible", "hidden-unresolved", "not-applicable", "owner-resolved",
    "public-visible", "special-condition",
)
MAX_OPTION_TYPE_V1 = 32
MAX_SELECTION_TYPE_V1 = 16
MAX_SELECTION_CONTEXT_V1 = 64
_STATE_SCALARS_V1 = 41
_BAG_NAMES_V1 = ("own_hand", "deck_reveal", "looking_visible", "self_discard", "opponent_discard")
_SINGLE_CARD_NAMES_V1 = ("context", "effect", "stadium")
_OPTIONAL_INT_FIELDS_V1 = ("number", "attack_id", "special_condition", "energy_count", "skill_card_id")

_ZONE_INDEX_V1 = {name: index for index, name in enumerate(SEMANTIC_ZONE_VOCABULARY_V1)}
_VISIBILITY_INDEX_V1 = {name: index for index, name in enumerate(VISIBILITY_VOCABULARY_V1)}
_CATEGORICAL_STATE_SCALAR_INDICES_V1 = frozenset(
    {0, 1, 4, 5, 11, 12, 13, 14, *range(23, 39)}
)
_OPPONENT_VALUE_BUCKETS_V1 = 256


class NeuralModelV1Error(ValueError):
    """Raised when the model receives an input outside its frozen domains."""


@dataclass(frozen=True, slots=True)
class SpecialistModelConfigV1:
    """Frozen topology; it is part of the checkpoint's training identity."""

    card_vocabulary_size: int
    hidden_dim: int = 128
    card_dim: int = 64
    symbol_dim: int = 16
    representation_version: int = 2

    def __post_init__(self) -> None:
        for name in ("card_vocabulary_size", "hidden_dim", "card_dim", "symbol_dim", "representation_version"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise NeuralModelV1Error(f"{name} must be a positive int")
        if self.representation_version != 2:
            raise NeuralModelV1Error("only representation_version=2 is supported; v1 checkpoints are stale")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": NEURAL_MODEL_SCHEMA_V1,
            "card_vocabulary_size": self.card_vocabulary_size,
            "hidden_dim": self.hidden_dim,
            "card_dim": self.card_dim,
            "symbol_dim": self.symbol_dim,
            "representation_version": self.representation_version,
        }


def _zone_index(zone: object) -> int:
    if type(zone) is not str or zone not in _ZONE_INDEX_V1:
        raise NeuralModelV1Error(f"unknown semantic zone {zone!r}")
    return _ZONE_INDEX_V1[zone]


def _visibility_index(visibility: object) -> int:
    if type(visibility) is not str or visibility not in _VISIBILITY_INDEX_V1:
        raise NeuralModelV1Error(f"unknown visibility {visibility!r}")
    return _VISIBILITY_INDEX_V1[visibility]


def _bounded(value: int, *, limit: int, field: str) -> int:
    if type(value) is not int or not 0 <= value < limit:
        raise NeuralModelV1Error(f"{field} is outside its frozen domain")
    return value


class SpecialistPolicyModelV1(nn.Module):
    """Encode the actor-visible state once, then score each legal semantic class."""

    def __init__(self, config: SpecialistModelConfigV1) -> None:
        super().__init__()
        if type(config) is not SpecialistModelConfigV1:
            raise NeuralModelV1Error("config must be a SpecialistModelConfigV1")
        self.config = config
        dim, card_dim, symbol = config.hidden_dim, config.card_dim, config.symbol_dim

        # index 0 is reserved padding in every card slot.
        self.card_embedding = nn.Embedding(config.card_vocabulary_size + 1, card_dim, padding_idx=0)
        self.zone_embedding = nn.Embedding(len(SEMANTIC_ZONE_VOCABULARY_V1), symbol)
        self.visibility_embedding = nn.Embedding(len(VISIBILITY_VOCABULARY_V1), symbol)
        self.option_embedding = nn.Embedding(MAX_OPTION_TYPE_V1, symbol)
        self.selection_type_embedding = nn.Embedding(MAX_SELECTION_TYPE_V1, symbol)
        self.selection_context_embedding = nn.Embedding(MAX_SELECTION_CONTEXT_V1, symbol)

        self.scalar_encoder = nn.Linear(_STATE_SCALARS_V1, dim)
        self.bag_encoder = nn.Linear(card_dim * len(_BAG_NAMES_V1), dim)
        self.single_card_encoder = nn.Linear(card_dim * len(_SINGLE_CARD_NAMES_V1), dim)
        # Pokémon now retain zone, energy-type composition, and attachment
        # identities instead of collapsing to one card id plus six scalars.
        self.pokemon_encoder = nn.Linear(card_dim + symbol + 6 + 12 + card_dim * 3, dim)
        self.pokemon_count_encoder = nn.Linear(1, dim)
        self.state_norm = nn.LayerNorm(dim)
        self.state_mix = nn.Sequential(nn.Linear(dim * 4, dim), nn.GELU(), nn.Linear(dim, dim))

        endpoint_dim = card_dim * 2 + symbol * 2 + 1 + dim + 1
        self.endpoint_encoder = nn.Linear(endpoint_dim, dim)
        candidate_dim = dim * 3 + symbol * 3 + len(_OPTIONAL_INT_FIELDS_V1) * 2
        self.candidate_mix = nn.Sequential(
            nn.Linear(candidate_dim, dim), nn.GELU(), nn.Linear(dim, dim)
        )
        self.candidate_norm = nn.LayerNorm(dim)
        self.candidate_bias = nn.Linear(dim, 1)

        self.position_embedding = nn.Embedding(2, dim)  # unordered vs ordered prefix
        self.query = nn.Sequential(nn.Linear(dim * 2, dim), nn.GELU(), nn.Linear(dim, dim))
        self.stop_vector = nn.Parameter(torch.zeros(dim))
        self.stop_bias = nn.Parameter(torch.zeros(()))
        # The value head the design specifies alongside the STOP score ("one
        # STOP score and one value head").  It reads the same encoded decision
        # state the candidate scorer does, so one backbone pass serves both the
        # policy and its baseline.
        self.value_head = nn.Sequential(nn.Linear(dim, dim), nn.GELU(), nn.Linear(dim, 1))
        # The public state may be identical while matchup difficulty differs.
        # This small critic-only table prevents a pooled opponent mixture from
        # forcing the policy baseline to explain every opponent with one scalar.
        self.opponent_value_embedding = nn.Embedding(_OPPONENT_VALUE_BUCKETS_V1, 1)

    # -- feature builders -------------------------------------------------

    def _card(self, card_id: int) -> torch.Tensor:
        index = _bounded(card_id, limit=self.config.card_vocabulary_size + 1, field="card_id")
        return self.card_embedding(torch.tensor(index, dtype=torch.long))

    def _endpoint(self, endpoint: SemanticEndpointV1) -> torch.Tensor:
        pokemon = (
            self._pokemon(endpoint.pokemon)
            if endpoint.pokemon is not None
            else torch.zeros(self.config.hidden_dim)
        )
        parts = [
            self._card(endpoint.card_id),
            self._card(endpoint.host_card_id),
            self.zone_embedding(torch.tensor(_zone_index(endpoint.semantic_zone), dtype=torch.long)),
            self.visibility_embedding(
                torch.tensor(_visibility_index(endpoint.visibility), dtype=torch.long)
            ),
            torch.tensor([float(endpoint.owner_role)], dtype=torch.float32),
            pokemon,
            torch.tensor([float(endpoint.pokemon is not None)], dtype=torch.float32),
        ]
        return self.endpoint_encoder(torch.cat(parts))

    def _pokemon(self, entity: PokemonEntityV1) -> torch.Tensor:
        max_hp = float(entity.max_hp) or 1.0
        scalars = torch.tensor(
            [
                float(entity.owner_role),
                float(entity.hp) / max_hp,
                math.log1p(float(entity.max_hp)),
                float(entity.appear_this_turn),
                math.log1p(float(sum(entity.energy_type_counts))),
                math.log1p(float(len([card for card in entity.tools if card]))),
            ],
            dtype=torch.float32,
        )
        def attachment(values: tuple[int, ...]) -> torch.Tensor:
            if not values:
                return torch.zeros(self.config.card_dim)
            return self.card_embedding(torch.tensor(values, dtype=torch.long)).sum(dim=0)

        return self.pokemon_encoder(torch.cat([
            self._card(entity.card_id),
            self.zone_embedding(torch.tensor(_zone_index(entity.zone), dtype=torch.long)),
            scalars,
            torch.tensor([math.log1p(float(value)) for value in entity.energy_type_counts], dtype=torch.float32),
            attachment(entity.energy_cards),
            attachment(entity.tools),
            attachment(entity.pre_evolution),
        ]))

    def _encode_state_scalars(self, values: Sequence[int]) -> torch.Tensor:
        if len(values) != _STATE_SCALARS_V1:
            raise NeuralModelV1Error("state scalar vector has the wrong length")
        return torch.tensor(
            [
                float(value) if index in _CATEGORICAL_STATE_SCALAR_INDICES_V1
                else math.log1p(float(value))
                for index, value in enumerate(values)
            ],
            dtype=torch.float32,
        )

    def encode_state(self, model_input: SpecialistModelInputV1) -> torch.Tensor:
        """Encode the decision state once; it is shared by every step of the decision."""
        if type(model_input) is not SpecialistModelInputV1:
            raise NeuralModelV1Error("model_input must be a SpecialistModelInputV1")
        scalars = self._encode_state_scalars(model_input.state_scalars)
        state = self.scalar_encoder(scalars)

        bags = []
        for name in _BAG_NAMES_V1:
            bag = model_input.card_bags[name]
            tokens = torch.tensor(bag.tokens, dtype=torch.long)
            mask = torch.tensor(bag.mask, dtype=torch.float32).unsqueeze(-1)
            if int(tokens.max()) > self.config.card_vocabulary_size:
                raise NeuralModelV1Error("card bag token is outside the vocabulary")
            # Sum pooling is permutation invariant, which a multiset requires.
            bags.append((self.card_embedding(tokens) * mask).sum(dim=0))
        bag_state = self.bag_encoder(torch.cat(bags))

        singles = [self._card(model_input.single_card_ids[name]) for name in _SINGLE_CARD_NAMES_V1]
        single_state = self.single_card_encoder(torch.cat(singles))

        if model_input.pokemon_entities:
            pokemon = torch.stack([self._pokemon(item) for item in model_input.pokemon_entities])
            pokemon_state = pokemon.mean(dim=0) + self.pokemon_count_encoder(
                torch.tensor([[math.log1p(float(len(model_input.pokemon_entities)))]], dtype=torch.float32)
            ).squeeze(0)
        else:
            pokemon_state = torch.zeros(self.config.hidden_dim)

        merged = torch.cat([state, bag_state, single_state, pokemon_state])
        return self.state_norm(self.state_mix(merged))

    def state_value_from_state(
        self, state: torch.Tensor, opponent_instance_id: str | None = None,
    ) -> torch.Tensor:
        """Return ``V(x)`` for an already-encoded decision state, as a 0-D tensor.

        Takes the encoded state rather than a ``SpecialistModelInputV1`` so a
        caller that already needs the state for scoring candidates pays for one
        ``encode_state`` instead of two.  :meth:`state_value` is the convenience
        entry point for callers that have only the model input.
        """
        if type(state) is not torch.Tensor:
            raise NeuralModelV1Error("state must be a torch.Tensor")
        if tuple(state.shape) != (self.config.hidden_dim,):
            raise NeuralModelV1Error(
                f"state must have shape ({self.config.hidden_dim},), got {tuple(state.shape)}"
            )
        value = self.value_head(state).squeeze(-1)
        if opponent_instance_id is not None:
            if type(opponent_instance_id) is not str or not opponent_instance_id:
                raise NeuralModelV1Error("opponent_instance_id must be a nonempty string")
            bucket = int.from_bytes(
                hashlib.sha256(opponent_instance_id.encode("utf-8")).digest()[:4], "big"
            ) % _OPPONENT_VALUE_BUCKETS_V1
            value = value + self.opponent_value_embedding(
                torch.tensor(bucket, dtype=torch.long)
            ).squeeze(-1)
        return value

    def state_value(self, model_input: SpecialistModelInputV1) -> torch.Tensor:
        """Return ``V(x)`` for one decision state, as a 0-D tensor."""
        return self.state_value_from_state(self.encode_state(model_input))

    def encode_candidate(self, action: SemanticActionV1) -> torch.Tensor:
        """Encode one semantic class independently of its position in the legal set."""
        if type(action) is not SemanticActionV1:
            raise NeuralModelV1Error("candidate must be a SemanticActionV1")
        optional: list[float] = []
        for name in _OPTIONAL_INT_FIELDS_V1:
            value = getattr(action, name)
            optional.extend((0.0, 0.0) if value is None else (1.0, math.log1p(float(abs(value)))))
        parts = [
            self._endpoint(action.source),
            self._endpoint(action.target),
            self._endpoint(action.host),
            self.option_embedding(
                torch.tensor(
                    _bounded(action.option_type, limit=MAX_OPTION_TYPE_V1, field="option_type"),
                    dtype=torch.long,
                )
            ),
            self.selection_type_embedding(
                torch.tensor(
                    _bounded(
                        action.selection_type, limit=MAX_SELECTION_TYPE_V1, field="selection_type"
                    ),
                    dtype=torch.long,
                )
            ),
            self.selection_context_embedding(
                torch.tensor(
                    _bounded(
                        action.selection_context,
                        limit=MAX_SELECTION_CONTEXT_V1,
                        field="selection_context",
                    ),
                    dtype=torch.long,
                )
            ),
            torch.tensor(optional, dtype=torch.float32),
        ]
        return self.candidate_norm(self.candidate_mix(torch.cat(parts)))

    def encode_candidates_batch(self, actions: Sequence[SemanticActionV1]) -> torch.Tensor:
        """Encode ``len(actions)`` semantic candidates in one vectorized pass.

        Mathematically the same computation :meth:`encode_candidate` performs
        per action, stacked -- every op here (embedding lookup, ``nn.Linear``,
        ``nn.LayerNorm``, GELU) acts on each row independently, so batching
        rows together changes only how many Python/dispatch calls happen,
        never which value a given row's forward pass computes (up to
        ordinary floating-point batched-GEMM rounding; see
        ``tests/meta_specialist/test_neural_model_v1.py::test_encode_candidates_batch_matches_the_sequential_path``
        for the measured tolerance). This method is new and additive:
        :meth:`encode_candidate` and the default (``candidate_cache=None``)
        path of :meth:`step_logits_from_state` never call it, so no existing
        caller is affected by its existence.
        """
        if type(actions) not in (list, tuple):
            raise NeuralModelV1Error("actions must be a list or tuple of SemanticActionV1")
        if any(type(item) is not SemanticActionV1 for item in actions):
            raise NeuralModelV1Error("every candidate must be a SemanticActionV1")
        if not actions:
            return torch.zeros((0, self.config.hidden_dim))

        def endpoint_batch(get: Any) -> torch.Tensor:
            # Nested Pokemon snapshots have variable-length attachment bags;
            # use the authoritative single-endpoint path so sequential and
            # batched candidate encoding retain exactly the same information.
            return torch.stack([self._endpoint(get(a)) for a in actions])

        source = endpoint_batch(lambda a: a.source)
        target = endpoint_batch(lambda a: a.target)
        host = endpoint_batch(lambda a: a.host)
        option = self.option_embedding(
            torch.tensor(
                [_bounded(a.option_type, limit=MAX_OPTION_TYPE_V1, field="option_type") for a in actions],
                dtype=torch.long,
            )
        )
        selection_type = self.selection_type_embedding(
            torch.tensor(
                [_bounded(a.selection_type, limit=MAX_SELECTION_TYPE_V1, field="selection_type") for a in actions],
                dtype=torch.long,
            )
        )
        selection_context = self.selection_context_embedding(
            torch.tensor(
                [
                    _bounded(a.selection_context, limit=MAX_SELECTION_CONTEXT_V1, field="selection_context")
                    for a in actions
                ],
                dtype=torch.long,
            )
        )
        optional_rows: list[list[float]] = []
        for action in actions:
            row: list[float] = []
            for name in _OPTIONAL_INT_FIELDS_V1:
                value = getattr(action, name)
                row.extend((0.0, 0.0) if value is None else (1.0, math.log1p(float(abs(value)))))
            optional_rows.append(row)
        optional = torch.tensor(optional_rows, dtype=torch.float32)
        parts = torch.cat([source, target, host, option, selection_type, selection_context, optional], dim=-1)
        return self.candidate_norm(self.candidate_mix(parts))

    def step_logits_from_state(
        self,
        state: torch.Tensor,
        step_input: SpecialistStepInputV1,
        *,
        candidate_cache: dict[SemanticActionV1, torch.Tensor] | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        """Score one autoregressive step against an already-encoded decision ``state``.

        ``state`` must be exactly this model's own ``encode_state(model_input)``
        for the model_input the caller intends; hoisting that call out lets a
        caller that scores many steps of the *same* decision (one collected
        trajectory transition -- see ``trajectory_target_v1.py``) compute it
        once instead of once per step, which is a pure reuse of a
        deterministic value and therefore changes no output, only how many
        times it is computed.

        With ``candidate_cache=None`` (the default) this performs exactly the
        same unbatched, one-``encode_candidate``-call-per-row computation
        :meth:`step_logits` has always performed; :meth:`step_logits`
        delegates here for precisely that reason, so its output is bit-for-bit
        unaffected by this method's existence. With a caller-supplied
        ``candidate_cache`` dict, a candidate already scored earlier in that
        dict's lifetime is reused rather than recomputed, and every *new*
        candidate this call needs is encoded in one batched
        :meth:`encode_candidates_batch` call instead of one Python call per
        candidate. The cache is never created or cleared here -- its lifetime
        (and therefore how far reuse extends) is entirely the caller's
        decision.
        """
        if type(step_input) is not SpecialistStepInputV1:
            raise NeuralModelV1Error("step_input must be a SpecialistStepInputV1")
        ordered = step_input.order_semantics == "ordered_sequence"

        def encode_many(actions: Sequence[SemanticActionV1]) -> torch.Tensor:
            if not actions:
                return torch.zeros((0, self.config.hidden_dim))
            if candidate_cache is None:
                return torch.stack([self.encode_candidate(item) for item in actions])
            missing: list[SemanticActionV1] = []
            for item in actions:
                if item not in candidate_cache and item not in missing:
                    missing.append(item)
            if missing:
                encoded = self.encode_candidates_batch(missing)
                for item, vector in zip(missing, encoded):
                    candidate_cache[item] = vector
            return torch.stack([candidate_cache[item] for item in actions])

        if step_input.semantic_prefix:
            encoded = encode_many(step_input.semantic_prefix)
            if ordered:
                # Only an ordered schema may see position; a set must not.
                encoded = encoded * torch.arange(
                    1, encoded.shape[0] + 1, dtype=torch.float32
                ).unsqueeze(-1).reciprocal()
            prefix = encoded.sum(dim=0)
        else:
            prefix = torch.zeros(self.config.hidden_dim)
        prefix = prefix + self.position_embedding(torch.tensor(int(ordered), dtype=torch.long))

        query = self.query(torch.cat([state, prefix]))
        scale = 1.0 / math.sqrt(float(self.config.hidden_dim))
        if step_input.allowed_semantic_classes:
            candidates = encode_many([item.semantic_row for item in step_input.allowed_semantic_classes])
            semantic = candidates @ query * scale + self.candidate_bias(candidates).squeeze(-1)
        else:
            semantic = torch.zeros(0)
        stop = (
            self.stop_vector @ query * scale + self.stop_bias
            if step_input.stop_available
            else None
        )
        return semantic, stop

    def step_logits(
        self, model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        """Return ``(semantic_logits, stop_logit)`` for one autoregressive step."""
        if type(step_input) is not SpecialistStepInputV1:
            raise NeuralModelV1Error("step_input must be a SpecialistStepInputV1")
        state = self.encode_state(model_input)
        return self.step_logits_from_state(state, step_input, candidate_cache=None)

    def forward(
        self, model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        return self.step_logits(model_input, step_input)


class TorchStepLogitPolicyV1:
    """Adapt the module to :class:`SpecialistStepLogitPolicyV1` with per-step caching."""

    def __init__(self, model: SpecialistPolicyModelV1) -> None:
        if type(model) is not SpecialistPolicyModelV1:
            raise NeuralModelV1Error("model must be a SpecialistPolicyModelV1")
        self._model = model
        self._model.eval()
        self._cache: dict[tuple[int, Any], SpecialistStepLogitsV1] = {}
        self.inference_calls = 0

    def reset(self) -> None:
        self._cache.clear()

    def _key(self, model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1):
        # Exact model-input identity plus canonical step bytes: one inference per
        # distinct prefix, and never a shared entry across two decisions.
        return (
            id(model_input),
            step_input.order_semantics,
            canonical_json_bytes_v2([item.to_dict() for item in step_input.semantic_prefix]),
            canonical_json_bytes_v2(
                [item.semantic_row.to_dict() for item in step_input.allowed_semantic_classes]
            ),
            step_input.stop_available,
        )

    def logits(
        self, model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1,
    ) -> SpecialistStepLogitsV1:
        key = self._key(model_input, step_input)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        self.inference_calls += 1
        with torch.inference_mode():
            semantic, stop = self._model.step_logits(model_input, step_input)
        values = tuple(float(value) for value in semantic.tolist())
        stop_value = None if stop is None else float(stop)
        if any(not math.isfinite(value) for value in values) or (
            stop_value is not None and not math.isfinite(stop_value)
        ):
            raise NeuralModelV1Error("model produced a non-finite logit")
        result = SpecialistStepLogitsV1(semantic_logits=values, stop_logit=stop_value)
        self._cache[key] = result
        return result


def build_specialist_policy_model_v1(
    config: SpecialistModelConfigV1, *, seed: int,
) -> SpecialistPolicyModelV1:
    """Build one deterministically initialized model on CPU."""
    if type(seed) is not int:
        raise NeuralModelV1Error("seed must be an int")
    generator_state = torch.random.get_rng_state()
    try:
        torch.manual_seed(seed)
        model = SpecialistPolicyModelV1(config)
    finally:
        torch.random.set_rng_state(generator_state)
    return model


__all__ = [
    "MAX_OPTION_TYPE_V1", "MAX_SELECTION_CONTEXT_V1", "MAX_SELECTION_TYPE_V1",
    "NEURAL_MODEL_SCHEMA_V1", "NeuralModelV1Error", "SEMANTIC_ZONE_VOCABULARY_V1",
    "SpecialistModelConfigV1", "SpecialistPolicyModelV1", "TorchStepLogitPolicyV1",
    "VISIBILITY_VOCABULARY_V1", "build_specialist_policy_model_v1",
]
