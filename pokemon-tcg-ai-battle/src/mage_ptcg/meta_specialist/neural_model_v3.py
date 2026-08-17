"""Relation-aware candidate policy model for Meta Specialist v3.

The module contains both representation candidates from the experiment plan:
``ZoneDeepSetsEncoderV3`` (R3-A) and ``RelationAwareEncoderV3`` (R3-B).  Both
share the same typed state contract and intentionally avoid positional/entity
serial features, so exchangeable public objects remain exchangeable.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from mage_ptcg.meta_specialist.representation_v3 import (
    ActionCandidateV3,
    EntityTokenV3,
    PublicEntityLocatorV3,
    RelationalStateV3,
    SemanticPrefixTokenV3,
)


NEURAL_MODEL_SCHEMA_V3 = "specialist-neural-model-v3"


class NeuralModelV3Error(ValueError):
    """Raised when v3 model inputs or topology are invalid."""


@dataclass(frozen=True, slots=True)
class StateEncodingV3:
    global_token: torch.Tensor
    entity_tokens: torch.Tensor
    entity_ids: tuple[int, ...]
    entity_locators: tuple[PublicEntityLocatorV3 | None, ...]


@dataclass(frozen=True, slots=True)
class PolicyOutputV3:
    logits: torch.Tensor
    global_token: torch.Tensor
    hidden_state: torch.Tensor | None = None


def _seeded_module(seed: int, factory):
    if type(seed) is not int:
        raise NeuralModelV3Error("seed must be an int")
    state = torch.random.get_rng_state()
    try:
        torch.manual_seed(seed)
        return factory()
    finally:
        torch.random.set_rng_state(state)


def _pad_float(values: tuple[float, ...], width: int, *, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
    result = torch.zeros(width, dtype=dtype, device=device)
    if values:
        result[: min(width, len(values))] = torch.tensor(values[:width], dtype=dtype, device=device)
    return result


def _pad_int(values: tuple[int, ...], width: int, *, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
    result = torch.zeros(width, dtype=dtype, device=device)
    if values:
        result[: min(width, len(values))] = torch.tensor(values[:width], dtype=dtype, device=device)
    return result


class _EntityEncoderBase(nn.Module):
    """Common typed-feature embeddings and deterministic entity projection."""

    def __init__(
        self,
        *,
        card_vocabulary_size: int,
        hidden_dim: int,
        embedding_dim: int,
        state_scalar_dim: int,
        entity_scalar_dim: int,
        entity_categorical_dim: int,
        entity_flag_dim: int,
        seed: int,
    ) -> None:
        if card_vocabulary_size < 1 or hidden_dim < 1 or embedding_dim < 1:
            raise NeuralModelV3Error("model dimensions must be positive")

        def build() -> None:
            super(_EntityEncoderBase, self).__init__()
            self.card_embedding = nn.Embedding(card_vocabulary_size + 1, embedding_dim, padding_idx=0)
            self.entity_type_embedding = nn.Embedding(32, embedding_dim)
            self.owner_embedding = nn.Embedding(3, embedding_dim)
            self.zone_embedding = nn.Embedding(32, embedding_dim)
            self.categorical_embedding = nn.Embedding(128, embedding_dim)
            self.scalar_projection = nn.Sequential(
                nn.Linear(entity_scalar_dim, embedding_dim), nn.LayerNorm(embedding_dim), nn.GELU(),
            )
            self.flag_projection = nn.Sequential(
                nn.Linear(entity_flag_dim, embedding_dim), nn.LayerNorm(embedding_dim), nn.GELU(),
            )
            self.entity_projection = nn.Sequential(
                nn.Linear(embedding_dim * 7, embedding_dim), nn.LayerNorm(embedding_dim), nn.GELU(),
                nn.Linear(embedding_dim, embedding_dim),
            )
            self.global_projection = nn.Sequential(
                nn.Linear(state_scalar_dim, embedding_dim), nn.LayerNorm(embedding_dim), nn.GELU(),
            )
            self.entity_pool_projection = nn.Sequential(
                nn.Linear(embedding_dim, embedding_dim), nn.LayerNorm(embedding_dim), nn.GELU(),
            )
            self.embedding_dim = embedding_dim
            self.hidden_dim = hidden_dim
            self.card_vocabulary_size = card_vocabulary_size
            self.entity_scalar_dim = entity_scalar_dim
            self.entity_categorical_dim = entity_categorical_dim
            self.entity_flag_dim = entity_flag_dim

        _seeded_module(seed, build)

    @staticmethod
    def _bounded(value: int, *, maximum: int, name: str) -> int:
        if type(value) is not int or value < 0 or value > maximum:
            raise NeuralModelV3Error(f"{name} is outside the v3 vocabulary")
        return value

    @property
    def _device(self) -> torch.device:
        return self.card_embedding.weight.device

    @property
    def _dtype(self) -> torch.dtype:
        return self.card_embedding.weight.dtype

    def _base_entity(self, entity: EntityTokenV3) -> torch.Tensor:
        card_id = self._bounded(entity.card_id, maximum=self.card_vocabulary_size, name="card_id")
        categorical = [min(value, 127) for value in entity.categorical_features]
        categorical_vector = (
            self.categorical_embedding(torch.tensor(categorical, dtype=torch.long, device=self._device)).mean(dim=0)
            if categorical else torch.zeros(self.embedding_dim, dtype=self._dtype, device=self._device)
        )
        scalar = self.scalar_projection(_pad_float(entity.scalar_features, self.entity_scalar_dim, device=self._device, dtype=self._dtype))
        flags = self.flag_projection(_pad_int(entity.binary_flags, self.entity_flag_dim, device=self._device, dtype=self._dtype))
        return self.entity_projection(torch.cat([
            self.card_embedding(torch.tensor(card_id, dtype=torch.long, device=self._device)),
            self.entity_type_embedding(torch.tensor(entity.entity_type, dtype=torch.long, device=self._device)),
            self.owner_embedding(torch.tensor(entity.owner, dtype=torch.long, device=self._device)),
            self.zone_embedding(torch.tensor(entity.zone, dtype=torch.long, device=self._device)),
            categorical_vector, scalar, flags,
        ]))

    def _global(self, state: RelationalStateV3) -> torch.Tensor:
        return self.global_projection(_pad_float(state.state_scalars, self.global_projection[0].in_features, device=self._device, dtype=self._dtype))

    def _base_tokens(self, state: RelationalStateV3) -> tuple[tuple[EntityTokenV3, ...], torch.Tensor]:
        ordered = state.canonical_entity_order()
        if not ordered:
            base = torch.zeros((0, self.embedding_dim), dtype=self._dtype, device=self._device)
            return ordered, base

        # Batch the fixed-width portions of entity encoding.  The earlier
        # per-entity implementation was functionally correct but paid Python
        # and tiny-kernel overhead once per card-bag token; this path keeps the
        # same closed feature rules while making p50/p95 latency measurable.
        count = len(ordered)
        card_ids = torch.tensor([self._bounded(entity.card_id, maximum=self.card_vocabulary_size, name="card_id") for entity in ordered], dtype=torch.long, device=self._device)
        entity_types = torch.tensor([entity.entity_type for entity in ordered], dtype=torch.long, device=self._device)
        owners = torch.tensor([entity.owner for entity in ordered], dtype=torch.long, device=self._device)
        zones = torch.tensor([entity.zone for entity in ordered], dtype=torch.long, device=self._device)
        scalar_values = torch.stack([_pad_float(entity.scalar_features, self.entity_scalar_dim, device=self._device, dtype=self._dtype) for entity in ordered])
        flag_values = torch.stack([_pad_int(entity.binary_flags, self.entity_flag_dim, device=self._device, dtype=self._dtype) for entity in ordered])
        categorical_width = max((len(entity.categorical_features) for entity in ordered), default=0)
        if categorical_width:
            cat_values = torch.zeros((count, categorical_width), dtype=torch.long, device=self._device)
            cat_mask = torch.zeros((count, categorical_width), dtype=self._dtype, device=self._device)
            for row, entity in enumerate(ordered):
                width = len(entity.categorical_features)
                if width:
                    cat_values[row, :width] = torch.tensor([min(value, 127) for value in entity.categorical_features], dtype=torch.long, device=self._device)
                    cat_mask[row, :width] = 1.0
            cat_emb = self.categorical_embedding(cat_values)
            categorical_vector = (cat_emb * cat_mask.unsqueeze(-1)).sum(1) / cat_mask.sum(1, keepdim=True).clamp_min(1.0)
        else:
            categorical_vector = torch.zeros((count, self.embedding_dim), dtype=self._dtype, device=self._device)
        base = self.entity_projection(torch.cat([
            self.card_embedding(card_ids), self.entity_type_embedding(entity_types),
            self.owner_embedding(owners), self.zone_embedding(zones), categorical_vector,
            self.scalar_projection(scalar_values), self.flag_projection(flag_values),
        ], dim=-1))
        return ordered, base

    def _relation_tokens(
        self, ordered: tuple[EntityTokenV3, ...], base: torch.Tensor,
    ) -> torch.Tensor:
        if not ordered:
            return base
        index = {entity.entity_id: position for position, entity in enumerate(ordered)}
        relation_rows = []
        for entity, own in zip(ordered, base):
            owner_tokens = torch.stack([
                token for token, peer in zip(base, ordered)
                if peer.owner == entity.owner
            ]).mean(dim=0)
            owner_relation = self.owner_relation(torch.cat([own, owner_tokens]))
            active = next((
                token for token, peer in zip(base, ordered)
                if peer.owner == entity.owner and peer.zone == 1 and peer.entity_type == 1
            ), None)
            active_relation = (
                torch.zeros_like(own) if active is None or entity.zone == 1
                else self.active_relation(torch.cat([own, active]))
            )
            if entity.host_entity_id is None:
                host_relation = torch.zeros_like(own)
                evolution_relation = torch.zeros_like(own)
                same_host_relation = torch.zeros_like(own)
            else:
                host = base[index[entity.host_entity_id]]
                host_relation = (
                    self.host_relation(torch.cat([own, host])) if entity.entity_type != 4
                    else torch.zeros_like(own)
                )
                evolution_relation = (
                    self.evolution_relation(torch.cat([own, host])) if entity.entity_type == 4
                    else torch.zeros_like(own)
                )
                peers = [
                    token for token, peer in zip(base, ordered)
                    if peer.host_entity_id == entity.host_entity_id and peer.entity_id != entity.entity_id
                ]
                same_host_relation = (
                    self.same_host_relation(torch.cat([own, torch.stack(peers).mean(dim=0)]))
                    if peers else torch.zeros_like(own)
                )
            relation_rows.append(owner_relation + active_relation + host_relation + evolution_relation + same_host_relation)
        return base + torch.stack(relation_rows)

    def encode_state_v3(self, state: RelationalStateV3) -> StateEncodingV3:
        raise NotImplementedError


class ZoneDeepSetsEncoderV3(_EntityEncoderBase):
    """R3-A: nonlinear zone-specific DeepSets encoder."""

    def __init__(
        self, *, card_vocabulary_size: int, hidden_dim: int = 256, embedding_dim: int = 192,
        seed: int = 0, state_scalar_dim: int = 41, entity_scalar_dim: int = 8,
        entity_categorical_dim: int = 16, entity_flag_dim: int = 8,
    ) -> None:
        super().__init__(
            card_vocabulary_size=card_vocabulary_size, hidden_dim=hidden_dim, embedding_dim=embedding_dim,
            state_scalar_dim=state_scalar_dim, entity_scalar_dim=entity_scalar_dim,
            entity_categorical_dim=entity_categorical_dim, entity_flag_dim=entity_flag_dim, seed=seed,
        )

        def build() -> None:
            self.host_relation = nn.Sequential(
                nn.Linear(embedding_dim * 2, embedding_dim), nn.GELU(), nn.Linear(embedding_dim, embedding_dim),
            )
            self.owner_relation = nn.Sequential(
                nn.Linear(embedding_dim * 2, embedding_dim), nn.GELU(), nn.Linear(embedding_dim, embedding_dim),
            )
            self.active_relation = nn.Sequential(
                nn.Linear(embedding_dim * 2, embedding_dim), nn.GELU(), nn.Linear(embedding_dim, embedding_dim),
            )
            self.same_host_relation = nn.Sequential(
                nn.Linear(embedding_dim * 2, embedding_dim), nn.GELU(), nn.Linear(embedding_dim, embedding_dim),
            )
            self.evolution_relation = nn.Sequential(
                nn.Linear(embedding_dim * 2, embedding_dim), nn.GELU(), nn.Linear(embedding_dim, embedding_dim),
            )
            self.zone_pool = nn.ModuleDict({
                name: nn.Sequential(
                    nn.Linear(embedding_dim, embedding_dim), nn.LayerNorm(embedding_dim), nn.GELU(),
                    nn.Linear(embedding_dim, embedding_dim),
                ) for name in ("own-active", "own-bench", "opponent-active", "opponent-bench", "other-public")
            })
            self.output_projection = nn.Sequential(
                nn.Linear(embedding_dim * 6, hidden_dim), nn.LayerNorm(hidden_dim), nn.GELU(),
            )
        _seeded_module(seed + 101, build)

    def encode_state_v3(self, state: RelationalStateV3) -> StateEncodingV3:
        if type(state) is not RelationalStateV3:
            raise NeuralModelV3Error("state must be a RelationalStateV3")
        ordered, base = self._base_tokens(state)
        entity_tokens = self._relation_tokens(ordered, base)
        pools = []
        for name, predicate in (
            ("own-active", lambda entity: entity.owner == 1 and entity.zone == 1 and entity.entity_type == 1),
            ("own-bench", lambda entity: entity.owner == 1 and entity.zone == 2 and entity.entity_type == 1),
            ("opponent-active", lambda entity: entity.owner == 2 and entity.zone == 1 and entity.entity_type == 1),
            ("opponent-bench", lambda entity: entity.owner == 2 and entity.zone == 2 and entity.entity_type == 1),
            ("other-public", lambda entity: not (entity.entity_type == 1 and entity.zone in {1, 2})),
        ):
            selected = [token for token, entity in zip(entity_tokens, ordered) if predicate(entity)]
            pooled = torch.stack(selected).mean(dim=0) if selected else entity_tokens.new_zeros(self.embedding_dim)
            pools.append(self.zone_pool[name](pooled))
        pooled_entities = entity_tokens.mean(dim=0) if entity_tokens.numel() else entity_tokens.new_zeros(self.embedding_dim)
        global_token = self.output_projection(torch.cat([self._global(state) + self.entity_pool_projection(pooled_entities), *pools]))
        return StateEncodingV3(
            global_token=global_token, entity_tokens=entity_tokens,
            entity_ids=tuple(entity.entity_id for entity in ordered),
            entity_locators=tuple(entity.public_locator for entity in ordered),
        )


class RelationAwareEncoderV3(_EntityEncoderBase):
    """R3-B: relation-aware attention encoder (mainline candidate)."""

    def __init__(
        self, *, card_vocabulary_size: int, hidden_dim: int = 256, embedding_dim: int = 192,
        attention_heads: int = 4, attention_blocks: int = 2, seed: int = 0,
        state_scalar_dim: int = 41, entity_scalar_dim: int = 8,
        entity_categorical_dim: int = 16, entity_flag_dim: int = 8,
    ) -> None:
        if attention_heads < 1 or embedding_dim % attention_heads != 0:
            raise NeuralModelV3Error("embedding_dim must be divisible by attention_heads")
        super().__init__(
            card_vocabulary_size=card_vocabulary_size, hidden_dim=hidden_dim, embedding_dim=embedding_dim,
            state_scalar_dim=state_scalar_dim, entity_scalar_dim=entity_scalar_dim,
            entity_categorical_dim=entity_categorical_dim, entity_flag_dim=entity_flag_dim, seed=seed,
        )

        def build() -> None:
            self.host_relation = nn.Sequential(
                nn.Linear(embedding_dim * 2, embedding_dim), nn.GELU(), nn.Linear(embedding_dim, embedding_dim),
            )
            self.owner_relation = nn.Sequential(
                nn.Linear(embedding_dim * 2, embedding_dim), nn.GELU(), nn.Linear(embedding_dim, embedding_dim),
            )
            self.active_relation = nn.Sequential(
                nn.Linear(embedding_dim * 2, embedding_dim), nn.GELU(), nn.Linear(embedding_dim, embedding_dim),
            )
            self.same_host_relation = nn.Sequential(
                nn.Linear(embedding_dim * 2, embedding_dim), nn.GELU(), nn.Linear(embedding_dim, embedding_dim),
            )
            self.evolution_relation = nn.Sequential(
                nn.Linear(embedding_dim * 2, embedding_dim), nn.GELU(), nn.Linear(embedding_dim, embedding_dim),
            )
            self.attention = nn.ModuleList(
                nn.MultiheadAttention(embedding_dim, attention_heads, dropout=0.05, batch_first=True)
                for _ in range(attention_blocks)
            )
            self.attention_norm = nn.ModuleList(nn.LayerNorm(embedding_dim) for _ in range(attention_blocks))
            self.ffn = nn.ModuleList(
                nn.Sequential(nn.Linear(embedding_dim, 512), nn.GELU(), nn.Linear(512, embedding_dim))
                for _ in range(attention_blocks)
            )
            self.ffn_norm = nn.ModuleList(nn.LayerNorm(embedding_dim) for _ in range(attention_blocks))
            self.output_projection = nn.Sequential(nn.Linear(embedding_dim, hidden_dim), nn.LayerNorm(hidden_dim), nn.GELU())
        _seeded_module(seed + 101, build)

    def encode_state_v3(self, state: RelationalStateV3) -> StateEncodingV3:
        if type(state) is not RelationalStateV3:
            raise NeuralModelV3Error("state must be a RelationalStateV3")
        ordered, base = self._base_tokens(state)
        entity_tokens = self._relation_tokens(ordered, base)
        pooled_entities = entity_tokens.mean(dim=0) if entity_tokens.numel() else entity_tokens.new_zeros(self.embedding_dim)
        global_seed = self._global(state) + self.entity_pool_projection(pooled_entities)
        tokens = torch.cat([global_seed.unsqueeze(0), entity_tokens], dim=0).unsqueeze(0)
        for attention, attn_norm, ffn, ffn_norm in zip(self.attention, self.attention_norm, self.ffn, self.ffn_norm):
            normalized = attn_norm(tokens)
            attended, _ = attention(normalized, normalized, normalized, need_weights=False)
            tokens = tokens + attended
            tokens = tokens + ffn(ffn_norm(tokens))
        encoded = self.output_projection(tokens.squeeze(0))
        return StateEncodingV3(
            global_token=encoded[0], entity_tokens=encoded[1:],
            entity_ids=tuple(entity.entity_id for entity in ordered),
            entity_locators=tuple(entity.public_locator for entity in ordered),
        )


class SpecialistModelV3(nn.Module):
    """Relation-aware policy head with a single-layer recurrent memory."""

    def __init__(
        self, *, card_vocabulary_size: int, hidden_dim: int = 256, embedding_dim: int = 192,
        seed: int = 0, encoder_kind: str = "relation-attention",
    ) -> None:
        super().__init__()
        encoder_factory = RelationAwareEncoderV3 if encoder_kind == "relation-attention" else ZoneDeepSetsEncoderV3 if encoder_kind == "zone-deepsets" else None
        if encoder_factory is None:
            raise NeuralModelV3Error("encoder_kind must be relation-attention or zone-deepsets")
        self.encoder = encoder_factory(
            card_vocabulary_size=card_vocabulary_size, hidden_dim=hidden_dim, embedding_dim=embedding_dim, seed=seed,
        )
        self.action_type_embedding = nn.Embedding(64, hidden_dim)
        self.entity_reference_projection = nn.Linear(
            embedding_dim if encoder_kind == "zone-deepsets" else hidden_dim, hidden_dim,
        )
        # v1's candidate domain is capped at 512, so an ordered prefix may
        # legally have 0..512 selected public entities.  This is not a card
        # serial feature: positions apply only to an already state-bound
        # public locator sequence.
        self.selection_step_embedding = nn.Embedding(513, hidden_dim)
        self.selection_context_embedding = nn.Embedding(64, hidden_dim)
        self.selected_position_embedding = nn.Embedding(512, hidden_dim)
        self.selected_position_relation = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, hidden_dim),
        )
        self.source_target_relation = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, hidden_dim),
        )
        self.memory = nn.GRU(hidden_dim, hidden_dim, num_layers=1, batch_first=True)
        self.candidate_mix = nn.Sequential(
            nn.Linear(hidden_dim * 7 + 9, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, hidden_dim), nn.LayerNorm(hidden_dim),
        )
        self.candidate_bias = nn.Linear(hidden_dim, 1)
        self.stop_vector = nn.Parameter(torch.zeros(hidden_dim))
        self.stop_bias = nn.Parameter(torch.zeros(()))
        self.prefix_mix = nn.Sequential(nn.Linear(hidden_dim * 3 + 9, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, hidden_dim))
        self._initialize_head(seed + 1)

    def _initialize_head(self, seed: int) -> None:
        state = torch.random.get_rng_state()
        try:
            torch.manual_seed(seed)
            for module in (
                self.action_type_embedding, self.entity_reference_projection, self.selection_step_embedding, self.selection_context_embedding,
                self.selected_position_embedding, self.selected_position_relation,
                self.source_target_relation, self.memory, self.candidate_mix, self.candidate_bias, self.prefix_mix,
            ):
                def reset(child: nn.Module) -> None:
                    reset_parameters = getattr(child, "reset_parameters", None)
                    if callable(reset_parameters):
                        reset_parameters()
                module.apply(reset)
            nn.init.normal_(self.stop_vector, mean=0.0, std=0.02)
            self.stop_bias.data.zero_()
        finally:
            torch.random.set_rng_state(state)

    def _reference_embedding(
        self, locator: PublicEntityLocatorV3 | None, state_encoding: StateEncodingV3 | None,
    ) -> torch.Tensor:
        if locator is None or state_encoding is None:
            return self.action_type_embedding.weight.new_zeros(self.encoder.hidden_dim)
        if locator in state_encoding.entity_locators:
            return self.entity_reference_projection(state_encoding.entity_tokens[state_encoding.entity_locators.index(locator)])
        raise NeuralModelV3Error("candidate locator is not bound to the encoded state")

    def _selected_embedding(self, candidate: ActionCandidateV3, state_encoding: StateEncodingV3 | None) -> torch.Tensor:
        selected = [self._reference_embedding(locator, state_encoding) for locator in candidate.selected_locators]
        if not selected:
            return self.action_type_embedding.weight.new_zeros(self.encoder.hidden_dim)
        if not candidate.selection_order_sensitive:
            return torch.stack(selected).mean(dim=0)
        positioned = [
            self.selected_position_relation(torch.cat([
                token, self.selected_position_embedding(torch.tensor(
                    position, dtype=torch.long, device=token.device,
                )),
            ]))
            for position, token in enumerate(selected)
        ]
        return torch.stack(positioned).mean(dim=0)

    def encode_candidate_v3(self, candidate: ActionCandidateV3, *, state_encoding: StateEncodingV3 | None = None) -> torch.Tensor:
        if type(candidate) is not ActionCandidateV3:
            raise NeuralModelV3Error("candidate must be an ActionCandidateV3")
        categorical = list(candidate.categorical_args[:5]) + [0, 0, 0, 0, 0]
        numeric = list(candidate.numeric_args[:4]) + [0.0, 0.0, 0.0, 0.0]
        source = self._reference_embedding(candidate.source_locator, state_encoding)
        target = self._reference_embedding(candidate.target_locator, state_encoding)
        return self.candidate_mix(torch.cat([
            source,
            target,
            self.source_target_relation(torch.cat([source, target])),
            self.action_type_embedding(torch.tensor(candidate.action_type, dtype=torch.long, device=source.device)),
            self.selection_step_embedding(torch.tensor(
                candidate.selection_step if candidate.selection_order_sensitive else 0,
                dtype=torch.long, device=source.device,
            )),
            self.selection_context_embedding(torch.tensor(categorical[1] % 64, dtype=torch.long, device=source.device)),
            self._selected_embedding(candidate, state_encoding),
            torch.tensor(
                [float(value) for value in categorical[:5]] + numeric[:4],
                dtype=source.dtype, device=source.device,
            ),
        ]))

    def _prefix_embedding(self, state: RelationalStateV3, encoding: StateEncodingV3) -> torch.Tensor:
        if not state.semantic_prefix:
            return encoding.global_token.new_zeros(self.encoder.hidden_dim)
        rows: list[torch.Tensor] = []
        for position, token in enumerate(state.semantic_prefix):
            categorical = list(token.categorical_args[:5]) + [0] * 5
            numeric = list(token.numeric_args[:4]) + [0.0] * 4
            source = self._reference_embedding(token.source_locator, encoding)
            target = self._reference_embedding(token.target_locator, encoding)
            row = self.prefix_mix(torch.cat([
                source, target,
                self.action_type_embedding(torch.tensor(token.action_type, dtype=torch.long, device=source.device)),
                torch.tensor([float(value) for value in categorical[:5]] + numeric[:4], dtype=source.dtype, device=source.device),
            ]))
            if state.prefix_order_sensitive:
                # A plain ``row + position`` followed by mean pooling loses the
                # permutation: both summands have the same total after a swap.
                # Bind the action content and its ordinal through the existing
                # nonlinear relation encoder before aggregating instead.
                row = self.selected_position_relation(torch.cat([
                    row,
                    self.selected_position_embedding(torch.tensor(
                        position, dtype=torch.long, device=row.device,
                    )),
                ]))
            rows.append(row)
        return torch.stack(rows).mean(0)

    def forward_v3(
        self, state: RelationalStateV3, *, hidden_state: torch.Tensor | None = None,
        episode_start: bool = False,
    ) -> PolicyOutputV3:
        encoding = self.encoder.encode_state_v3(state)
        hidden = None if episode_start else hidden_state
        recurrent, next_hidden = self.memory(encoding.global_token.view(1, 1, -1), hidden)
        global_token = recurrent[0, 0] + self._prefix_embedding(state, encoding)
        if not state.candidates:
            logits = global_token.new_zeros((0,))
        else:
            rows = [self.encode_candidate_v3(candidate, state_encoding=encoding) for candidate in state.candidates]
            candidate_tokens = torch.stack(rows)
            logits = self.candidate_bias(torch.tanh(candidate_tokens + global_token)).squeeze(-1)
            duplicate_mask = torch.tensor(
                [candidate.excludes_selected_duplicate for candidate in state.candidates],
                dtype=torch.bool, device=logits.device,
            )
            logits = logits.masked_fill(duplicate_mask, float("-inf"))
        return PolicyOutputV3(logits=logits, global_token=global_token, hidden_state=next_hidden)

    def step_logits_v3(self, state: RelationalStateV3, *, stop_available: bool) -> tuple[torch.Tensor, torch.Tensor | None]:
        """Score the same semantic domain plus the legal STOP token, if any."""
        output = self.forward_v3(state, episode_start=True)
        stop = self.stop_vector @ output.global_token + self.stop_bias if stop_available else None
        return output.logits, stop


__all__ = [
    "NEURAL_MODEL_SCHEMA_V3", "NeuralModelV3Error", "PolicyOutputV3",
    "RelationAwareEncoderV3", "SpecialistModelV3", "StateEncodingV3", "ZoneDeepSetsEncoderV3",
]
