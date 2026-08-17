"""Seal actor-visible routed-ensemble opponent meta sources.

The recipe combines two already smoke-qualified parent policies and routes one
observation to exactly one parent.  Routing is derived only from public board
state and the public selection context; no opponent hand, prize, deck, labels,
or future RNG are read.  Generated artifacts are research-only and start with
``smoke_ok=false`` so runtime promotion remains a separate gate.  The
``ACTION_LEVEL_*`` recipes extend this boundary: both parents are queried for
the same observation, and a deterministic public-state mixer chooses between
their already-produced legal index sets.  It never merges or invents indices.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import FRESH_META_SCHEMA_V1
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import (
    LOCAL_EVAL_ONLY_V1,
    scan_source_text,
)
from mage_ptcg.opponent_ingest.pipeline import normalize_deck_text
from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1


ROUTED_ENSEMBLE_META_SCHEMA_V1 = "meta-specialist-cg-routed-ensemble-v1"
ROUTED_ENSEMBLE_SOURCE_V1 = "internal_actor_visible_routed_ensemble"
RECIPE_V1 = "ACTOR_VISIBLE_ROUTED_ENSEMBLE_V1"
ACTION_LEVEL_MIX_SOURCE_V1 = "internal_actor_visible_action_level_mixer"
ACTION_LEVEL_RECIPE_V1 = "ACTOR_VISIBLE_ACTION_LEVEL_MIX_V1"
ROUTING_RECIPES_V1 = (
    "PUBLIC_HASH_V1",
    "TURN_PARITY_V1",
    "OPPONENT_BOARD_HASH_V1",
    "CONTEXT_TURN_HASH_V1",
    "OPPONENT_DAMAGE_SWITCH_V1",
    "OPPONENT_BOARD_SIZE_SWITCH_V1",
    "CONTEXT_THREAT_SWITCH_V1",
    "ACTION_LEVEL_KO_MIX_V1",
    "ACTION_LEVEL_TEMPO_MIX_V1",
    "ACTION_LEVEL_SETUP_MIX_V1",
    "ACTION_LEVEL_HASH_MIX_V1",
    "ACTION_LEVEL_CONSENSUS_MIX_V1",
    "ACTION_LEVEL_CONSENSUS_HASH_V1",
    "ACTION_LEVEL_CONSENSUS_KO_V1",
)
_ROOT = Path(__file__).resolve().parents[3]
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


class RoutedEnsembleMetaError(ValueError):
    """Raised when a routed source cannot be sealed fail-closed."""


def _derivation_recipe(routing_recipe: str) -> str:
    return ACTION_LEVEL_RECIPE_V1 if routing_recipe.startswith("ACTION_LEVEL_") else RECIPE_V1


def _source_kind(routing_recipe: str) -> str:
    return ACTION_LEVEL_MIX_SOURCE_V1 if routing_recipe.startswith("ACTION_LEVEL_") else ROUTED_ENSEMBLE_SOURCE_V1


@dataclass(frozen=True, slots=True)
class _Parent:
    key: str
    root: Path
    candidate_id: str
    entrypoint: str
    policy_sha256: str
    source_policy_sha256: str
    canonical_deck_hash: str
    deck_bytes: bytes
    source_branch: str
    source_commit: str
    source: str


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RoutedEnsembleMetaError(f"regular file required: {path}")
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: object) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RoutedEnsembleMetaError("payload is not canonical JSON") from exc


def _write_new(path: Path, data: bytes) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_json_new(path: Path, value: object) -> None:
    _write_new(path, _canonical_json(value))


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RoutedEnsembleMetaError(f"{label} is unreadable: {path}") from exc


def _official_ids() -> set[int]:
    path = _ROOT / "data/raw/EN_Card_Data.csv"
    if not path.is_file():
        return set()
    result: set[int] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.match(r"\s*(\d+)\s*,", line)
        if match:
            result.add(int(match.group(1)))
    return result


def _official_ace_spec_ids() -> set[int]:
    path = _ROOT / "data/raw/EN_Card_Data.csv"
    if not path.is_file():
        return set()
    result: set[int] = set()
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("Rule", "")).strip().upper() != "ACE SPEC":
                    continue
                try:
                    result.add(int(str(row.get("Card ID", "")).strip()))
                except ValueError:
                    continue
    except (OSError, UnicodeError, csv.Error) as exc:
        raise RoutedEnsembleMetaError("official card catalog is unreadable") from exc
    return result


def _parse_deck(data: bytes, candidate_id: str) -> tuple[list[int], str]:
    try:
        cards = [int(token) for token in data.decode("utf-8", errors="strict").replace(",", " ").split()]
    except (UnicodeError, ValueError) as exc:
        raise RoutedEnsembleMetaError(f"{candidate_id}: deck is not an integer list") from exc
    if len(cards) != 60:
        raise RoutedEnsembleMetaError(f"{candidate_id}: deck must contain exactly 60 cards")
    official = _official_ids()
    normalized = normalize_deck_text(data.decode("utf-8"), source_id=candidate_id, path="deck.csv", official_ids=official)
    if normalized.get("eligibility") != "EXACT_60_VALID":
        raise RoutedEnsembleMetaError(f"{candidate_id}: deck is not locally official and exact-60")
    ace_ids = _official_ace_spec_ids()
    if ace_ids and sum(card in ace_ids for card in cards) != 1:
        raise RoutedEnsembleMetaError(f"{candidate_id}: deck must contain exactly one ACE SPEC")
    canonical = canonical_deck_sha256(cards)
    return cards, canonical


def _pool_row(root: Path, candidate_id: str) -> Mapping[str, Any]:
    manifest = root / "pool_manifest.json"
    if not manifest.is_file():
        manifest = root.parent / "pool_manifest.json"
    raw = _read_json(manifest, "parent pool manifest")
    rows = raw.get("opponents", raw) if isinstance(raw, Mapping) else raw
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise RoutedEnsembleMetaError(f"parent pool must contain a list: {manifest}")
    matches = [row for row in rows if isinstance(row, Mapping) and str(row.get("id", "")) == candidate_id]
    if len(matches) != 1:
        raise RoutedEnsembleMetaError(f"parent candidate is absent or duplicated: {candidate_id}")
    return matches[0]


def _source_note_sha(note: str) -> str | None:
    match = re.search(r"^- source policy SHA(?:-256)?: `([^`]+)`$", note, flags=re.MULTILINE)
    return match.group(1) if match else None


def _payload_python_files(root: Path, *, entrypoint: str | None = None) -> tuple[Path, ...]:
    files: list[Path] = []
    entrypoint_path = root / entrypoint if entrypoint else None
    for path in sorted(root.rglob("*.py")):
        if path.is_symlink() or "__pycache__" in path.parts:
            continue
        # A conventional Kaggle wrapper at root/main.py is generated by the
        # intake layer and is not the imported policy.  A self-owned sealed
        # package may instead expose its policy directly as root/main.py; in
        # that layout the entrypoint must be scanned as part of the safety
        # boundary.
        if path == root / "main.py" and path != entrypoint_path:
            continue
        files.append(path)
    return tuple(files)


def _read_parent(key: str, root_value: Path | str) -> _Parent:
    if not _ID.fullmatch(key):
        raise RoutedEnsembleMetaError(f"invalid parent key: {key!r}")
    root = Path(root_value).resolve()
    if not root.is_dir():
        raise RoutedEnsembleMetaError(f"parent root is not a directory: {root}")
    main_path, deck_path, note_path = root / "main.py", root / "deck.csv", root / "SOURCE.md"
    if any(path.is_symlink() or not path.is_file() for path in (main_path, deck_path, note_path)):
        raise RoutedEnsembleMetaError(f"parent root is missing a regular main.py/deck.csv/SOURCE.md: {root}")
    payload_main = root / "payload" / "original_main.py"
    if payload_main.is_symlink():
        raise RoutedEnsembleMetaError(f"parent payload main must be a regular file: {payload_main}")
    entrypoint = "payload/original_main.py" if payload_main.is_file() else "main.py"
    candidate_id = root.name
    row = _pool_row(root, candidate_id)
    policy_sha = _sha256_file(main_path)
    if str(row.get("policy_hash")) != policy_sha:
        raise RoutedEnsembleMetaError(f"{candidate_id}: pool policy hash does not match main.py")
    if row.get("smoke_ok") is not True:
        raise RoutedEnsembleMetaError(f"{candidate_id}: parent must be smoke-qualified")
    if row.get("usage_boundary") != LOCAL_EVAL_ONLY_V1:
        raise RoutedEnsembleMetaError(f"{candidate_id}: parent crosses local-eval-only boundary")
    deck_bytes = deck_path.read_bytes()
    _cards, canonical = _parse_deck(deck_bytes, candidate_id)
    if str(row.get("canonical_deck_hash")) != canonical:
        raise RoutedEnsembleMetaError(f"{candidate_id}: pool canonical deck hash mismatch")
    note = note_path.read_text(encoding="utf-8", errors="strict")
    source_policy = str(row.get("source_policy_sha256", ""))
    if not _SHA64.fullmatch(source_policy):
        source_policy = _source_note_sha(note) or ""
    if not _SHA64.fullmatch(source_policy):
        raise RoutedEnsembleMetaError(f"{candidate_id}: source policy SHA is missing")
    findings: set[str] = set()
    for path in _payload_python_files(root, entrypoint=entrypoint):
        source_findings, _imports = scan_source_text(path.read_text(encoding="utf-8", errors="strict"))
        findings.update(source_findings)
    if findings:
        raise RoutedEnsembleMetaError(f"{candidate_id}: parent payload is statically unsafe: {sorted(findings)}")
    return _Parent(
        key=key,
        root=root,
        candidate_id=candidate_id,
        entrypoint=entrypoint,
        policy_sha256=policy_sha,
        source_policy_sha256=source_policy,
        canonical_deck_hash=canonical,
        deck_bytes=deck_bytes,
        source_branch=str(row.get("source_branch", "sealed_parent")),
        source_commit=str(row.get("source_commit", "unknown")),
        source=str(row.get("source", "sealed_source")),
    )


def _copy_parent_assets(parent: _Parent, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=False)
    # Parent payloads are imported in an isolated working directory.  Several
    # legally sourced Kaggle kernels load their own deck.csv during import;
    # keep that contract intact instead of relying on the host path.
    _write_new(target / "deck.csv", parent.deck_bytes)
    if parent.entrypoint == "main.py":
        _write_new(target / "main.py", (parent.root / "main.py").read_bytes())
    excluded = {"main.py", "deck.csv", "SOURCE.md", "pool_manifest.json", "fresh_meta.json", "meta_manifest.json", "cg_historical_split.json", "smoke_summary.json", "smoke_promotion_report.json"}
    for child in sorted(parent.root.iterdir(), key=lambda path: path.name):
        if child.name in excluded or child.name.startswith("__pycache__"):
            continue
        destination = target / child.name
        if child.is_symlink():
            raise RoutedEnsembleMetaError(f"parent symlink is forbidden: {child}")
        if child.is_dir():
            shutil.copytree(child, destination)
        elif child.is_file():
            shutil.copy2(child, destination)
        else:
            raise RoutedEnsembleMetaError(f"unsupported parent asset: {child}")


def _wrapper_text(candidate_id: str, recipe: str, entrypoint_a: str, entrypoint_b: str) -> str:
    module_base = re.sub(r"[^A-Za-z0-9_]", "_", candidate_id)
    return f'''"""Generated actor-visible routed ensemble wrapper for {candidate_id}."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent
_ROUTE_RECIPE = {recipe!r}


def _shared_engine_root() -> Path:
    for ancestor in (_ROOT, *_ROOT.parents, Path.cwd()):
        if (ancestor / "cg" / "__init__.py").is_file():
            return ancestor
    raise RuntimeError("shared cg engine root is unavailable")


def _card_ids(line):
    values = []
    for card in (getattr(line, "active", None) or ()):
        values.append(None if card is None else getattr(card, "id", None))
    values.append("|")
    for card in (getattr(line, "bench", None) or ()):
        values.append(None if card is None else getattr(card, "id", None))
    return tuple(values)


def _public_snapshot(observation):
    state = getattr(observation, "current", None)
    turn = getattr(state, "turn", 0)
    your_index = getattr(state, "yourIndex", 0)
    players = getattr(state, "players", None) or ()
    lines = tuple(_card_ids(player) for player in players[:2])
    stadium = tuple(getattr(card, "id", None) for card in (getattr(state, "stadium", None) or ()))
    select = getattr(observation, "select", None)
    context = getattr(select, "context", None)
    context_name = getattr(context, "name", None) or getattr(context, "value", None) or str(context)
    return (int(turn) if isinstance(turn, int) else 0, int(your_index) if isinstance(your_index, int) else 0, lines, stadium, str(context_name))


def _opponent_flags(observation):
    state = getattr(observation, "current", None)
    your_index = getattr(state, "yourIndex", 0)
    players = getattr(state, "players", None) or ()
    if not isinstance(your_index, int) or not 0 <= your_index < len(players):
        return False, 0, False
    opponent = players[1 - your_index]
    active = tuple(getattr(opponent, "active", None) or ())
    bench = tuple(getattr(opponent, "bench", None) or ())
    visible = [card for card in active + bench if card is not None]
    damaged = any(
        isinstance(getattr(card, "hp", None), int)
        and isinstance(getattr(card, "maxHp", None), int)
        and int(getattr(card, "hp")) < int(getattr(card, "maxHp"))
        for card in visible
    )
    bench_size = len(bench)
    active_new = any(bool(getattr(card, "appearThisTurn", False)) for card in active if card is not None)
    return damaged, bench_size, active_new


def _route_index(observation):
    turn, your_index, lines, stadium, context = _public_snapshot(observation)
    if _ROUTE_RECIPE == "TURN_PARITY_V1":
        return turn & 1
    if _ROUTE_RECIPE == "OPPONENT_DAMAGE_SWITCH_V1":
        return 1 if _opponent_flags(observation)[0] else 0
    if _ROUTE_RECIPE == "OPPONENT_BOARD_SIZE_SWITCH_V1":
        damaged, bench_size, active_new = _opponent_flags(observation)
        return 1 if bench_size >= 2 or active_new else 0
    if _ROUTE_RECIPE == "CONTEXT_THREAT_SWITCH_V1":
        threat = any(token in context.upper() for token in ("DAMAGE", "ATTACK", "EVOLVE", "RETREAT", "SWITCH"))
        return 1 if threat or turn >= 10 else 0
    if _ROUTE_RECIPE == "OPPONENT_BOARD_HASH_V1":
        board = lines[1 - your_index] if 0 <= your_index < len(lines) else lines
        raw = repr((board, stadium)).encode("utf-8")
    elif _ROUTE_RECIPE == "CONTEXT_TURN_HASH_V1":
        raw = repr((turn, context, stadium)).encode("utf-8")
    else:
        raw = repr((turn, your_index, lines, stadium, context)).encode("utf-8")
    return hashlib.sha256(raw).digest()[0] & 1


def _load_agent(parent_root, entrypoint_relative, module_name):
    engine_root = _shared_engine_root()
    original = parent_root / entrypoint_relative
    if not original.is_file():
        raise RuntimeError(f"parent entrypoint is unavailable: {{original}}")
    if str(engine_root) not in sys.path:
        sys.path.insert(0, str(engine_root))
    import_root_text = str(original.parent)
    sys.path.insert(0, import_root_text)
    previous_cwd = os.getcwd()
    try:
        os.chdir(str(parent_root))
        spec = importlib.util.spec_from_file_location(module_name, original)
        if spec is None or spec.loader is None:
            raise RuntimeError("parent payload import spec is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        os.chdir(previous_cwd)
        try:
            sys.path.remove(import_root_text)
        except ValueError:
            pass
    agent = getattr(module, "agent", None)
    if agent is None or not callable(agent):
        raise RuntimeError("parent payload must expose callable agent")
    return agent


_AGENT_A = _load_agent(_ROOT / "parent_a", {entrypoint_a!r}, "_routed_parent_a_{module_base}")
_AGENT_B = _load_agent(_ROOT / "parent_b", {entrypoint_b!r}, "_routed_parent_b_{module_base}")


def _call_parent(selected, observation, configuration):
    try:
        previous_cwd = os.getcwd()
        try:
            os.chdir(str(_ROOT))
            if configuration is None:
                return selected(observation)
            return selected(observation, configuration)
        finally:
            os.chdir(previous_cwd)
    except Exception:
        return None


def _valid_indices(value, option_count, minimum, maximum):
    if not isinstance(value, (list, tuple)):
        return None
    if any(isinstance(index, bool) or not isinstance(index, int) for index in value):
        return None
    if len(value) < minimum or len(value) > maximum:
        return None
    if len(set(value)) != len(value) or any(index < 0 or index >= option_count for index in value):
        return None
    return list(value)


def _option_name(option):
    value = getattr(option, "type", None)
    return str(getattr(value, "name", None) or getattr(value, "value", None) or value).upper()


def _action_set_score(observation, indices, recipe):
    """Score only a parent's already-selected public option indices."""

    select = getattr(observation, "select", None)
    options = list(getattr(select, "option", None) or ()) if select is not None else []
    if not options:
        return 0
    state = getattr(observation, "current", None)
    your_index = getattr(state, "yourIndex", 0)
    players = getattr(state, "players", None) or ()
    turn = getattr(state, "turn", 0)
    opponent_hp = 0
    if isinstance(your_index, int) and 0 <= your_index < len(players):
        opponent = players[1 - your_index]
        active = tuple(getattr(opponent, "active", None) or ())
        card = active[0] if active else None
        opponent_hp = int(getattr(card, "hp", 0) or 0) if card is not None else 0
    try:
        engine_root = _shared_engine_root()
        if str(engine_root) not in sys.path:
            sys.path.insert(0, str(engine_root))
        from cg.api import all_attack, to_observation_class
        if isinstance(observation, dict):
            observation = to_observation_class(observation)
        attack_damage = dict((getattr(item, "attackId", None), int(getattr(item, "damage", 0) or 0)) for item in all_attack())
    except Exception:
        attack_damage = {{}}
    score = 0
    has_attack = False
    has_ko = False
    has_setup = False
    for index in indices:
        if not isinstance(index, int) or not 0 <= index < len(options):
            continue
        option = options[index]
        name = _option_name(option)
        if name == "ATTACK":
            has_attack = True
            damage = attack_damage.get(getattr(option, "attackId", None), 0)
            score += 100 + damage
            if opponent_hp > 0 and damage >= opponent_hp:
                has_ko = True
        elif name in ("EVOLVE", "ABILITY"):
            score += 80
            has_setup = True
        elif name in ("PLAY", "ATTACH", "ENERGY", "ENERGY_CARD", "TOOL_CARD"):
            score += 60
            has_setup = True
        elif name == "RETREAT":
            score += 35
        elif name == "END":
            score -= 100
        else:
            score += 10
    context = str(getattr(getattr(observation, "select", None), "context", "")).upper()
    if recipe in ("ACTION_LEVEL_KO_MIX_V1", "ACTION_LEVEL_CONSENSUS_KO_V1") and has_ko:
        score += 10000
    if recipe == "ACTION_LEVEL_TEMPO_MIX_V1" and (has_attack or "ATTACK" in context):
        score += 200 + (min(max(int(turn), 0), 20) * 5 if isinstance(turn, int) else 0)
    if recipe == "ACTION_LEVEL_SETUP_MIX_V1" and isinstance(turn, int) and turn <= 4 and has_setup and not has_attack:
        score += 500
    return score


def _action_level_pick(observation, configuration):
    first = _call_parent(_AGENT_A, observation, configuration)
    second = _call_parent(_AGENT_B, observation, configuration)
    if first == second:
        return first if first is not None else []
    select = getattr(observation, "select", None)
    if isinstance(observation, dict):
        try:
            engine_root = _shared_engine_root()
            if str(engine_root) not in sys.path:
                sys.path.insert(0, str(engine_root))
            from cg.api import to_observation_class
            select = getattr(to_observation_class(observation), "select", None)
            observation = to_observation_class(observation)
        except Exception:
            select = None
    options = list(getattr(select, "option", None) or ()) if select is not None else []
    minimum = int(getattr(select, "minCount", 0) or 0) if select is not None else 0
    maximum = int(getattr(select, "maxCount", len(options)) or len(options)) if select is not None else 0
    minimum = max(0, min(minimum, len(options)))
    maximum = max(minimum, min(maximum, len(options)))
    first_valid = _valid_indices(first, len(options), minimum, maximum)
    second_valid = _valid_indices(second, len(options), minimum, maximum)
    if first_valid is None:
        if second_valid is not None:
            return second_valid
        raise RuntimeError("both action-level parents returned invalid selections")
    if second_valid is None:
        return first_valid
    if _ROUTE_RECIPE == "ACTION_LEVEL_CONSENSUS_MIX_V1":
        second_set = set(second_valid)
        common = [index for index in first_valid if index in second_set]
        # Prefer an action set both smoke-qualified parents already deemed
        # legal.  When no common set can satisfy a required selection, fall
        # through to the public-only score/tie-breaker below; never invent an
        # index or relax the simulator's min/max contract.
        required = minimum if minimum > 0 else 1
        if len(common) >= required:
            return common[:maximum]
    score_first = _action_set_score(observation, first_valid, _ROUTE_RECIPE)
    score_second = _action_set_score(observation, second_valid, _ROUTE_RECIPE)
    if score_first != score_second and _ROUTE_RECIPE not in ("ACTION_LEVEL_HASH_MIX_V1", "ACTION_LEVEL_CONSENSUS_HASH_V1"):
        return first_valid if score_first > score_second else second_valid
    raw = repr(_public_snapshot(observation)).encode("utf-8")
    return first_valid if hashlib.sha256(raw).digest()[0] & 1 == 0 else second_valid


def agent(observation, configuration=None):
    if _ROUTE_RECIPE.startswith("ACTION_LEVEL_"):
        return _action_level_pick(observation, configuration)
    selected = _AGENT_A if _route_index(observation) == 0 else _AGENT_B
    return _call_parent(selected, observation, configuration)
'''


def route_parent_index(observation: object, recipe: str) -> int:
    """Return the deterministic parent bucket using actor-visible fields only."""

    if recipe not in ROUTING_RECIPES_V1:
        raise RoutedEnsembleMetaError(f"unsupported routing recipe: {recipe}")
    state = getattr(observation, "current", None)
    turn = getattr(state, "turn", 0)
    your_index = getattr(state, "yourIndex", 0)
    players = getattr(state, "players", None) or ()

    def card_ids(line: object) -> tuple[object, ...]:
        values: list[object] = []
        for card in (getattr(line, "active", None) or ()):
            values.append(None if card is None else getattr(card, "id", None))
        values.append("|")
        for card in (getattr(line, "bench", None) or ()):
            values.append(None if card is None else getattr(card, "id", None))
        return tuple(values)

    lines = tuple(card_ids(player) for player in players[:2])
    stadium = tuple(getattr(card, "id", None) for card in (getattr(state, "stadium", None) or ()))
    select = getattr(observation, "select", None)
    context = getattr(select, "context", None)
    context_name = getattr(context, "name", None) or getattr(context, "value", None) or str(context)
    turn_value = int(turn) if isinstance(turn, int) else 0
    index_value = int(your_index) if isinstance(your_index, int) else 0
    if recipe == "TURN_PARITY_V1":
        return turn_value & 1
    if recipe == "OPPONENT_DAMAGE_SWITCH_V1":
        if not isinstance(index_value, int) or not 0 <= index_value < len(players):
            return 0
        opponent = players[1 - index_value]
        active = tuple(getattr(opponent, "active", None) or ())
        bench = tuple(getattr(opponent, "bench", None) or ())
        visible = [card for card in active + bench if card is not None]
        damaged = any(isinstance(getattr(card, "hp", None), int) and isinstance(getattr(card, "maxHp", None), int) and int(getattr(card, "hp")) < int(getattr(card, "maxHp")) for card in visible)
        return 1 if damaged else 0
    if recipe == "OPPONENT_BOARD_SIZE_SWITCH_V1":
        if not isinstance(index_value, int) or not 0 <= index_value < len(players):
            return 0
        opponent = players[1 - index_value]
        active = tuple(getattr(opponent, "active", None) or ())
        bench = tuple(getattr(opponent, "bench", None) or ())
        bench_size = len(bench)
        active_new = any(bool(getattr(card, "appearThisTurn", False)) for card in active if card is not None)
        return 1 if bench_size >= 2 or active_new else 0
    if recipe == "CONTEXT_THREAT_SWITCH_V1":
        threat = any(token in str(context_name).upper() for token in ("DAMAGE", "ATTACK", "EVOLVE", "RETREAT", "SWITCH"))
        return 1 if threat or turn_value >= 10 else 0
    if recipe.startswith("ACTION_LEVEL_"):
        raw = repr((turn_value, index_value, lines, stadium, str(context_name))).encode("utf-8")
        return hashlib.sha256(raw).digest()[0] & 1
    if recipe == "OPPONENT_BOARD_HASH_V1":
        board = lines[1 - index_value] if 0 <= index_value < len(lines) else lines
        raw = repr((board, stadium)).encode("utf-8")
    elif recipe == "CONTEXT_TURN_HASH_V1":
        raw = repr((turn_value, str(context_name), stadium)).encode("utf-8")
    else:
        raw = repr((turn_value, index_value, lines, stadium, str(context_name))).encode("utf-8")
    return hashlib.sha256(raw).digest()[0] & 1


def _existing_pairs(path: Path | None) -> set[tuple[str, str]]:
    if path is None:
        return set()
    raw = _read_json(path.resolve(), "current pool manifest")
    rows = raw.get("opponents", raw) if isinstance(raw, Mapping) else raw
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise RoutedEnsembleMetaError("current pool manifest must contain a list")
    return {(str(row.get("policy_hash")), str(row.get("canonical_deck_hash"))) for row in rows if isinstance(row, Mapping)}


def _artifact_contains(roots: Sequence[Path], token: str) -> bool:
    needle = token.encode("ascii", errors="ignore")
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*")) if root.exists() else []
        for path in paths:
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in {".json", ".jsonl", ".md", ".txt", ".csv", ".py"}:
                continue
            try:
                if path.stat().st_size > 16 * 1024 * 1024:
                    continue
                if needle in path.read_bytes():
                    return True
            except OSError:
                continue
    return False


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_ROOT))
    except ValueError:
        return str(path.resolve())


def _candidate_id(spec: Mapping[str, str], parent_a: _Parent, parent_b: _Parent, deck: _Parent) -> str:
    routing_recipe = str(spec.get("routing_recipe", ""))
    digest = _sha256_bytes(_canonical_json({"recipe": _derivation_recipe(routing_recipe), "spec": dict(spec), "a": parent_a.policy_sha256, "a_entrypoint": parent_a.entrypoint, "b": parent_b.policy_sha256, "b_entrypoint": parent_b.entrypoint, "deck": deck.canonical_deck_hash}))[:12]
    value = f"routed_{spec['id']}_{digest}"
    if not _ID.fullmatch(value):
        raise RoutedEnsembleMetaError(f"generated candidate id is invalid: {value}")
    return value


def _source_sha(candidate_id: str, parent_a: _Parent, parent_b: _Parent, deck: _Parent, recipe: str) -> str:
    return _sha256_bytes(_canonical_json({"candidate_id": candidate_id, "recipe": _derivation_recipe(recipe), "routing_recipe": recipe, "policy_a": parent_a.policy_sha256, "policy_a_entrypoint": parent_a.entrypoint, "policy_b": parent_b.policy_sha256, "policy_b_entrypoint": parent_b.entrypoint, "deck_parent": deck.canonical_deck_hash}))


def _build_split(output: Path, rows: Sequence[Mapping[str, object]], meta_rows: Sequence[Mapping[str, object]], p1_package: Path) -> Path:
    if len(rows) < 3:
        raise RoutedEnsembleMetaError("at least three routed candidates are required for train/dev/final separation")
    meta_path = output / "meta_manifest.json"
    pool_path = output / "pool_manifest.json"
    source_kind = str(meta_rows[0].get("source", ROUTED_ENSEMBLE_SOURCE_V1)) if meta_rows else ROUTED_ENSEMBLE_SOURCE_V1
    _write_json_new(meta_path, {"schema_version": "cg-routed-ensemble-meta-distribution-v1", "research_only": True, "source_kind": source_kind, "rows": list(meta_rows)})
    p1_main, p1_deck = p1_package / "main.py", p1_package / "deck.csv"
    if not p1_main.is_file() or not p1_deck.is_file():
        raise RoutedEnsembleMetaError("P1 package must contain main.py and deck.csv")
    ids = sorted(str(row["id"]) for row in rows)
    meta_by_id = {str(row["opponent_id"]): row for row in meta_rows}

    def split_row(candidate_id: str) -> dict[str, object]:
        item = meta_by_id[candidate_id]
        return {key: item[key] for key in ("opponent_id", "archetype", "deck_sha256", "policy_sha256", "source_sha256", "weight", "usage_boundary", "training_exposure")}

    split = {
        "schema_version": "cg-weekend-meta-splits-v1",
        "research_only": True,
        "candidate_exclusion_ids": [],
        "bindings": {"p1_policy_sha256": _sha256_file(p1_main), "p1_deck_sha256": _sha256_file(p1_deck), "meta_manifest_sha256": _sha256_file(meta_path), "pool_manifest_sha256": _sha256_file(pool_path), "evaluator_sha256": evaluation_implementation_sha256_v1()},
        "sources": {"meta_manifest_path": _relative(meta_path), "pool_manifest_path": _relative(pool_path)},
        "evaluation_contract": {"both_seats": True, "fault_inclusive": True, "training_exposure": 0, "teacher_labels_saved": False, "final_results_read_during_search": False},
        "train_blocks": [ids[:-2]],
        "splits": {"META_TRAIN": [split_row(item) for item in ids[:-2]], "META_DEV": [split_row(ids[-2])], "META_FINAL": [split_row(ids[-1])]},
        "notes": ["Actor-visible routed/action-level mixer is a local-eval-only source recipe.", "Runtime smoke promotion is required before this split is bound to CEM."],
    }
    split_path = output / "cg_historical_split.json"
    _write_json_new(split_path, split)
    return split_path


def seal_routed_ensemble_meta_v1(*, parent_roots: Mapping[str, Path | str], specifications: Sequence[Mapping[str, str]], output_root: Path | str, source_epoch: str, seed_namespace: str, p1_package: Path | str, current_pool_manifest: Path | str | None = None, scan_roots: Sequence[Path | str] = ()) -> dict[str, object]:
    """Generate a fresh routed-ensemble pool and an unpromoted split."""

    if not source_epoch.strip() or not seed_namespace.strip():
        raise RoutedEnsembleMetaError("source_epoch and seed_namespace must be non-empty")
    if not parent_roots:
        raise RoutedEnsembleMetaError("at least one parent root is required")
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(output)
    parents = {str(key): _read_parent(str(key), value) for key, value in parent_roots.items()}
    if len(parents) < 2:
        raise RoutedEnsembleMetaError("at least two distinct parent roots are required")
    specs = [dict(spec) for spec in specifications]
    if not specs or len({str(spec.get("id", "")) for spec in specs}) != len(specs):
        raise RoutedEnsembleMetaError("specifications must have unique non-empty ids")
    resolved_specs: list[tuple[dict[str, str], _Parent, _Parent, _Parent]] = []
    for spec in specs:
        required = ("id", "policy_a", "policy_b", "deck_parent", "routing_recipe")
        missing = [key for key in required if not str(spec.get(key, "")).strip()]
        if missing:
            raise RoutedEnsembleMetaError(f"specification is missing fields: {missing}")
        recipe = str(spec["routing_recipe"])
        if recipe not in ROUTING_RECIPES_V1:
            raise RoutedEnsembleMetaError(f"unsupported routing recipe: {recipe}")
        try:
            parent_a = parents[str(spec["policy_a"])]
            parent_b = parents[str(spec["policy_b"])]
            deck = parents[str(spec["deck_parent"])]
        except KeyError as exc:
            raise RoutedEnsembleMetaError(f"specification references unknown parent: {exc.args[0]}") from exc
        if parent_a.canonical_deck_hash != deck.canonical_deck_hash or parent_b.canonical_deck_hash != deck.canonical_deck_hash:
            raise RoutedEnsembleMetaError(f"{spec['id']}: deck parent canonical hash must match both policy parents")
        resolved_specs.append((spec, parent_a, parent_b, deck))
    existing_pairs = _existing_pairs(Path(current_pool_manifest).resolve() if current_pool_manifest else None)
    scan_paths = tuple(Path(path).resolve() for path in scan_roots)
    output.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, object]] = []
    meta_rows: list[dict[str, object]] = []
    references: list[dict[str, object]] = []
    rejected: dict[str, list[str]] = {}
    evidence_dir = output / "evidence"
    for spec, parent_a, parent_b, deck in resolved_specs:
        recipe = str(spec["routing_recipe"])
        candidate_id = _candidate_id(spec, parent_a, parent_b, deck)
        target = output / candidate_id
        reasons: list[str] = []
        if target.exists() or _artifact_contains(scan_paths, candidate_id):
            reasons.append("candidate_id_reused")
        if reasons:
            rejected[candidate_id] = sorted(set(reasons))
            continue
        target.mkdir(parents=True, exist_ok=False)
        try:
            _copy_parent_assets(parent_a, target / "parent_a")
            _copy_parent_assets(parent_b, target / "parent_b")
            _write_new(target / "main.py", _wrapper_text(candidate_id, recipe, parent_a.entrypoint, parent_b.entrypoint).encode("utf-8"))
            _write_new(target / "deck.csv", deck.deck_bytes)
        except Exception:
            shutil.rmtree(target)
            raise
        policy_sha = _sha256_file(target / "main.py")
        pair = (policy_sha, deck.canonical_deck_hash)
        if pair in existing_pairs or any((str(row.get("policy_hash")), str(row.get("canonical_deck_hash"))) == pair for row in rows):
            shutil.rmtree(target)
            rejected[candidate_id] = ["pair_identity_reused"]
            continue
        source_sha = _source_sha(candidate_id, parent_a, parent_b, deck, recipe)
        evidence = {
            "candidate_id": candidate_id,
            "fresh": True,
            "unused_before_run": True,
            "source": _source_kind(recipe),
            "usage_boundary": LOCAL_EVAL_ONLY_V1,
            "derivation_recipe": _derivation_recipe(recipe),
            "routing_recipe": recipe,
            "policy_a_parent_id": parent_a.candidate_id,
            "policy_a_parent_policy_sha256": parent_a.policy_sha256,
            "policy_a_parent_entrypoint": parent_a.entrypoint,
            "policy_b_parent_id": parent_b.candidate_id,
            "policy_b_parent_policy_sha256": parent_b.policy_sha256,
            "policy_b_parent_entrypoint": parent_b.entrypoint,
            "deck_parent_id": deck.candidate_id,
            "deck_parent_canonical_deck_hash": deck.canonical_deck_hash,
            "policy_sha256": policy_sha,
            "canonical_deck_hash": deck.canonical_deck_hash,
            "source_sha256": source_sha,
            "static_findings": [],
            "runtime_smoke_required": True,
            "public_state_fields": ["turn", "yourIndex", "active_ids", "bench_ids", "stadium_ids", "selection_context"],
            "private_fields_used": [],
            "mixing_mode": "ACTION_LEVEL_LEGAL_INDEX_SET" if recipe.startswith("ACTION_LEVEL_") else "OBSERVATION_LEVEL_PARENT_ROUTE",
            "both_parent_calls_per_decision": bool(recipe.startswith("ACTION_LEVEL_")),
            "runtime_cost_multiplier": 2 if recipe.startswith("ACTION_LEVEL_") else 1,
        }
        evidence_path = evidence_dir / f"{candidate_id}.json"
        _write_json_new(evidence_path, evidence)
        _write_new(target / "SOURCE.md", ("# Actor-visible routed/action-level mixer meta source (research-only)\n\n" f"- derivation recipe: `{_derivation_recipe(recipe)}`\n" f"- routing recipe: `{recipe}`\n" f"- policy A parent: `{parent_a.candidate_id}` (`{parent_a.policy_sha256}`)\n" f"- policy B parent: `{parent_b.candidate_id}` (`{parent_b.policy_sha256}`)\n" f"- deck parent: `{deck.candidate_id}` (`{deck.canonical_deck_hash}`)\n" f"- generated wrapper SHA-256: `{policy_sha}`\n" f"- source SHA-256: `{source_sha}`\n" "- public state only: `turn, yourIndex, active_ids, bench_ids, stadium_ids, selection_context`\n" "- private fields used: `none`\n" f"- mixer mode: `{'ACTION_LEVEL_LEGAL_INDEX_SET' if recipe.startswith('ACTION_LEVEL_') else 'OBSERVATION_LEVEL_PARENT_ROUTE'}`\n" f"- parent calls per decision: `{2 if recipe.startswith('ACTION_LEVEL_') else 1}`\n" "- usage boundary: `local_eval_only`\n" "- runtime smoke: `REQUIRED_BEFORE_CEM`\n" "- submission bundle: prohibited\n").encode("utf-8"))
        row = {"id": candidate_id, "canonical_deck_hash": deck.canonical_deck_hash, "mean_decision_ms": None, "policy_hash": policy_sha, "source_policy_sha256": parent_a.source_policy_sha256, "smoke_ok": False, "source": _source_kind(recipe), "source_branch": f"routed/{parent_a.source_branch}+{parent_b.source_branch}", "source_commit": f"{parent_a.source_commit}+{parent_b.source_commit}", "usage_boundary": LOCAL_EVAL_ONLY_V1, "asset_preflight": "STATIC_AND_EXACT_60", "derivation_recipe": _derivation_recipe(recipe), "routing_recipe": recipe, "policy_a_parent_id": parent_a.candidate_id, "policy_b_parent_id": parent_b.candidate_id, "deck_parent_id": deck.candidate_id}
        rows.append(row)
        meta_rows.append({"opponent_id": candidate_id, "archetype": f"ActionLevelMixer:{recipe}" if recipe.startswith("ACTION_LEVEL_") else f"RoutedEnsemble:{recipe}", "deck_sha256": deck.canonical_deck_hash, "policy_sha256": policy_sha, "source_sha256": source_sha, "weight": 1.0, "usage_boundary": LOCAL_EVAL_ONLY_V1, "training_exposure": 0, "source": _source_kind(recipe), "derivation_recipe": _derivation_recipe(recipe), "routing_recipe": recipe})
        references.append({"id": candidate_id, "fresh": True, "unused_before_run": True, "freshness_evidence_sha256": _sha256_file(evidence_path), "freshness_evidence_path": str(Path("evidence") / evidence_path.name), "policy_sha256": policy_sha, "canonical_deck_hash": deck.canonical_deck_hash, "source": _source_kind(recipe), "source_sha256": source_sha})
    if len(rows) < 3:
        raise RoutedEnsembleMetaError(f"routed ensemble recipe produced {len(rows)} candidates; at least 3 are required")
    rows.sort(key=lambda row: str(row["id"]))
    meta_rows.sort(key=lambda row: str(row["opponent_id"]))
    references.sort(key=lambda row: str(row["id"]))
    pool_path = output / "pool_manifest.json"
    meta_path = output / "meta_manifest.json"
    fresh_path = output / "fresh_meta.json"
    _write_json_new(pool_path, rows)
    pool_sha = _sha256_file(pool_path)
    seed_plan_sha = _sha256_bytes(_canonical_json({"source_epoch": source_epoch, "seed_namespace": seed_namespace, "reference_ids": [str(row["id"]) for row in rows]}))
    _write_json_new(fresh_path, {"schema_version": FRESH_META_SCHEMA_V1, "batch_id": f"routed-ensemble-{re.sub(r'[^A-Za-z0-9_.-]+', '-', source_epoch)}-{re.sub(r'[^A-Za-z0-9_.-]+', '-', seed_namespace)}", "source_epoch": source_epoch, "seed_namespace": seed_namespace, "seed_plan_sha256": seed_plan_sha, "pool_manifest_sha256": pool_sha, "reference_ids": [str(row["id"]) for row in rows], "references": references, "freshness_basis": "new actor-visible routed policy wrapper with hash-bound parent/deck identities; runtime smoke pending", "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False}, "research_only": True})
    split_path = _build_split(output, rows, meta_rows, Path(p1_package).resolve())
    report_recipe = ACTION_LEVEL_RECIPE_V1 if rows and all(str(row.get("routing_recipe", "")).startswith("ACTION_LEVEL_") for row in rows) else RECIPE_V1
    report_source_kind = _source_kind(str(rows[0].get("routing_recipe", ""))) if rows else ROUTED_ENSEMBLE_SOURCE_V1
    report = {"schema_version": ROUTED_ENSEMBLE_META_SCHEMA_V1, "status": "SEALED", "source_epoch": source_epoch, "seed_namespace": seed_namespace, "recipe": report_recipe, "source_kind": report_source_kind, "accepted_count": len(rows), "accepted_ids": [str(row["id"]) for row in rows], "rejected": rejected, "pool_manifest_path": str(pool_path), "pool_manifest_sha256": pool_sha, "meta_manifest_path": str(meta_path), "meta_manifest_sha256": _sha256_file(meta_path), "fresh_meta_path": str(fresh_path), "fresh_meta_sha256": _sha256_file(fresh_path), "split_path": str(split_path), "split_sha256": _sha256_file(split_path), "runtime_smoke_required": True, "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False}, "research_only": True, "imports_executed": False, "network_access": False}
    _write_json_new(output / "intake_report.json", report)
    return report


def build_routed_ensemble_split_v1(*, output_root: Path | str, p1_package: Path | str) -> dict[str, object]:
    """Rebind manifests after every selected pool row is smoke-qualified."""

    output = Path(output_root).resolve()
    pool_path, fresh_path = output / "pool_manifest.json", output / "fresh_meta.json"
    if not pool_path.is_file() or not fresh_path.is_file():
        raise RoutedEnsembleMetaError("promoted root must contain pool_manifest.json and fresh_meta.json")
    raw_pool = _read_json(pool_path, "pool manifest")
    rows = raw_pool.get("opponents", raw_pool) if isinstance(raw_pool, Mapping) else raw_pool
    fresh = _read_json(fresh_path, "fresh meta")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, Mapping) for row in rows):
        raise RoutedEnsembleMetaError("pool manifest must contain rows")
    if any(row.get("smoke_ok") is not True for row in rows):
        raise RoutedEnsembleMetaError("split can be rebound only after smoke promotion")
    if not isinstance(fresh, Mapping) or not isinstance(fresh.get("references"), list):
        raise RoutedEnsembleMetaError("fresh_meta.references must be a list")
    refs = {str(item.get("id")): item for item in fresh["references"] if isinstance(item, Mapping)}
    meta_rows: list[dict[str, object]] = []
    for row in rows:
        candidate_id = str(row["id"])
        ref = refs.get(candidate_id)
        if ref is None:
            raise RoutedEnsembleMetaError(f"fresh meta is missing {candidate_id}")
        routing_recipe = str(row.get("routing_recipe", "UNKNOWN"))
        meta_rows.append({"opponent_id": candidate_id, "archetype": f"ActionLevelMixer:{routing_recipe}" if routing_recipe.startswith("ACTION_LEVEL_") else f"RoutedEnsemble:{routing_recipe}", "deck_sha256": str(row["canonical_deck_hash"]), "policy_sha256": str(row["policy_hash"]), "source_sha256": str(ref.get("source_sha256")), "weight": 1.0, "usage_boundary": LOCAL_EVAL_ONLY_V1, "training_exposure": 0, "source": _source_kind(routing_recipe), "derivation_recipe": _derivation_recipe(routing_recipe), "routing_recipe": routing_recipe})
    meta_path = output / "meta_manifest.json"
    if meta_path.exists():
        raise FileExistsError(meta_path)
    meta_source_kind = str(meta_rows[0].get("source", ROUTED_ENSEMBLE_SOURCE_V1)) if meta_rows else ROUTED_ENSEMBLE_SOURCE_V1
    _write_json_new(meta_path, {"schema_version": "cg-routed-ensemble-meta-distribution-v1", "research_only": True, "source_kind": meta_source_kind, "rows": sorted(meta_rows, key=lambda item: str(item["opponent_id"]))})
    p1_root = Path(p1_package).resolve()
    p1_main, p1_deck = p1_root / "main.py", p1_root / "deck.csv"
    if not p1_main.is_file() or not p1_deck.is_file():
        raise RoutedEnsembleMetaError("P1 package must contain main.py and deck.csv")
    ids = sorted(str(row["id"]) for row in rows)
    meta_by_id = {str(row["opponent_id"]): row for row in meta_rows}

    def split_row(candidate_id: str) -> dict[str, object]:
        item = meta_by_id[candidate_id]
        return {key: item[key] for key in ("opponent_id", "archetype", "deck_sha256", "policy_sha256", "source_sha256", "weight", "usage_boundary", "training_exposure")}

    split = {"schema_version": "cg-weekend-meta-splits-v1", "research_only": True, "candidate_exclusion_ids": [], "bindings": {"p1_policy_sha256": _sha256_file(p1_main), "p1_deck_sha256": _sha256_file(p1_deck), "meta_manifest_sha256": _sha256_file(meta_path), "pool_manifest_sha256": _sha256_file(pool_path), "evaluator_sha256": evaluation_implementation_sha256_v1()}, "sources": {"meta_manifest_path": _relative(meta_path), "pool_manifest_path": _relative(pool_path)}, "evaluation_contract": {"both_seats": True, "fault_inclusive": True, "training_exposure": 0, "teacher_labels_saved": False, "final_results_read_during_search": False}, "train_blocks": [ids[:-2]], "splits": {"META_TRAIN": [split_row(item) for item in ids[:-2]], "META_DEV": [split_row(ids[-2])], "META_FINAL": [split_row(ids[-1])]}, "notes": ["Smoke-promoted actor-visible routed ensemble pool.", "Routed wrapper reads no private opponent fields."]}
    split_path = output / "cg_historical_split.json"
    _write_json_new(split_path, split)
    return {"status": "SEALED", "meta_manifest_path": str(meta_path), "meta_manifest_sha256": _sha256_file(meta_path), "split_path": str(split_path), "split_sha256": _sha256_file(split_path)}


__all__ = ["ROUTED_ENSEMBLE_META_SCHEMA_V1", "ROUTED_ENSEMBLE_SOURCE_V1", "ACTION_LEVEL_MIX_SOURCE_V1", "ACTION_LEVEL_RECIPE_V1", "RECIPE_V1", "ROUTING_RECIPES_V1", "RoutedEnsembleMetaError", "route_parent_index", "seal_routed_ensemble_meta_v1", "build_routed_ensemble_split_v1"]
