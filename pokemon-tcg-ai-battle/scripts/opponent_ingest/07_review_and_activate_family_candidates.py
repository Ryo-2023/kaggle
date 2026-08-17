#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from mage_ptcg.opponent_ingest.review import review_and_activate

parser=argparse.ArgumentParser()
parser.add_argument('--ingest-root', type=Path, required=True)
parser.add_argument('--diversity-root', type=Path, required=True)
parser.add_argument('--output-root', type=Path, required=True)
args=parser.parse_args()
print(json.dumps(review_and_activate(ingest_root=args.ingest_root, diversity_root=args.diversity_root, output_root=args.output_root), ensure_ascii=False, sort_keys=True))
