from __future__ import annotations

import importlib.util
from pathlib import Path

from scripts.build_rule_v0_priority_attack_first_v1 import (
    PRIORITY_ATTACK_FIRST_V1,
    build_rule_v0_priority_attack_first_source_v1,
)


ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path):
    spec = importlib.util.spec_from_file_location("research_priority_attack_first", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_materialized_policy_uses_attack_first_priority_without_touching_production(tmp_path: Path) -> None:
    output = tmp_path / "main.py"
    manifest = build_rule_v0_priority_attack_first_source_v1(
        output_path=output,
        repo_root=ROOT,
    )

    assert output.is_file()
    assert manifest["variant"] == "rule-v0-main-priority-attack-first-v1"
    assert manifest["priority"] == list(PRIORITY_ATTACK_FIRST_V1)
    assert manifest["production_mutated"] is False
    assert manifest["policy_sha256"]

    module = _load(output)
    observation = {
        "select": {
            "type": 0,
            "option": [
                {"type": 7},
                {"type": 13},
                {"type": 8},
                {"type": 9},
                {"type": 10},
                {"type": 14},
            ],
            "minCount": 1,
            "maxCount": 1,
        }
    }
    assert module.agent(observation) == [1]


def test_materializer_refuses_to_clobber_existing_policy(tmp_path: Path) -> None:
    output = tmp_path / "main.py"
    output.write_text("sentinel\n", encoding="utf-8")
    try:
        build_rule_v0_priority_attack_first_source_v1(output_path=output, repo_root=ROOT)
    except FileExistsError:
        pass
    else:
        raise AssertionError("materializer must refuse to replace an existing file")
    assert output.read_text(encoding="utf-8") == "sentinel\n"
