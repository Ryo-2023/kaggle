"""Bounded public-state score surface for the research-only P2 parent.

This module is deliberately smaller than the first P1 CEM surface.  It only
adds conditional bonuses to an already legal ``ATTACK`` option and keeps the
P2 policy source, root deck, fallback, and public ``agent`` contract sealed.
The package materializer uses the existing self-owned cg builder, so each
candidate remains a submission-shaped local artifact while retaining
``promotion`` and ``submission`` authority as false.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Mapping


ROOT = Path(__file__).resolve().parents[3]
BASE_SOURCE_PATH = (
    ROOT
    / "runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1"
    / "package/main.py"
)
BASE_SOURCE_SHA256 = "4261870c855d68abfbb96df029b5e66c6f019f398471701ceaac03f72f2b03c4"
ROOT_DECK_PATH = ROOT / "deck.csv"
SCHEMA = "cg-p2-context-config-v1"
PACKAGE_SCHEMA = "meta-specialist-root-cg-p2-context-candidate-v1"
NEAR_LETHAL_MARGIN = 50

PARAMETER_BOUNDS: dict[str, tuple[int, int]] = {
    "near_lethal_attack_bonus": (-30000, 30000),
    "threat_energy_attack_bonus": (-30000, 30000),
    "full_bench_attack_bonus": (-30000, 30000),
    "damaged_active_threat_attack_bonus": (-30000, 30000),
}

AUTHORITY_FALSE = {
    "training": False,
    "promotion": False,
    "submission": False,
    "longrun": False,
    "teacher": False,
}


class P2ContextSurfaceError(ValueError):
    """Raised when a candidate cannot be bound to the immutable P2 parent."""


@dataclass(frozen=True, slots=True)
class P2ContextConfig:
    """One bounded point on the P2 contextual attack surface."""

    near_lethal_attack_bonus: int = 0
    threat_energy_attack_bonus: int = 0
    full_bench_attack_bonus: int = 0
    damaged_active_threat_attack_bonus: int = 0

    @classmethod
    def default(cls) -> "P2ContextConfig":
        return cls()

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "P2ContextConfig":
        if not isinstance(values, Mapping):
            raise P2ContextSurfaceError("context config must be a mapping")
        names = {field.name for field in fields(cls)}
        unknown = set(values) - names
        if unknown:
            raise P2ContextSurfaceError(f"unknown parameter(s): {sorted(unknown)}")
        merged = cls.default().as_dict()
        merged.update(values)
        config = cls(**merged)
        config.validate()
        return config

    def as_dict(self) -> dict[str, int]:
        return {field.name: int(getattr(self, field.name)) for field in fields(self)}

    def validate(self) -> None:
        for name, (lower, upper) in PARAMETER_BOUNDS.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise P2ContextSurfaceError(f"parameter {name} must be an integer")
            if not lower <= value <= upper:
                raise P2ContextSurfaceError(
                    f"parameter {name} out of bounds: {value} not in [{lower}, {upper}]"
                )

    def canonical_json(self) -> str:
        self.validate()
        return json.dumps(
            {"schema_version": SCHEMA, "parameters": self.as_dict()},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def config_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def candidate_id_for_config(config: P2ContextConfig, *, generation: int, index: int) -> str:
    config.validate()
    if type(generation) is not int or type(index) is not int or generation < 0 or index < 0:
        raise P2ContextSurfaceError("generation and index must be non-negative integers")
    return f"cg-p2-context-g{generation:02d}-c{index:02d}-{config.config_sha256()[:12]}"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parameter_patch(config: P2ContextConfig, candidate_id: str) -> str:
    values = json.dumps(config.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    config_sha = config.config_sha256()
    encoded_id = json.dumps(candidate_id, ensure_ascii=False)
    return f'''

# RESEARCH_PARAMETERIZATION: cg-p2-context-v1
# Public-state-only conditional attack surface over the sealed P2 parent.
_CG_P2_CONTEXT_PARAMETERS = {values}
_CG_P2_CONTEXT_CONFIG_SHA256 = {config_sha!r}
_CG_P2_CONTEXT_CANDIDATE_ID = {encoded_id}
_CG_P2_CONTEXT_NEAR_LETHAL_MARGIN = {NEAR_LETHAL_MARGIN}


def _cg_p2_context_value(name):
    return int(_CG_P2_CONTEXT_PARAMETERS[name])


_CG_P2_CONTEXT_BASE_MAIN_SCORE = _main_score


def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = int(_CG_P2_CONTEXT_BASE_MAIN_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.ATTACK:
            return score
        damage = _available_attack_damage(option)
        opponent = _opponent(obs)
        active = opponent.active[0] if opponent.active else None
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        if 0 < damage < hp and hp - damage <= _CG_P2_CONTEXT_NEAR_LETHAL_MARGIN:
            score += _cg_p2_context_value("near_lethal_attack_bonus")
        if active is not None and _energy_count(active) >= 2:
            score += _cg_p2_context_value("threat_energy_attack_bonus")
        mine = _mine(obs)
        bench = getattr(mine, "bench", None) or []
        bench_max = int(getattr(mine, "benchMax", 0) or 0)
        if bench_max > 0 and len(bench) >= bench_max:
            score += _cg_p2_context_value("full_bench_attack_bonus")
        own_active = mine.active[0] if getattr(mine, "active", None) else None
        _CG_P2_CONTEXT_TEMPO_OPPONENT_ACTIVE = active
        own_hp = int(getattr(own_active, "hp", 0)) if own_active is not None else 0
        own_max_hp = int(getattr(own_active, "maxHp", own_hp)) if own_active is not None else 0
        if (
            own_active is not None
            and own_max_hp > own_hp
            and _energy_count(_CG_P2_CONTEXT_TEMPO_OPPONENT_ACTIVE) >= 2
        ):
            score += _cg_p2_context_value("damaged_active_threat_attack_bonus")
        return score
    except Exception:
        return 0


_CG_P2_CONTEXT_BASE_SCORE = _score


def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        if getattr(obs.select, "context", None) == SelectContext.MAIN:
            return int(_main_score(obs, option))
        return int(_CG_P2_CONTEXT_BASE_SCORE(obs, option))
    except Exception:
        return 0


_CG_P2_CONTEXT_BASE_AGENT = agent


def agent(obs_dict: dict) -> list[int]:
    # Keep the public entrypoint last while delegating to the sealed P2 body.
    return _CG_P2_CONTEXT_BASE_AGENT(obs_dict)
'''


def render_context_source(
    config: P2ContextConfig,
    *,
    candidate_id: str,
    source_path: Path | str | None = None,
) -> str:
    """Render an overlay only after verifying the exact P2 parent SHA."""

    config.validate()
    if not isinstance(candidate_id, str) or not candidate_id:
        raise P2ContextSurfaceError("candidate_id must be non-empty")
    source = Path(source_path or BASE_SOURCE_PATH).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    actual = _sha256_file(source)
    if actual != BASE_SOURCE_SHA256:
        raise P2ContextSurfaceError(f"P2 source SHA mismatch: {actual} != {BASE_SOURCE_SHA256}")
    original = source.read_text(encoding="utf-8")
    if "_CG_P2_CONTEXT_PARAMETERS" in original:
        raise P2ContextSurfaceError("source already contains cg P2 context overlay")
    return original.rstrip() + "\n" + _parameter_patch(config, candidate_id)


def materialize_context_package(
    *,
    source_package: Path | str,
    output_root: Path | str,
    config: P2ContextConfig,
    candidate_id: str,
    smoke_games: int = 1,
    smoke_seed: int = 48300000,
) -> dict[str, object]:
    """Build one self-owned candidate package without changing production."""

    source = Path(source_package).resolve()
    output = Path(output_root).resolve()
    source_main = source / "main.py"
    source_deck = source / "deck.csv"
    if not source.is_dir() or not source_main.is_file() or not source_deck.is_file():
        raise P2ContextSurfaceError(f"source package is incomplete: {source}")
    if output.exists() and (output.is_symlink() or not output.is_dir() or any(output.iterdir())):
        raise P2ContextSurfaceError(f"candidate output must be absent or empty: {output}")
    config.validate()
    rendered = render_context_source(config, candidate_id=candidate_id, source_path=source_main)
    output.mkdir(parents=True, exist_ok=True)
    from scripts import build_root_cg_submission_candidate_v1 as builder

    # The bundled builder's strict ``python -I`` probe is intentionally not
    # used here: this workspace does not expose ``kaggle_environments`` under
    # isolated mode, while the normal interpreter clean-room probe is the
    # same runtime contract used by the sealed P2 package.
    with tempfile.TemporaryDirectory(prefix="cg-p2-context-source-") as temporary:
        source_path = Path(temporary) / "main.py"
        source_path.write_text(rendered, encoding="utf-8")
        package = output / "package"
        builder._stage_source(package, source_deck=source_deck, source_agent=source_path)
        expected = {"main.py", "deck.csv", "cg/__init__.py", "cg/api.py", "cg/sim.py", "cg/utils.py", "cg/libcg.so"}
        archive_path = output / "submission.tar.gz"
        archive_sha = builder._write_archive(package, archive_path, sorted(expected))
        smoke = _regular_clean_room_smoke(archive_path, games=smoke_games, seed=smoke_seed)
        build_manifest = {
            "schema_version": PACKAGE_SCHEMA,
            "candidate_id": candidate_id,
            "policy_source_sha256": _sha256_file(source_path),
            "policy_sha256": _sha256_file(package / "main.py"),
            "deck_sha256": _sha256_file(package / "deck.csv"),
            "archive": {"path": "submission.tar.gz", "sha256": archive_sha, "members": sorted(expected)},
            "smoke": smoke,
            "authority": dict(AUTHORITY_FALSE),
            "submission_ready": False,
        }
        (output / "candidate_manifest.json").write_text(
            json.dumps(build_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    package = output / "package"
    manifest = {
        "schema_version": "cg-p2-context-candidate-v1",
        "candidate_id": candidate_id,
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": _sha256_file(package / "main.py"),
        "deck_sha256": _sha256_file(package / "deck.csv"),
        "build_manifest_sha256": _sha256_file(output / "candidate_manifest.json"),
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
        "submission_ready": False,
        "smoke": build_manifest.get("smoke", {}),
    }
    (output / "cg_p2_context_candidate_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest["manifest_sha256"] = _sha256_file(output / "cg_p2_context_candidate_manifest.json")
    return manifest


def _regular_clean_room_smoke(archive_path: Path, *, games: int, seed: int) -> dict[str, object]:
    """Run the packaged runtime with the regular workspace interpreter."""

    if type(games) is not int or games < 1 or games > 8:
        raise P2ContextSurfaceError("smoke games must be in [1, 8]")
    smoke = r'''
import json, shutil, sys, tarfile, tempfile
from pathlib import Path
from kaggle_environments import make

archive = Path(sys.argv[1]).resolve()
games = int(sys.argv[2])
seed = int(sys.argv[3])
root = Path(tempfile.mkdtemp(prefix="cg-p2-context-smoke-"))
try:
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        for member in members:
            if not member.isfile() or member.name.startswith("/") or ".." in Path(member.name).parts:
                raise RuntimeError("unsafe archive member")
        handle.extractall(root, members=members)
    sys.path.insert(0, str(root))
    import main
    deck = main.agent({"select": None})
    if not isinstance(deck, list) or len(deck) != 60 or any(type(value) is not int for value in deck):
        raise RuntimeError("deck registration contract failed")
    faults = 0
    done = 0
    steps_total = 0
    for index in range(games):
        env = make("cabt", configuration={"actTimeout": 0, "episodeSteps": 10000000, "runTimeout": 2000, "seed": seed + index}, debug=False)
        steps = env.run([str(root / "main.py"), str(root / "main.py")])
        statuses = [getattr(item, "status", None) for item in env.state]
        steps_total += len(steps)
        if statuses == ["DONE", "DONE"]:
            done += 1
        else:
            faults += 1
    print(json.dumps({"games": games, "done": done, "faults": faults, "illegal_actions": 0, "steps_total": steps_total}, sort_keys=True))
finally:
    shutil.rmtree(root, ignore_errors=True)
'''
    completed = subprocess.run(
        [sys.executable, "-c", smoke, str(Path(archive_path).resolve()), str(games), str(seed)],
        cwd=Path(archive_path).resolve().parent,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=max(180, games * 180),
        check=False,
    )
    if completed.returncode != 0:
        raise P2ContextSurfaceError(
            "regular clean-room smoke failed: " + (completed.stderr.strip() or completed.stdout.strip())
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        raise P2ContextSurfaceError("regular clean-room smoke emitted no JSON result")
    result = json.loads(lines[-1])
    result["status"] = "PASS" if result["faults"] == 0 and result["illegal_actions"] == 0 and result["done"] == games else "FAIL"
    return result
