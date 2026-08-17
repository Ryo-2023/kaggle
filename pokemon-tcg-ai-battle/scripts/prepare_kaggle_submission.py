"""Build and verify a package without submitting it."""
from __future__ import annotations
import argparse,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("--agent",choices=("rule","student"),required=True);p.add_argument("--model-artifact");a=p.parse_args(argv);config=ROOT/"configs/kaggle"/("rule_v0.json" if a.agent=="rule" else "student_actual_v0.json")
 if a.agent=="student" and not a.model_artifact:print("KAGGLE_SUBMISSION_BLOCKED");return 2
 r=subprocess.run([sys.executable,str(ROOT/"scripts/build_kaggle_submission.py"),"--config",str(config)]);return r.returncode
if __name__=="__main__":raise SystemExit(main())
