"""Registry-driven opponent instances for the actor pool.

正典 §13 (medal curriculum と opponent league) と §7 (`OpponentPoolManifest`)
に対応する。正典が要求する opponent instance は「deck hash、policy
implementation / hash、policy type、source rank band、local strength band、
sampling weight、seat protocol、scenario seed namespace、version、asset 利用
境界」を持つ独立した実体であり、単一の rule agent ではない。

この module が存在する理由は、以前の実装が opponent を
``frozenset({"cabt_rule_agent_v0"})`` の閉じた enum に固定し、かつ相手デッキを
subject 自身のデッキに束縛して self-mirror しか表現できなかったことにある。
その状態では正典 §13 の strength band curriculum も §14 の promotion gate も
意味を持たない。

## fail-closed の方針

未知の opponent id、manifest に無い id、ディスク上に実体が無い id は
**例外で落とす**。以前の実装は相手デッキが見つからないと subject 自身のデッキへ
無言で fallback しており、「16 種類の相手と対戦している」という外形の裏で実際は
self-mirror を回していた。正典 §5 の「不足した runtime ID は起動時に失敗させ、
推測で補完しない」に従い、fallback は設けない。

## 非公開情報境界

正典 §9.2 に従い、この module が返す opponent identity (deck hash、policy hash、
opponent_id) は **scheduler と manifest のためだけ**にある。subject の観測、
teacher の decision feature、student の入力へ渡してはならない。
``OpponentInstanceV1`` は agent callable を作る以上のことをせず、identity を
observation へ載せる経路を持たない。

## 利用境界

pool の全 asset は ``local_eval_only`` である (各 ``SOURCE.md`` が local offline
evaluation のみに限定し、再配布と as-is 提出を禁じている)。opponent として実行
することは許されるが、提出 bundle へ混入させてはならない。``usage_boundary``
を manifest から読み、``bundle_allowed`` 以外を bundle 経路へ渡さないことは
package 側の責務とする (正典 §22)。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping


OPPONENT_POOL_SCHEMA_V1 = "meta-specialist-opponent-pool-v1"

_POOL_MANIFEST_NAME_V1 = "pool_manifest.json"
_OPPONENT_ENTRY_POINT_NAME_V1 = "main.py"
_OPPONENT_DECK_NAME_V1 = "deck.csv"
_OPPONENT_AGENT_ATTR_V1 = "agent"

# The subject's own mirror opponent.  Kept as one *registered* instance rather
# than the only possible one: a self-mirror is a legitimate curriculum entry
# (正典 §13 の self-play checkpoint population), but it must be selected
# explicitly, never reached by falling back from a missing asset.
MIRROR_OPPONENT_ID_V1 = "cabt_rule_agent_v0"


class OpponentPoolV1Error(ValueError):
    """Raised when an opponent instance cannot be resolved or verified."""


@dataclass(frozen=True, slots=True)
class OpponentInstanceV1:
    """One resolvable opponent: a deck plus the policy that pilots it.

    ``policy_path`` empty means the engine's own built-in ``"rule"`` agent
    pilots ``deck_csv_path`` -- that is the ``cabt_rule_agent_v0`` mirror
    instance and nothing else.
    """

    opponent_id: str
    deck_csv_path: str
    policy_path: str
    canonical_deck_hash: str
    policy_hash: str
    usage_boundary: str
    source: str
    mean_decision_ms: float | None

    def __post_init__(self) -> None:
        if not self.opponent_id:
            raise OpponentPoolV1Error("opponent_id must be non-empty")
        if not self.usage_boundary:
            raise OpponentPoolV1Error(f"{self.opponent_id}: usage_boundary must be non-empty")

    @property
    def is_mirror(self) -> bool:
        return self.opponent_id == MIRROR_OPPONENT_ID_V1

    @property
    def policy_type(self) -> str:
        """正典 §13 の opponent instance が要求する policy type."""
        return "cabt_rule_agent" if self.is_mirror else "external_submission_agent"


def default_pool_root_v1(repo_root: Path) -> Path:
    return Path(repo_root) / "opponents"


def _require_legal_deck_v1(opponent_id: str, deck_path: Path) -> None:
    """Reject an opponent whose deck cannot be a legal 60-card list.

    Only the **structural** rule is checked here: exactly 60 integer card IDs,
    matching ``decks.py``'s own qualification (``len(card_ids) != 60``).  That
    is what actually bit -- a pooled agent shipped a 61-card deck, and every
    game against it faulted in the engine's deck reader.

    Deliberately *not* re-implemented here: the copy-count rule.  A first
    attempt at "at most 4 copies" mis-flagged 47 of 65 decks because basic
    energy (Card IDs 1-8) is exempt from that cap.  A hand-rolled rule that is
    wrong in either direction is worse than none -- too strict silently shrinks
    the league, too lax admits a deck that faults.  The engine remains the
    oracle for the full rule set and enforces it at match time; a violation
    there surfaces as a per-game fault, which the collection runners already
    count rather than swallow.

    ``main`` is not imported: ``test_meta_specialist_production_modules_do_not_
    import_top_level_main`` forbids it, because in a submission archive the
    root ``main`` is the submission's own entry point, not the engine's.
    """
    try:
        text = deck_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpponentPoolV1Error(f"{opponent_id}: could not read {deck_path}: {exc}") from exc
    card_ids = []
    for token in text.split():
        try:
            card_ids.append(int(token))
        except ValueError as exc:
            raise OpponentPoolV1Error(
                f"{opponent_id}: {deck_path} holds a non-integer card ID {token!r}"
            ) from exc
    if len(card_ids) != 60:
        raise OpponentPoolV1Error(
            f"{opponent_id}: {deck_path} must contain exactly 60 cards, got {len(card_ids)}"
        )


def load_opponent_pool_v1(pool_root: Path | str) -> Mapping[str, OpponentInstanceV1]:
    """Read ``opponents/pool_manifest.json`` into verified instances.

    Fail-closed: a manifest entry whose ``main.py``/``deck.csv`` is missing, or
    whose on-disk ``main.py`` bytes do not match the recorded ``policy_hash``,
    raises rather than being silently skipped.  A silently skipped opponent
    would shrink the league without any signal, which is the failure mode this
    module exists to prevent.
    """
    root = Path(pool_root)
    manifest_path = root / _POOL_MANIFEST_NAME_V1
    if not manifest_path.is_file():
        raise OpponentPoolV1Error(
            f"opponent pool manifest is missing: {manifest_path}. The actor pool "
            "requires an explicit registry; it does not scan directories."
        )
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OpponentPoolV1Error(f"{manifest_path} is not valid JSON: {exc}") from exc

    rows = raw if isinstance(raw, list) else raw.get("opponents", raw)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list) or not rows:
        raise OpponentPoolV1Error(f"{manifest_path} contains no opponent entries")

    instances: dict[str, OpponentInstanceV1] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise OpponentPoolV1Error(f"{manifest_path}: every entry must be a JSON object")
        opponent_id = row.get("id", "")
        if not opponent_id:
            raise OpponentPoolV1Error(f"{manifest_path}: an entry is missing its 'id'")
        if opponent_id in instances:
            raise OpponentPoolV1Error(f"{manifest_path}: duplicate opponent id {opponent_id!r}")

        deck_path = root / opponent_id / _OPPONENT_DECK_NAME_V1
        policy_path = root / opponent_id / _OPPONENT_ENTRY_POINT_NAME_V1
        if not deck_path.is_file():
            raise OpponentPoolV1Error(f"{opponent_id}: deck.csv is missing at {deck_path}")
        if not policy_path.is_file():
            raise OpponentPoolV1Error(
                f"{opponent_id}: main.py is missing at {policy_path}. An opponent "
                "without a policy cannot be played; the actor pool will not "
                "substitute the built-in rule agent for it."
            )

        recorded_policy_hash = row.get("policy_hash", "")
        on_disk_policy_hash = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        if recorded_policy_hash and recorded_policy_hash != on_disk_policy_hash:
            raise OpponentPoolV1Error(
                f"{opponent_id}: policy_hash in the manifest ({recorded_policy_hash}) "
                f"does not match the bytes at {policy_path} ({on_disk_policy_hash})"
            )

        _require_legal_deck_v1(opponent_id, deck_path)

        mean_ms = row.get("mean_decision_ms")
        instances[opponent_id] = OpponentInstanceV1(
            opponent_id=opponent_id,
            deck_csv_path=str(deck_path),
            policy_path=str(policy_path),
            canonical_deck_hash=row.get("canonical_deck_hash", ""),
            policy_hash=on_disk_policy_hash,
            usage_boundary=row.get("usage_boundary", ""),
            source=row.get("source", ""),
            mean_decision_ms=float(mean_ms) if isinstance(mean_ms, (int, float)) else None,
        )
    return instances


def resolve_opponent_v1(
    pool: Mapping[str, OpponentInstanceV1],
    opponent_id: str,
    *,
    subject_deck_csv_path: str,
) -> OpponentInstanceV1:
    """Resolve one opponent id, or fail.

    ``MIRROR_OPPONENT_ID_V1`` is synthesised against the subject's own deck so
    a self-mirror stays expressible -- but only when it is *asked for* by name.
    """
    if opponent_id == MIRROR_OPPONENT_ID_V1:
        return OpponentInstanceV1(
            opponent_id=MIRROR_OPPONENT_ID_V1,
            deck_csv_path=str(subject_deck_csv_path),
            policy_path="",
            canonical_deck_hash="",
            policy_hash="",
            usage_boundary="internal_mirror",
            source="engine_builtin",
            mean_decision_ms=None,
        )
    instance = pool.get(opponent_id)
    if instance is None:
        raise OpponentPoolV1Error(
            f"unknown opponent_id {opponent_id!r}. Registered ids: "
            f"{sorted(pool)[:8]}{'...' if len(pool) > 8 else ''}. The actor pool "
            "does not fall back to a self-mirror for an unregistered opponent."
        )
    return instance


# --------------------------------------------------------------------------
# Loading an external submission agent.
#
# Every opponent's ``main.py`` is a Kaggle submission entry point exposing a
# module-level ``agent(obs_dict) -> list[int]`` (``main.Agent`` is
# ``Callable[[dict], list[int]]``).  Each is loaded under a unique module name
# derived from its content hash so that 66 modules all literally named "main"
# cannot collide with one another -- or, more dangerously, with the
# repository's own ``main`` module, which ``scripts.test_sim`` imports for the
# engine's deck reader and built-in agents.
# --------------------------------------------------------------------------


def _opponent_module_name_v1(instance: OpponentInstanceV1) -> str:
    return f"mage_ptcg_meta_specialist_opponent_{instance.opponent_id}_{instance.policy_hash[:12]}"


# Submission agents import the engine surface as a top-level ``cg`` package and,
# for the scraped ``meta_*`` decks, their shared deck-agnostic pilot as
# ``agents.generic_agent``.  Neither is importable from this package's own
# location, so both roots are put on ``sys.path`` for the duration of the
# import and removed afterwards.  See ``cg/PROVENANCE.md`` and
# ``vendor_opponent_pilots/PROVENANCE.md`` for where each comes from and why a
# single shared copy (rather than per-opponent vendored copies) is required:
# every opponent must run against the same engine bytes the match itself uses.
# The vendored pilots come *first*: the repository has its own top-level
# ``agents`` package (the Rule Agent v0 helpers that ``main.py`` imports), and
# the pooled ``meta_*`` submissions need the ozawa-branch ``agents.generic_agent``
# instead.  With the repository root first, whichever of the two happened to be
# imported earlier would win -- an order-dependent resolution that silently
# changes which pilot plays.  See `vendor_opponent_pilots/PROVENANCE.md`.
_OPPONENT_IMPORT_ROOT_NAMES_V1 = ("vendor_opponent_pilots", "")

# Top-level module names that an opponent import may bind and that must be
# restored afterwards.  ``main`` and ``__main__`` protect the engine's own
# entry point; ``agents`` protects the repository's Rule Agent v0 package from
# being left replaced by a vendored pilot (and vice versa); ``cg`` isolates the
# subject candidate's partial engine package from an opponent's shared engine
# import (for example ``from cg.game import battle_start``).
_PRESERVED_MODULE_PREFIXES_V1 = ("main", "__main__", "agents", "cg")

# Importing ``cg.sim`` calls the native engine's global initializer.  Keep one
# shared-engine module graph per checkout root so a candidate's cached ``cg``
# package can be swapped out for an opponent import without reinitializing the
# native library for every opponent in the pool.
_SHARED_CG_MODULES_V1: dict[str, dict[str, Any]] = {}


def _opponent_import_roots_v1(repo_root: Path) -> list[str]:
    roots = []
    for name in _OPPONENT_IMPORT_ROOT_NAMES_V1:
        path = Path(repo_root) / name if name else Path(repo_root)
        if path.is_dir():
            roots.append(str(path))
    return roots


def _shared_engine_root_v1(policy_path: Path) -> Path:
    """Locate the checkout root that owns the shared top-level ``cg`` package.

    Historical pools are intentionally materialized below ``runs/``.  Taking
    ``policy_path.parents[2]`` therefore points at ``runs/`` rather than the
    checkout root, and a source importing ``cg.game`` fails even after module
    cache isolation.  Prefer the ancestor that also owns the vendored pilot
    (the repository's unambiguous root), then fall back to the nearest ancestor
    with ``cg/game.py`` for small isolated test checkouts.
    """
    resolved_policy = Path(policy_path).resolve()
    ancestors = tuple(resolved_policy.parents)
    for root in ancestors:
        if (root / "cg" / "game.py").is_file() and (root / "vendor_opponent_pilots").is_dir():
            return root
    for root in ancestors:
        if (root / "cg" / "game.py").is_file():
            return root
    # Preserve the historical failure mode when a caller supplies an isolated
    # policy path with no shared engine at all; the subsequent import reports
    # the missing dependency rather than silently selecting an arbitrary root.
    return ancestors[2]


def _module_belongs_to_root_v1(module: Any, root: Path) -> bool:
    """Return whether an imported module was loaded from ``root``."""
    module_path = getattr(module, "__file__", None)
    if not module_path:
        return False
    try:
        Path(module_path).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def load_opponent_agent_callable_v1(instance: OpponentInstanceV1) -> Callable[[dict], list[int]]:
    """Import one opponent's ``main.py`` and return its ``agent`` callable.

    ``sys.modules`` is restored around the import so a loaded opponent can
    never leave the repository's own ``main``/``cg``/``agents`` modules
    replaced by a submission copy.  A stale top-level module would silently
    change which deck reader, engine surface, or built-in agents later matches
    resolve.
    """
    if instance.is_mirror:
        raise OpponentPoolV1Error(
            "the mirror instance has no external policy; the engine's built-in "
            "rule agent pilots it"
        )
    module_name = _opponent_module_name_v1(instance)
    cached = sys.modules.get(module_name)
    if cached is not None:
        return _agent_attr_v1(cached, instance)

    spec = importlib.util.spec_from_file_location(module_name, instance.policy_path)
    if spec is None or spec.loader is None:
        raise OpponentPoolV1Error(f"could not load opponent policy at {instance.policy_path}")
    module = importlib.util.module_from_spec(spec)
    repo_root = _shared_engine_root_v1(Path(instance.policy_path))
    shared_cache_key = str(repo_root)

    preserved = {
        name: module
        for name, module in list(sys.modules.items())
        if name in _PRESERVED_MODULE_PREFIXES_V1
        or name.startswith(tuple(f"{prefix}." for prefix in _PRESERVED_MODULE_PREFIXES_V1))
    }
    # Evict them so the opponent's import resolves against the roots installed
    # below rather than whatever an earlier, unrelated import already cached.
    for name in preserved:
        sys.modules.pop(name, None)
    shared_cg = _SHARED_CG_MODULES_V1.get(shared_cache_key, {})
    if shared_cg:
        sys.modules.update(shared_cg)
    # Snapshot the whole of `sys.path`, not merely the roots added here: five
    # pooled submissions insert their own directory during import, and an
    # opponent directory left on `sys.path` would silently re-point any later
    # top-level import into that submission's copy of a module.
    preserved_path = list(sys.path)
    import_roots = _opponent_import_roots_v1(repo_root)
    # Put the shared roots before *all* pre-existing entries.  A candidate
    # package is commonly already on ``sys.path`` and may itself contain a
    # partial top-level ``cg``; merely prepending roots that were not present
    # leaves that candidate path between ``vendor_opponent_pilots`` and the
    # shared checkout root.
    sys.path[:] = import_roots + [entry for entry in preserved_path if entry not in import_roots]
    sys.modules[module_name] = module
    import_succeeded = False
    try:
        spec.loader.exec_module(module)
        import_succeeded = True
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise OpponentPoolV1Error(
            f"{instance.opponent_id}: executing {instance.policy_path} failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    finally:
        sys.path[:] = preserved_path
        if import_succeeded:
            loaded_shared_cg = {
                name: loaded
                for name, loaded in sys.modules.items()
                if (name == "cg" or name.startswith("cg."))
                and _module_belongs_to_root_v1(loaded, repo_root)
            }
            if loaded_shared_cg:
                _SHARED_CG_MODULES_V1.setdefault(shared_cache_key, {}).update(loaded_shared_cg)
        # Drop whatever the opponent's import bound under these names, then put
        # the originals back.  Leaving a vendored pilot installed as `agents`
        # would silently re-point the repository's own Rule Agent v0 imports.
        for name in list(sys.modules):
            if name in _PRESERVED_MODULE_PREFIXES_V1 or name.startswith(
                tuple(f"{prefix}." for prefix in _PRESERVED_MODULE_PREFIXES_V1)
            ):
                sys.modules.pop(name, None)
        sys.modules.update(preserved)
        if not any(name == "cg" or name.startswith("cg.") for name in preserved):
            sys.modules.update(_SHARED_CG_MODULES_V1.get(shared_cache_key, {}))
    return _agent_attr_v1(module, instance)


def _agent_attr_v1(module: Any, instance: OpponentInstanceV1) -> Callable[[dict], list[int]]:
    agent = getattr(module, _OPPONENT_AGENT_ATTR_V1, None)
    if agent is None or not callable(agent):
        raise OpponentPoolV1Error(
            f"{instance.opponent_id}: {instance.policy_path} does not expose a "
            f"callable {_OPPONENT_AGENT_ATTR_V1!r} entry point"
        )
    return agent


def build_opponent_agent_factory_v1(
    instance: OpponentInstanceV1,
) -> Callable[[Any, int], Callable[[dict], list[int]]]:
    """Build the ``agent_b_factory``-shaped callable the engine expects.

    ``run_match`` calls ``factory(deck, seed)``.  A submission agent takes
    neither: it reads its own ``deck.csv`` (each pooled copy resolves that
    relative to its own directory) and carries its own RNG discipline.  Both
    arguments are therefore accepted and ignored, which is also why the
    opponent cannot observe the subject's deck or seed through this boundary.

    The loaded agent is handed back wrapped in a **plain function**, never as
    whatever callable the opponent's module happened to bind.  ``kaggle_environments``
    decides how many arguments to pass by introspecting the callable::

        if hasattr(agent, "__code__") and hasattr(agent.__code__, "co_argcount"):
            args = args[: agent.__code__.co_argcount]
        return agent(*args) if callable(agent) else agent

    A function has ``__code__`` and gets its argument list truncated to its own
    arity.  A *callable object* does not, so it is invoked with every argument
    the environment has (observation, configuration, ...), raising ``TypeError``
    inside the opponent.  The engine reports that only as ``AGENT_ERROR``.

    This is not hypothetical: the seven ``meta_*`` opponents bind
    ``agent = make_agent(deck)``, which returns a ``_GenericAgentState``
    instance.  Every game against them ended at step 1 with
    ``agent_status == ["DONE", "ERROR"]`` -- measured 10/10 raw versus 6/6
    completed once wrapped -- so 7 of the 60 rotation opponents silently
    contributed no data at all.
    """
    agent = load_opponent_agent_callable_v1(instance)

    def factory(_deck: Any, _seed: int) -> Callable[[dict], list[int]]:
        def call(observation: dict) -> list[int]:
            return agent(observation)

        return call

    return factory


def opponent_version_v1(instance: OpponentInstanceV1, *, mirror_version: str) -> str:
    """The opponent's own version identity.

    正典 §13 は opponent instance ごとの version を要求する。以前の実装は
    ``opponent_kind`` が何であれ常に repo root の ``main.py`` の hash を返して
    いたため、異なる相手の結果が同一 version として集計されうる状態だった。
    """
    return mirror_version if instance.is_mirror else instance.policy_hash
