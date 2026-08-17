"""Reproduce the seven ``origin/dev`` meta opponents without editing the snapshot.

The snapshot's ``agents.generic_agent`` must win import resolution for the
opponent, whereas Rule v0 must first be imported from the current checkout.
The generated adapter captures Rule v0 and then removes the colliding package
from ``sys.modules``.  Every run starts in a fresh temporary directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
META_GLOB = "meta_*"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def adapter_source() -> str:
    return f'''import sys
from pathlib import Path
_ROOT = {str(ROOT)!r}
sys.path.insert(0, _ROOT)
from main import make_rule_agent, read_deck_csv
_agent = make_rule_agent(deck=read_deck_csv(Path(__file__).with_name("deck.csv")), seed=20260726)
for _name in list(sys.modules):
    if _name == "agents" or _name.startswith("agents."):
        del sys.modules[_name]
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
def agent(obs, configuration=None):
    del configuration
    return _agent(obs)
'''


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--deck", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=ROOT / ".venv/bin/python")
    parser.add_argument("--games", type=int, default=8)
    parser.add_argument("--max-steps", type=int, default=1000)
    args = parser.parse_args()
    opponents = sorted((args.snapshot / "opponents").glob(META_GLOB))
    if len(opponents) != 7:
        raise ValueError(f"expected 7 meta opponents, found {len(opponents)}")
    source = adapter_source()
    results = []
    with tempfile.TemporaryDirectory(prefix="ptcg-meta-repro-") as temporary:
        adapter = Path(temporary) / "rule_adapter"
        adapter.mkdir()
        adapter_main = adapter / "main.py"
        adapter_main.write_text(source, encoding="utf-8")
        shutil.copy2(args.deck, adapter / "deck.csv")
        for index, opponent in enumerate(opponents):
            command = [str(args.python), "-m", "bench.cli", "--agent-a", str(adapter), "--agent-b", str(opponent), "-n", str(args.games), "--seed", str(2026073800 + index * 10), "--run-name", f"meta-repro-{opponent.name}", "--max-steps", str(args.max_steps)]
            env = dict(os.environ, PYTHONPATH=".")
            run = subprocess.run(command, cwd=args.snapshot, env=env, text=True, capture_output=True, check=False)
            summary = {"games": None, "completed": None, "errors": None}
            for line in run.stdout.splitlines():
                if "games=" in line and "completed=" in line and "errors=" in line:
                    bits = line.replace("=", " ").split()
                    summary = {"games": int(bits[1]), "completed": int(bits[3]), "errors": int(bits[5])}
                    break
            results.append({
                "opponent_id": opponent.name, "snapshot": str(args.snapshot), "snapshot_ref": "origin/dev@a4b1f2407bb85ce79c76072f6df6e4f55ac463c5",
                "entrypoint": "main.py:agent", "import_name": "agents.generic_agent", "runtime_dependency": "snapshot bench + current Rule v0 closure",
                "source_hash": sha(opponent / "main.py"), "deck_hash": sha(opponent / "deck.csv"), "adapter_hash": sha(adapter_main),
                "command": command, "returncode": run.returncode, **summary,
                "status": "PASS" if run.returncode == 0 and summary == {"games": args.games, "completed": args.games, "errors": 0} else "FAIL",
            })
    atomic_json(args.output, {"schema": "dev-snapshot-meta-repro-v1", "results": results})
    failures = [row["opponent_id"] for row in results if row["status"] != "PASS"]
    print(json.dumps({"opponents": len(results), "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
