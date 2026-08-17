"""Route legacy PPO commands and feature-flagged population/R2D3 commands."""
from __future__ import annotations

import sys

if len(sys.argv) > 1 and sys.argv[1] == "submitted-opponents":
    from .submitted_opponents_cli import main
    raise SystemExit(main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == "r2d3":
    from .r2d3.cli import main
    raise SystemExit(main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == "psro":
    from .psro_cli import main
    raise SystemExit(main(sys.argv[2:]))
from .cli import main
raise SystemExit(main())
