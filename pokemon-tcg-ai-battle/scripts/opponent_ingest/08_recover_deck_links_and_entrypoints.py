#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
from mage_ptcg.opponent_ingest.recovery import recover
p=argparse.ArgumentParser();p.add_argument('--ingest-root',type=Path,required=True);p.add_argument('--output-root',type=Path,required=True);p.add_argument('--repo',type=Path,default=Path.cwd());a=p.parse_args()
print(json.dumps(recover(repo=a.repo,ingest_root=a.ingest_root,output_root=a.output_root),sort_keys=True))
