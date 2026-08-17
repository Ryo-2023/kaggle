from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run_cg_alternating_runtime_v1.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package(tmp_path: Path, name: str, *, deck_path: Path, policy: str) -> Path:
    root = tmp_path / name / "package"
    root.mkdir(parents=True)
    (root / "deck.csv").write_bytes(deck_path.read_bytes())
    (root / "main.py").write_text(policy, encoding="utf-8")
    archive = root.parent / "submission.tar.gz"
    archive.write_bytes(f"archive-{name}".encode())
    manifest = root.parent / "candidate_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "candidate_id": name,
                "deck_sha256": _sha(root / "deck.csv"),
                "archive": {"path": "submission.tar.gz", "sha256": _sha(archive)},
                "policy_source_sha256": _sha(root / "main.py"),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def test_cli_dry_run_materializes_cg_alternating_plan(tmp_path: Path) -> None:
    hilda_deck = ROOT / "runs/final-sprint-autonomous/root-cg-dusk-hilda-package-v2-20260814/package/deck.csv"
    deck_control = _package(tmp_path, "deck-control", deck_path=ROOT / "deck.csv", policy="def agent(obs): return []\n")
    deck_candidate = _package(tmp_path, "deck-candidate", deck_path=hilda_deck, policy="def agent(obs): return []\n")
    policy_control = _package(tmp_path, "policy-control", deck_path=hilda_deck, policy="def agent(obs): return [0]\n")
    policy_candidate = _package(tmp_path, "policy-candidate", deck_path=hilda_deck, policy="def agent(obs): return [1]\n")
    output = tmp_path / "iteration"
    command = [
        sys.executable,
        str(SCRIPT),
        "--deck-candidate-package",
        str(deck_candidate),
        "--deck-control-package",
        str(deck_control),
        "--policy-candidate-package",
        str(policy_candidate),
        "--policy-control-package",
        str(policy_control),
        "--config",
        str(ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"),
        "--output",
        str(output),
        "--base-seed",
        "40400000",
    ]
    result = subprocess.run(command, cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": ".:src"}, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "DRY_RUN"
    assert payload["policy_phase_started"] is False
    assert (output / "iteration.json").is_file()
    assert (output / "policy-fixed-short/manifest.json").is_file()
    assert not (output / "policy-fixed-short/evaluation").exists()

