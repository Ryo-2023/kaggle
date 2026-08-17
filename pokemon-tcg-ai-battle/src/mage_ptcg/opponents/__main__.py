from .cli import main
from .lineage_v2 import main as lineage_v2_main
from .team_reference_v1 import main as team_reference_v1_main

import sys

if len(sys.argv) > 1 and sys.argv[1] == "lineage-v2":
    raise SystemExit(lineage_v2_main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == "team-reference-v1":
    raise SystemExit(team_reference_v1_main(sys.argv[2:]))
raise SystemExit(main())
