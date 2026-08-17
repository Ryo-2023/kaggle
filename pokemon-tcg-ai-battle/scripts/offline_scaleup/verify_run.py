"""Run the bounded verifier and print only its summary JSON."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(); parser.add_argument("run_dir", type=Path); args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root), str(root / "src")))}
raise SystemExit(subprocess.call([sys.executable, "-m", "mage_ptcg.offline_scaleup", "verify-run", "--run-dir", str(args.run_dir)], cwd=root, env=env))
