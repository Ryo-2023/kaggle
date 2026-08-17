from __future__ import annotations

from dataclasses import dataclass
import math


MAX_REFERENCE_CLASSES_V1 = 64
MAX_REFERENCE_ALIAS_CANDIDATES_V1 = 64
MAX_REFERENCE_SELECTION_LENGTH_V1 = 64
MAX_REFERENCE_COMPLETE_ACTIONS_V1 = 65_536
MAX_REFERENCE_ROWS_V1 = 65_536
MAX_SEMANTIC_TOKEN_BYTES_V1 = 4_096

_MASS_ABS_TOLERANCE_V1 = 1.0e-12


class ReferenceLossError(ValueError):
    pass


def _fail(message: str) -> None:
    raise ReferenceLossError(message)


def _require_tuple(value: object, name: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        _fail(f"{name} must be an exact tuple")
    return value


def _require_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        _fail(f"{name} must be an exact bool")
    return value


def _require_int(value: object, name: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        _fail(f"{name} must be an exact int")
    if value < minimum or value > maximum:
        _fail(f"{name} is outside the supported range")
    return value


def _require_finite_float(value: object, name: str) -> float:
    if type(value) is not float:
        _fail(f"{name} must be an exact float")
    if not math.isfinite(value):
        _fail(f"{name} must be finite")
    return value


def _require_unit_float(value: object, name: str) -> float:
    result = _require_finite_float(value, name)
    if result < 0.0 or result > 1.0:
        _fail(f"{name} must be in [0, 1]")
    return result


def _require_log_probability(value: object, name: str) -> float:
    if type(value) is not float:
        _fail(f"{name} must be an exact float")
    if math.isnan(value) or value == math.inf:
        _fail(f"{name} must be a finite value or negative infinity")
    if value > _MASS_ABS_TOLERANCE_V1:
        _fail(f"{name} cannot be positive")
    return value


def _require_token(value: object, name: str) -> bytes:
    if type(value) is not bytes:
        _fail(f"{name} must be exact bytes")
    if not value:
        _fail(f"{name} cannot be empty")
    if len(value) > MAX_SEMANTIC_TOKEN_BYTES_V1:
        _fail(f"{name} is too large")
    return value


def _require_tokens(value: object, name: str) -> tuple[bytes, ...]:
    raw = _require_tuple(value, name)
    if len(raw) > MAX_REFERENCE_SELECTION_LENGTH_V1:
        _fail(f"{name} is too long")
    result: list[bytes] = []
    for position, token in enumerate(raw):
        result.append(_require_token(token, f"{name}[{position}]"))
    return tuple(result)


def _require_sorted_unique_tokens(value: object, name: str) -> tuple[bytes, ...]:
    tokens = _require_tokens(value, name)
    if tokens != tuple(sorted(tokens)) or len(tokens) != len(set(tokens)):
        _fail(f"{name} must be strictly increasing")
    return tokens


def _require_float_tuple(
    value: object,
    name: str,
    *,
    maximum_length: int = MAX_REFERENCE_CLASSES_V1,
) -> tuple[float, ...]:
    raw = _require_tuple(value, name)
    if len(raw) > maximum_length:
        _fail(f"{name} is too long")
    result: list[float] = []
    for position, item in enumerate(raw):
        result.append(_require_finite_float(item, f"{name}[{position}]"))
    return tuple(result)


def _require_probability_tuple(value: object, name: str) -> tuple[float, ...]:
    raw = _require_tuple(value, name)
    if len(raw) > MAX_REFERENCE_CLASSES_V1:
        _fail(f"{name} is too long")
    result: list[float] = []
    for position, item in enumerate(raw):
        result.append(_require_unit_float(item, f"{name}[{position}]"))
    return tuple(result)


def _is_unit_mass(value: float) -> bool:
    return math.isclose(value, 1.0, rel_tol=0.0, abs_tol=_MASS_ABS_TOLERANCE_V1)


def _same_mass(left: float, right: float) -> bool:
    if left == 0.0 or right == 0.0:
        return left == right
    return math.isclose(left, right, rel_tol=1.0e-12, abs_tol=0.0)


def _ordered_fsum(values: list[float] | tuple[float, ...]) -> float:
    return math.fsum(sorted(values))


@dataclass(frozen=True, slots=True)
class SemanticClassV1:
    token: bytes
    alias_count: int

    def __post_init__(self) -> None:
        _require_token(self.token, "token")
        _require_int(
            self.alias_count,
            "alias_count",
            minimum=1,
            maximum=MAX_REFERENCE_ALIAS_CANDIDATES_V1,
        )


@dataclass(frozen=True, slots=True)
class SemanticSelectionSpaceV1:
    classes: tuple[SemanticClassV1, ...]
    minimum: int
    maximum: int
    order_semantics: str

    def __post_init__(self) -> None:
        classes = _require_tuple(self.classes, "classes")
        if len(classes) > MAX_REFERENCE_CLASSES_V1:
            _fail("classes exceeds the reference bound")
        for position, semantic_class in enumerate(classes):
            if type(semantic_class) is not SemanticClassV1:
                _fail(f"classes[{position}] must be an exact SemanticClassV1")
        tokens = tuple(semantic_class.token for semantic_class in classes)
        if tokens != tuple(sorted(tokens)) or len(tokens) != len(set(tokens)):
            _fail("classes must be strictly increasing by token")
        total_aliases = sum(semantic_class.alias_count for semantic_class in classes)
        if total_aliases > MAX_REFERENCE_ALIAS_CANDIDATES_V1:
            _fail("total alias candidates exceeds the reference bound")
        _require_int(
            self.minimum,
            "minimum",
            minimum=0,
            maximum=MAX_REFERENCE_SELECTION_LENGTH_V1,
        )
        _require_int(
            self.maximum,
            "maximum",
            minimum=0,
            maximum=MAX_REFERENCE_SELECTION_LENGTH_V1,
        )
        if self.minimum > self.maximum:
            _fail("minimum cannot exceed maximum")
        if self.maximum > total_aliases:
            _fail("maximum cannot exceed total alias capacity")
        if type(self.order_semantics) is not str or self.order_semantics not in {
            "ordered",
            "unordered",
        }:
            _fail("order_semantics must be 'ordered' or 'unordered'")


@dataclass(frozen=True, slots=True)
class CompleteActionMassRowV1:
    semantic_selection: tuple[bytes, ...]
    mass: float

    def __post_init__(self) -> None:
        _require_tokens(self.semantic_selection, "semantic_selection")
        _require_unit_float(self.mass, "mass")


@dataclass(frozen=True, slots=True)
class SemanticCompleteMassV1:
    semantic_selection: tuple[bytes, ...]
    mass: float

    def __post_init__(self) -> None:
        _require_tokens(self.semantic_selection, "semantic_selection")
        _require_unit_float(self.mass, "mass")


@dataclass(frozen=True, slots=True)
class ConditionalTargetRowV1:
    semantic_prefix: tuple[bytes, ...]
    semantic_tokens: tuple[bytes, ...]
    stop_available: bool
    semantic_target_masses: tuple[float, ...]
    stop_target_mass: float | None
    reach_mass: float

    def __post_init__(self) -> None:
        _require_tokens(self.semantic_prefix, "semantic_prefix")
        tokens = _require_sorted_unique_tokens(self.semantic_tokens, "semantic_tokens")
        stop_available = _require_bool(self.stop_available, "stop_available")
        masses = _require_probability_tuple(
            self.semantic_target_masses,
            "semantic_target_masses",
        )
        if len(tokens) != len(masses):
            _fail("semantic target masses must match semantic tokens")
        if stop_available:
            stop_mass = _require_unit_float(self.stop_target_mass, "stop_target_mass")
        else:
            if self.stop_target_mass is not None:
                _fail("stop_target_mass must be None when STOP is unavailable")
            stop_mass = 0.0
        if not tokens and not stop_available:
            _fail("a conditional domain cannot be empty")
        if not _is_unit_mass(math.fsum((*masses, stop_mass))):
            _fail("conditional target masses must sum to one")
        reach_mass = _require_finite_float(self.reach_mass, "reach_mass")
        if reach_mass <= 0.0 or reach_mass > 1.0 + _MASS_ABS_TOLERANCE_V1:
            _fail("reach_mass must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class PushedForwardTargetsV1:
    space: SemanticSelectionSpaceV1
    complete_semantic_masses: tuple[SemanticCompleteMassV1, ...]
    conditional_targets: tuple[ConditionalTargetRowV1, ...]
    quality_weight: float

    def __post_init__(self) -> None:
        if type(self.space) is not SemanticSelectionSpaceV1:
            _fail("space must be an exact SemanticSelectionSpaceV1")
        complete = _require_tuple(self.complete_semantic_masses, "complete_semantic_masses")
        targets = _require_tuple(self.conditional_targets, "conditional_targets")
        if len(complete) > MAX_REFERENCE_COMPLETE_ACTIONS_V1:
            _fail("too many complete semantic masses")
        if len(targets) > MAX_REFERENCE_ROWS_V1:
            _fail("too many conditional targets")
        for row in complete:
            if type(row) is not SemanticCompleteMassV1:
                _fail("complete semantic masses must have exact row types")
        for row in targets:
            if type(row) is not ConditionalTargetRowV1:
                _fail("conditional targets must have exact row types")
        complete_keys = tuple(row.semantic_selection for row in complete)
        target_keys = tuple(row.semantic_prefix for row in targets)
        if complete_keys != tuple(sorted(complete_keys)) or len(complete_keys) != len(
            set(complete_keys)
        ):
            _fail("complete semantic masses must be sorted and unique")
        if target_keys != tuple(sorted(target_keys)) or len(target_keys) != len(set(target_keys)):
            _fail("conditional targets must be sorted and unique")
        if not complete or not _is_unit_mass(math.fsum(row.mass for row in complete)):
            _fail("complete semantic masses must sum to one")
        _require_unit_float(self.quality_weight, "quality_weight")


@dataclass(frozen=True, slots=True)
class ReferenceLogitRowV1:
    semantic_prefix: tuple[bytes, ...]
    semantic_tokens: tuple[bytes, ...]
    stop_available: bool
    semantic_logits: tuple[float, ...]
    stop_logit: float | None

    def __post_init__(self) -> None:
        _require_tokens(self.semantic_prefix, "semantic_prefix")
        tokens = _require_sorted_unique_tokens(self.semantic_tokens, "semantic_tokens")
        stop_available = _require_bool(self.stop_available, "stop_available")
        logits = _require_float_tuple(self.semantic_logits, "semantic_logits")
        if len(tokens) != len(logits):
            _fail("semantic logits must match semantic tokens")
        if stop_available and tokens:
            _require_finite_float(self.stop_logit, "stop_logit")
        elif self.stop_logit is not None:
            _fail("stop_logit must be None for a model-free or unavailable STOP")
        if not tokens and not stop_available:
            _fail("a logit domain cannot be empty")


@dataclass(frozen=True, slots=True)
class NormalizedRaggedDomainV1:
    semantic_log_probabilities: tuple[float, ...]
    semantic_probabilities: tuple[float, ...]
    stop_log_probability: float | None
    stop_probability: float | None
    forced_stop: bool

    def __post_init__(self) -> None:
        log_probabilities = _require_tuple(
            self.semantic_log_probabilities,
            "semantic_log_probabilities",
        )
        probabilities = _require_probability_tuple(
            self.semantic_probabilities,
            "semantic_probabilities",
        )
        if len(log_probabilities) != len(probabilities):
            _fail("semantic log probabilities and probabilities must match")
        for position, value in enumerate(log_probabilities):
            _require_log_probability(value, f"semantic_log_probabilities[{position}]")
        forced_stop = _require_bool(self.forced_stop, "forced_stop")
        if self.stop_probability is None or self.stop_log_probability is None:
            if self.stop_probability is not None or self.stop_log_probability is not None:
                _fail("STOP log probability and probability must appear together")
            stop_probability = 0.0
        else:
            stop_probability = _require_unit_float(self.stop_probability, "stop_probability")
            _require_log_probability(self.stop_log_probability, "stop_log_probability")
        if forced_stop:
            if probabilities or self.stop_probability != 1.0 or self.stop_log_probability != 0.0:
                _fail("forced STOP must be the sole model-free outcome")
        elif not probabilities and self.stop_probability is None:
            _fail("a normalized domain cannot be empty")
        if not _is_unit_mass(math.fsum((*probabilities, stop_probability))):
            _fail("normalized probabilities must sum to one")


@dataclass(frozen=True, slots=True)
class ReferenceGradientRowV1:
    semantic_prefix: tuple[bytes, ...]
    semantic_tokens: tuple[bytes, ...]
    stop_available: bool
    semantic_gradients: tuple[float, ...]
    stop_gradient: float | None

    def __post_init__(self) -> None:
        _require_tokens(self.semantic_prefix, "semantic_prefix")
        tokens = _require_sorted_unique_tokens(self.semantic_tokens, "semantic_tokens")
        gradients = _require_float_tuple(self.semantic_gradients, "semantic_gradients")
        stop_available = _require_bool(self.stop_available, "stop_available")
        if len(tokens) != len(gradients):
            _fail("semantic gradients must match semantic tokens")
        if stop_available and tokens:
            _require_finite_float(self.stop_gradient, "stop_gradient")
        elif self.stop_gradient is not None:
            _fail("stop_gradient must be None for a model-free or unavailable STOP")


@dataclass(frozen=True, slots=True)
class ReferenceLossRowV1:
    semantic_prefix: tuple[bytes, ...]
    semantic_tokens: tuple[bytes, ...]
    stop_available: bool
    semantic_probabilities: tuple[float, ...]
    stop_probability: float | None
    reach_mass: float
    cross_entropy: float
    reach_weighted_loss: float
    gradient: ReferenceGradientRowV1
    example_gradient: ReferenceGradientRowV1

    def __post_init__(self) -> None:
        prefix = _require_tokens(self.semantic_prefix, "semantic_prefix")
        tokens = _require_sorted_unique_tokens(self.semantic_tokens, "semantic_tokens")
        stop_available = _require_bool(self.stop_available, "stop_available")
        probabilities = _require_probability_tuple(
            self.semantic_probabilities,
            "semantic_probabilities",
        )
        if len(tokens) != len(probabilities):
            _fail("semantic probabilities must match semantic tokens")
        if stop_available:
            if not tokens:
                _fail("a forced sole STOP must not create a loss row")
            stop_probability = _require_unit_float(self.stop_probability, "stop_probability")
        else:
            if self.stop_probability is not None:
                _fail("stop_probability must be None when STOP is unavailable")
            stop_probability = 0.0
        if not _is_unit_mass(math.fsum((*probabilities, stop_probability))):
            _fail("row probabilities must sum to one")
        reach_mass = _require_finite_float(self.reach_mass, "reach_mass")
        if reach_mass <= 0.0 or reach_mass > 1.0 + _MASS_ABS_TOLERANCE_V1:
            _fail("reach_mass must be in (0, 1]")
        cross_entropy = _require_finite_float(self.cross_entropy, "cross_entropy")
        reach_weighted_loss = _require_finite_float(
            self.reach_weighted_loss,
            "reach_weighted_loss",
        )
        if cross_entropy < 0.0 or reach_weighted_loss < 0.0:
            _fail("losses cannot be negative")
        if not _same_mass(reach_weighted_loss, reach_mass * cross_entropy):
            _fail("reach_weighted_loss must equal reach_mass times cross_entropy")
        if type(self.gradient) is not ReferenceGradientRowV1 or type(
            self.example_gradient
        ) is not ReferenceGradientRowV1:
            _fail("gradient rows must have exact types")
        for gradient in (self.gradient, self.example_gradient):
            if (
                gradient.semantic_prefix != prefix
                or gradient.semantic_tokens != tokens
                or gradient.stop_available != stop_available
            ):
                _fail("gradient domains must match the loss row")
        for raw, weighted in zip(
            self.gradient.semantic_gradients,
            self.example_gradient.semantic_gradients,
            strict=True,
        ):
            if not _same_mass(weighted, reach_mass * raw):
                _fail("example gradient must include reach exactly once")
        if self.gradient.stop_gradient is None:
            if self.example_gradient.stop_gradient is not None:
                _fail("example STOP gradient domain is inconsistent")
        elif not _same_mass(
            self.example_gradient.stop_gradient,
            reach_mass * self.gradient.stop_gradient,
        ):
            _fail("example STOP gradient must include reach exactly once")


@dataclass(frozen=True, slots=True)
class ReferenceLossExampleInputV1:
    targets: PushedForwardTargetsV1
    logit_rows: tuple[ReferenceLogitRowV1, ...]

    def __post_init__(self) -> None:
        if type(self.targets) is not PushedForwardTargetsV1:
            _fail("targets must be an exact PushedForwardTargetsV1")
        rows = _require_tuple(self.logit_rows, "logit_rows")
        if len(rows) > MAX_REFERENCE_ROWS_V1:
            _fail("too many logit rows in one example")
        for row in rows:
            if type(row) is not ReferenceLogitRowV1:
                _fail("logit rows must have exact types")


@dataclass(frozen=True, slots=True)
class ReferenceExampleLossV1:
    rows: tuple[ReferenceLossRowV1, ...]
    example_loss: float
    quality_weight: float
    weighted_loss: float
    trainable: bool

    def __post_init__(self) -> None:
        rows = _require_tuple(self.rows, "rows")
        if len(rows) > MAX_REFERENCE_ROWS_V1:
            _fail("too many loss rows in one example")
        for row in rows:
            if type(row) is not ReferenceLossRowV1:
                _fail("loss rows must have exact types")
        example_loss = _require_finite_float(self.example_loss, "example_loss")
        quality = _require_unit_float(self.quality_weight, "quality_weight")
        weighted_loss = _require_finite_float(self.weighted_loss, "weighted_loss")
        trainable = _require_bool(self.trainable, "trainable")
        if example_loss < 0.0 or weighted_loss < 0.0:
            _fail("example losses cannot be negative")
        if trainable != bool(rows):
            _fail("an example is trainable exactly when it has loss rows")
        if not _same_mass(
            example_loss,
            math.fsum(row.reach_weighted_loss for row in rows),
        ):
            _fail("example loss must be the reach-weighted row sum")
        if not _same_mass(weighted_loss, quality * example_loss):
            _fail("quality_weight must be applied once to the example loss")


@dataclass(frozen=True, slots=True)
class ReferenceExampleGradientV1:
    rows: tuple[ReferenceGradientRowV1, ...]

    def __post_init__(self) -> None:
        rows = _require_tuple(self.rows, "rows")
        if len(rows) > MAX_REFERENCE_ROWS_V1:
            _fail("too many gradient rows in one example")
        for row in rows:
            if type(row) is not ReferenceGradientRowV1:
                _fail("gradient rows must have exact types")


@dataclass(frozen=True, slots=True)
class ReferenceLossResultV1:
    examples: tuple[ReferenceExampleLossV1, ...]
    weighted_loss_sum: float
    weight_sum: float
    mean_loss: float
    mean_gradients: tuple[ReferenceExampleGradientV1, ...]

    def __post_init__(self) -> None:
        examples = _require_tuple(self.examples, "examples")
        gradients = _require_tuple(self.mean_gradients, "mean_gradients")
        if len(examples) > MAX_REFERENCE_ROWS_V1 or len(examples) != len(gradients):
            _fail("loss result example count is invalid")
        for example, gradient in zip(examples, gradients, strict=True):
            if type(example) is not ReferenceExampleLossV1:
                _fail("example losses must have exact types")
            if type(gradient) is not ReferenceExampleGradientV1:
                _fail("mean example gradients must have exact types")
            if len(example.rows) != len(gradient.rows):
                _fail("mean gradient rows must match their example")
            for row, gradient_row in zip(example.rows, gradient.rows, strict=True):
                if (
                    gradient_row.semantic_prefix != row.semantic_prefix
                    or gradient_row.semantic_tokens != row.semantic_tokens
                    or gradient_row.stop_available != row.stop_available
                ):
                    _fail("mean gradient domains must match their loss rows")
        weighted_loss_sum = _require_finite_float(
            self.weighted_loss_sum,
            "weighted_loss_sum",
        )
        weight_sum = _require_finite_float(self.weight_sum, "weight_sum")
        mean_loss = _require_finite_float(self.mean_loss, "mean_loss")
        if weighted_loss_sum < 0.0 or weight_sum < 0.0 or mean_loss < 0.0:
            _fail("loss aggregates cannot be negative")
        expected_weight = math.fsum(
            example.quality_weight for example in examples if example.trainable
        )
        expected_weighted_loss = math.fsum(
            example.weighted_loss for example in examples if example.trainable
        )
        expected_mean = (
            expected_weighted_loss / expected_weight if expected_weight > 0.0 else 0.0
        )
        if not _same_mass(weight_sum, expected_weight):
            _fail("weight_sum must count each trainable example once")
        if not _same_mass(weighted_loss_sum, expected_weighted_loss):
            _fail("weighted_loss_sum must contain one weighted term per example")
        if not _same_mass(mean_loss, expected_mean):
            _fail("mean_loss must use the example-quality denominator")


def normalize_ragged_logits_v1(
    semantic_logits: tuple[float, ...],
    *,
    stop_available: bool,
    stop_logit: float | None,
) -> NormalizedRaggedDomainV1:
    logits = _require_float_tuple(semantic_logits, "semantic_logits")
    stop = _require_bool(stop_available, "stop_available")
    if not logits:
        if stop and stop_logit is None:
            return NormalizedRaggedDomainV1(
                semantic_log_probabilities=(),
                semantic_probabilities=(),
                stop_log_probability=0.0,
                stop_probability=1.0,
                forced_stop=True,
            )
        _fail("an empty semantic domain requires a model-free sole STOP")
    if stop:
        stop_value = _require_finite_float(stop_logit, "stop_logit")
        complete_logits = (*logits, stop_value)
    else:
        if stop_logit is not None:
            _fail("stop_logit must be None when STOP is unavailable")
        complete_logits = logits
    maximum = max(complete_logits)
    shifted = tuple(value - maximum for value in complete_logits)
    exponential = tuple(math.exp(value) for value in shifted)
    denominator = math.fsum(exponential)
    log_denominator = math.log(denominator)
    log_probabilities = tuple(value - log_denominator for value in shifted)
    probabilities = tuple(value / denominator for value in exponential)
    semantic_count = len(logits)
    return NormalizedRaggedDomainV1(
        semantic_log_probabilities=log_probabilities[:semantic_count],
        semantic_probabilities=probabilities[:semantic_count],
        stop_log_probability=log_probabilities[-1] if stop else None,
        stop_probability=probabilities[-1] if stop else None,
        forced_stop=False,
    )


def _require_space(space: object) -> SemanticSelectionSpaceV1:
    if type(space) is not SemanticSelectionSpaceV1:
        _fail("space must be an exact SemanticSelectionSpaceV1")
    return space


def enumerate_complete_semantic_selections_v1(
    space: SemanticSelectionSpaceV1,
) -> tuple[tuple[bytes, ...], ...]:
    checked_space = _require_space(space)
    tokens = tuple(semantic_class.token for semantic_class in checked_space.classes)
    capacities = tuple(semantic_class.alias_count for semantic_class in checked_space.classes)
    counts = [0 for _ in tokens]
    selections: list[tuple[bytes, ...]] = []

    def append_complete(prefix: tuple[bytes, ...]) -> None:
        selections.append(prefix)
        if len(selections) > MAX_REFERENCE_COMPLETE_ACTIONS_V1:
            _fail("complete semantic selection tree exceeds the reference bound")

    def visit_ordered(prefix: tuple[bytes, ...]) -> None:
        if len(prefix) >= checked_space.minimum:
            append_complete(prefix)
        if len(prefix) == checked_space.maximum:
            return
        for position, token in enumerate(tokens):
            if counts[position] >= capacities[position]:
                continue
            counts[position] += 1
            visit_ordered((*prefix, token))
            counts[position] -= 1

    def visit_unordered(prefix: tuple[bytes, ...], start: int) -> None:
        if len(prefix) >= checked_space.minimum:
            append_complete(prefix)
        if len(prefix) == checked_space.maximum:
            return
        for position in range(start, len(tokens)):
            if counts[position] >= capacities[position]:
                continue
            counts[position] += 1
            visit_unordered((*prefix, tokens[position]), position)
            counts[position] -= 1

    if checked_space.order_semantics == "ordered":
        visit_ordered(())
    else:
        visit_unordered((), 0)
    selections.sort()
    return tuple(selections)


def _capacity_by_token(space: SemanticSelectionSpaceV1) -> dict[bytes, int]:
    return {semantic_class.token: semantic_class.alias_count for semantic_class in space.classes}


def _canonical_complete_selection(
    space: SemanticSelectionSpaceV1,
    selection: tuple[bytes, ...],
) -> tuple[bytes, ...]:
    checked = _require_tokens(selection, "semantic_selection")
    if space.order_semantics == "unordered":
        checked = tuple(sorted(checked))
    if len(checked) < space.minimum or len(checked) > space.maximum:
        _fail("complete semantic selection violates min/max")
    capacities = _capacity_by_token(space)
    counts: dict[bytes, int] = {}
    for token in checked:
        if token not in capacities:
            _fail("complete semantic selection contains an unknown token")
        counts[token] = counts.get(token, 0) + 1
        if counts[token] > capacities[token]:
            _fail("complete semantic selection exceeds alias capacity")
    return checked


def _require_canonical_prefix(
    space: SemanticSelectionSpaceV1,
    prefix: tuple[bytes, ...],
) -> tuple[bytes, ...]:
    checked = _require_tokens(prefix, "semantic_prefix")
    if len(checked) > space.maximum:
        _fail("semantic prefix exceeds maximum length")
    if space.order_semantics == "unordered" and checked != tuple(sorted(checked)):
        _fail("unordered semantic prefixes must be canonical")
    capacities = _capacity_by_token(space)
    counts: dict[bytes, int] = {}
    for token in checked:
        if token not in capacities:
            _fail("semantic prefix contains an unknown token")
        counts[token] = counts.get(token, 0) + 1
        if counts[token] > capacities[token]:
            _fail("semantic prefix exceeds alias capacity")
    return checked


def _legal_next_tokens(
    space: SemanticSelectionSpaceV1,
    prefix: tuple[bytes, ...],
) -> tuple[bytes, ...]:
    if len(prefix) >= space.maximum:
        return ()
    counts = {token: prefix.count(token) for token in set(prefix)}
    lower_bound = prefix[-1] if prefix and space.order_semantics == "unordered" else None
    legal: list[bytes] = []
    for position, semantic_class in enumerate(space.classes):
        token = semantic_class.token
        if lower_bound is not None and token < lower_bound:
            continue
        if counts.get(token, 0) < semantic_class.alias_count:
            if space.order_semantics == "unordered":
                new_length = len(prefix) + 1
                required_after = max(0, space.minimum - new_length)
                remaining_at_or_above = semantic_class.alias_count - (
                    counts.get(token, 0) + 1
                )
                remaining_at_or_above += sum(
                    later.alias_count - counts.get(later.token, 0)
                    for later in space.classes[position + 1 :]
                )
                if remaining_at_or_above < required_after:
                    continue
            legal.append(token)
    return tuple(legal)


def push_forward_complete_action_mass_v1(
    space: SemanticSelectionSpaceV1,
    complete_action_masses: tuple[CompleteActionMassRowV1, ...],
    *,
    quality_weight: float,
) -> PushedForwardTargetsV1:
    checked_space = _require_space(space)
    rows = _require_tuple(complete_action_masses, "complete_action_masses")
    if len(rows) > MAX_REFERENCE_COMPLETE_ACTIONS_V1:
        _fail("too many complete action mass rows")
    quality = _require_unit_float(quality_weight, "quality_weight")
    legal_selections = enumerate_complete_semantic_selections_v1(checked_space)
    legal_set = set(legal_selections)
    grouped: dict[tuple[bytes, ...], list[float]] = {
        selection: [] for selection in legal_selections
    }
    all_input_masses: list[float] = []
    for position, row in enumerate(rows):
        if type(row) is not CompleteActionMassRowV1:
            _fail(f"complete_action_masses[{position}] has the wrong type")
        selection = _canonical_complete_selection(checked_space, row.semantic_selection)
        if selection not in legal_set:
            _fail("complete semantic selection is not legal")
        grouped[selection].append(row.mass)
        all_input_masses.append(row.mass)
    input_total = _ordered_fsum(all_input_masses)
    if not _is_unit_mass(input_total):
        _fail("complete action mass must sum to one")

    complete = tuple(
        SemanticCompleteMassV1(
            selection,
            _ordered_fsum(grouped[selection]) / input_total,
        )
        for selection in legal_selections
    )
    reach_parts: dict[tuple[bytes, ...], list[float]] = {}
    next_parts: dict[tuple[bytes, ...], dict[bytes, list[float]]] = {}
    stop_parts: dict[tuple[bytes, ...], list[float]] = {}
    for row in complete:
        if row.mass == 0.0:
            continue
        for offset in range(len(row.semantic_selection) + 1):
            prefix = row.semantic_selection[:offset]
            reach_parts.setdefault(prefix, []).append(row.mass)
            if offset == len(row.semantic_selection):
                stop_parts.setdefault(prefix, []).append(row.mass)
            else:
                token = row.semantic_selection[offset]
                next_parts.setdefault(prefix, {}).setdefault(token, []).append(row.mass)

    targets: list[ConditionalTargetRowV1] = []
    for prefix in sorted(reach_parts):
        legal_tokens = _legal_next_tokens(checked_space, prefix)
        stop_available = len(prefix) >= checked_space.minimum
        if not legal_tokens and stop_available:
            continue
        token_parts = next_parts.get(prefix, {})
        # Reach is the sum of this row's own absolute terms, not a separately
        # accumulated total.  Normalizing by the same terms that form the
        # numerators keeps the conditional distribution and its reach mass
        # rounded together, so this oracle stays bit-identical to the dataset
        # push-forward instead of differing by one summation order.
        absolute_semantic = tuple(
            _ordered_fsum(token_parts.get(token, [])) for token in legal_tokens
        )
        absolute_stop = (
            _ordered_fsum(stop_parts.get(prefix, [])) if stop_available else None
        )
        reach = _ordered_fsum(
            absolute_semantic
            if absolute_stop is None
            else absolute_semantic + (absolute_stop,)
        )
        if reach <= 0.0:
            continue
        semantic_masses = tuple(value / reach for value in absolute_semantic)
        stop_mass = None if absolute_stop is None else absolute_stop / reach
        targets.append(
            ConditionalTargetRowV1(
                semantic_prefix=prefix,
                semantic_tokens=legal_tokens,
                stop_available=stop_available,
                semantic_target_masses=semantic_masses,
                stop_target_mass=stop_mass,
                reach_mass=reach,
            )
        )
    return PushedForwardTargetsV1(
        space=checked_space,
        complete_semantic_masses=complete,
        conditional_targets=tuple(targets),
        quality_weight=quality,
    )


def _reconstruct_complete_semantic_mass_and_reaches_v1(
    space: SemanticSelectionSpaceV1,
    conditional_targets: tuple[ConditionalTargetRowV1, ...],
) -> tuple[
    tuple[SemanticCompleteMassV1, ...],
    dict[tuple[bytes, ...], float],
]:
    checked_space = _require_space(space)
    rows = _require_tuple(conditional_targets, "conditional_targets")
    if len(rows) > MAX_REFERENCE_ROWS_V1:
        _fail("too many conditional target rows")
    by_prefix: dict[tuple[bytes, ...], ConditionalTargetRowV1] = {}
    for position, row in enumerate(rows):
        if type(row) is not ConditionalTargetRowV1:
            _fail(f"conditional_targets[{position}] has the wrong type")
        prefix = _require_canonical_prefix(checked_space, row.semantic_prefix)
        if prefix in by_prefix:
            _fail("conditional target prefixes must be unique")
        legal_tokens = _legal_next_tokens(checked_space, prefix)
        stop_available = len(prefix) >= checked_space.minimum
        if row.semantic_tokens != legal_tokens or row.stop_available != stop_available:
            _fail("conditional target row does not cover its full legal domain")
        if not legal_tokens and stop_available:
            _fail("a sole forced STOP must not have a conditional row")
        by_prefix[prefix] = row

    selections = enumerate_complete_semantic_selections_v1(checked_space)
    reconstructed: list[SemanticCompleteMassV1] = []
    used_prefixes: set[tuple[bytes, ...]] = set()
    authoritative_reaches: dict[tuple[bytes, ...], float] = {}
    for selection in selections:
        probability = 1.0
        for offset in range(len(selection) + 1):
            if probability == 0.0:
                break
            prefix = selection[:offset]
            legal_tokens = _legal_next_tokens(checked_space, prefix)
            stop_available = len(prefix) >= checked_space.minimum
            if not legal_tokens and stop_available:
                if offset != len(selection):
                    _fail("forced STOP occurred before a complete selection ended")
                continue
            row = by_prefix.get(prefix)
            if row is None:
                _fail("a positive-reach conditional target row is missing")
            if not _same_mass(row.reach_mass, probability):
                _fail("conditional reach_mass is not attested by its parent path")
            used_prefixes.add(prefix)
            authoritative_reaches[prefix] = probability
            if offset == len(selection):
                if not row.stop_available or row.stop_target_mass is None:
                    _fail("a complete selection requires an available STOP")
                probability *= row.stop_target_mass
            else:
                next_token = selection[offset]
                try:
                    token_position = row.semantic_tokens.index(next_token)
                except ValueError:
                    _fail("a complete selection uses a missing semantic class")
                probability *= row.semantic_target_masses[token_position]
        reconstructed.append(SemanticCompleteMassV1(selection, probability))
    if set(by_prefix) != used_prefixes:
        _fail("zero-reach conditional prefixes must be omitted")
    if not _is_unit_mass(math.fsum(row.mass for row in reconstructed)):
        _fail("conditional targets do not reconstruct unit complete mass")
    return tuple(reconstructed), authoritative_reaches


def reconstruct_complete_semantic_mass_v1(
    space: SemanticSelectionSpaceV1,
    conditional_targets: tuple[ConditionalTargetRowV1, ...],
) -> tuple[SemanticCompleteMassV1, ...]:
    reconstructed, _ = _reconstruct_complete_semantic_mass_and_reaches_v1(
        space,
        conditional_targets,
    )
    return reconstructed


def _zero_gradient(row: ReferenceLossRowV1) -> ReferenceGradientRowV1:
    return ReferenceGradientRowV1(
        semantic_prefix=row.semantic_prefix,
        semantic_tokens=row.semantic_tokens,
        stop_available=row.stop_available,
        semantic_gradients=tuple(0.0 for _ in row.semantic_tokens),
        stop_gradient=0.0 if row.stop_available else None,
    )


def _evaluate_loss_row(
    target: ConditionalTargetRowV1,
    logit: ReferenceLogitRowV1,
    *,
    reach_mass: float,
) -> ReferenceLossRowV1:
    if (
        target.semantic_prefix != logit.semantic_prefix
        or target.semantic_tokens != logit.semantic_tokens
        or target.stop_available != logit.stop_available
    ):
        _fail("target and logit row domains must match exactly")
    normalized = normalize_ragged_logits_v1(
        logit.semantic_logits,
        stop_available=logit.stop_available,
        stop_logit=logit.stop_logit,
    )
    if normalized.forced_stop:
        _fail("a sole forced STOP must not create a loss row")
    loss_terms = [
        -mass * log_probability
        for mass, log_probability in zip(
            target.semantic_target_masses,
            normalized.semantic_log_probabilities,
            strict=True,
        )
        if mass > 0.0
    ]
    if target.stop_available and target.stop_target_mass is not None:
        if target.stop_target_mass > 0.0:
            if normalized.stop_log_probability is None:
                _fail("normalized STOP log probability is missing")
            loss_terms.append(-target.stop_target_mass * normalized.stop_log_probability)
    cross_entropy = math.fsum(loss_terms)
    if not math.isfinite(cross_entropy):
        _fail("cross entropy is outside the finite reference range")
    if cross_entropy < 0.0 and abs(cross_entropy) <= _MASS_ABS_TOLERANCE_V1:
        cross_entropy = 0.0
    semantic_gradients = tuple(
        probability - mass
        for probability, mass in zip(
            normalized.semantic_probabilities,
            target.semantic_target_masses,
            strict=True,
        )
    )
    stop_gradient = (
        normalized.stop_probability - target.stop_target_mass
        if target.stop_available
        and normalized.stop_probability is not None
        and target.stop_target_mass is not None
        else None
    )
    gradient = ReferenceGradientRowV1(
        semantic_prefix=target.semantic_prefix,
        semantic_tokens=target.semantic_tokens,
        stop_available=target.stop_available,
        semantic_gradients=semantic_gradients,
        stop_gradient=stop_gradient,
    )
    example_gradient = ReferenceGradientRowV1(
        semantic_prefix=target.semantic_prefix,
        semantic_tokens=target.semantic_tokens,
        stop_available=target.stop_available,
        semantic_gradients=tuple(reach_mass * value for value in semantic_gradients),
        stop_gradient=(
            reach_mass * stop_gradient if stop_gradient is not None else None
        ),
    )
    return ReferenceLossRowV1(
        semantic_prefix=target.semantic_prefix,
        semantic_tokens=target.semantic_tokens,
        stop_available=target.stop_available,
        semantic_probabilities=normalized.semantic_probabilities,
        stop_probability=normalized.stop_probability,
        reach_mass=reach_mass,
        cross_entropy=cross_entropy,
        reach_weighted_loss=reach_mass * cross_entropy,
        gradient=gradient,
        example_gradient=example_gradient,
    )


def _attest_complete_targets(
    targets: PushedForwardTargetsV1,
) -> dict[tuple[bytes, ...], float]:
    reconstructed, authoritative_reaches = _reconstruct_complete_semantic_mass_and_reaches_v1(
        targets.space,
        targets.conditional_targets,
    )
    if len(reconstructed) != len(targets.complete_semantic_masses):
        _fail("complete semantic target support is inconsistent")
    for supplied, attested in zip(
        targets.complete_semantic_masses,
        reconstructed,
        strict=True,
    ):
        if supplied.semantic_selection != attested.semantic_selection or not _same_mass(
            supplied.mass,
            attested.mass,
        ):
            _fail("complete semantic masses disagree with the conditional tree")
    return authoritative_reaches


def evaluate_reference_losses_v1(
    example_inputs: tuple[ReferenceLossExampleInputV1, ...],
) -> ReferenceLossResultV1:
    inputs = _require_tuple(example_inputs, "example_inputs")
    if len(inputs) > MAX_REFERENCE_ROWS_V1:
        _fail("too many loss examples")
    total_logit_rows = 0
    total_complete_actions = 0
    for example in inputs:
        if type(example) is not ReferenceLossExampleInputV1:
            _fail("loss examples must have exact input types")
        total_logit_rows += len(example.logit_rows)
        total_complete_actions += len(example.targets.complete_semantic_masses)
        if total_logit_rows > MAX_REFERENCE_ROWS_V1:
            _fail("batch loss rows exceed the reference bound")
        if total_complete_actions > MAX_REFERENCE_COMPLETE_ACTIONS_V1:
            _fail("batch complete-action support exceeds the reference bound")

    evaluated_examples: list[ReferenceExampleLossV1] = []
    trainable_weights: list[float] = []
    weighted_losses: list[float] = []
    for example in inputs:
        targets = example.targets
        authoritative_reaches = _attest_complete_targets(targets)
        if len(targets.conditional_targets) != len(example.logit_rows):
            _fail("target and logit row counts must match within each example")
        evaluated_rows = tuple(
            _evaluate_loss_row(
                target,
                logit,
                reach_mass=authoritative_reaches[target.semantic_prefix],
            )
            for target, logit in zip(
                targets.conditional_targets,
                example.logit_rows,
                strict=True,
            )
        )
        example_loss = math.fsum(row.reach_weighted_loss for row in evaluated_rows)
        weighted_loss = targets.quality_weight * example_loss
        trainable = bool(evaluated_rows)
        evaluated_examples.append(
            ReferenceExampleLossV1(
                rows=evaluated_rows,
                example_loss=example_loss,
                quality_weight=targets.quality_weight,
                weighted_loss=weighted_loss,
                trainable=trainable,
            )
        )
        if trainable:
            trainable_weights.append(targets.quality_weight)
            weighted_losses.append(weighted_loss)

    weight_sum = math.fsum(trainable_weights)
    weighted_loss_sum = math.fsum(weighted_losses)
    mean_loss = weighted_loss_sum / weight_sum if weight_sum > 0.0 else 0.0
    mean_gradients: list[ReferenceExampleGradientV1] = []
    for example in evaluated_examples:
        if weight_sum > 0.0 and example.trainable:
            scale = example.quality_weight / weight_sum
            rows = tuple(
                ReferenceGradientRowV1(
                    semantic_prefix=row.semantic_prefix,
                    semantic_tokens=row.semantic_tokens,
                    stop_available=row.stop_available,
                    semantic_gradients=tuple(
                        scale * value for value in row.example_gradient.semantic_gradients
                    ),
                    stop_gradient=(
                        scale * row.example_gradient.stop_gradient
                        if row.example_gradient.stop_gradient is not None
                        else None
                    ),
                )
                for row in example.rows
            )
        else:
            rows = tuple(_zero_gradient(row) for row in example.rows)
        mean_gradients.append(ReferenceExampleGradientV1(rows=rows))
    return ReferenceLossResultV1(
        examples=tuple(evaluated_examples),
        weighted_loss_sum=weighted_loss_sum,
        weight_sum=weight_sum,
        mean_loss=mean_loss,
        mean_gradients=tuple(mean_gradients),
    )
