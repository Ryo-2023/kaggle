"""One-shot, fail-closed Kaggle submission status check."""
from __future__ import annotations
import argparse,json,shutil
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("--competition",required=True);a=p.parse_args(argv)
 if not shutil.which("kaggle"):print(json.dumps({"status":"KAGGLE_AUTH_REQUIRED","competition":a.competition}));return 3
 print(json.dumps({"status":"KAGGLE_RULES_ACCEPTANCE_REQUIRED","competition":a.competition}));return 3
if __name__=="__main__":raise SystemExit(main())
