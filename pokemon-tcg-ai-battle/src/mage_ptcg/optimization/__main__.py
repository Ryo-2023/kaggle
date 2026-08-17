import sys

from .pilot import main as pilot_main
from .outcome import main as outcome_main
from .sparse import main as sparse_main
from .deck_specialist import main as deck_specialist_main
from .contextual_abstention import main as contextual_abstention_main
from .atomic_rules import main as atomic_rules_main
from .trajectory_v2 import main as trajectory_v2_main
from .semantic_trace import main as semantic_trace_main
from .semantic_failure_lab import main as semantic_failure_lab_main
from .atomic_policy_compiler import main as atomic_policy_compiler_main
from .validated_atomic_rules import main as validated_atomic_rules_main

if len(sys.argv) > 1 and sys.argv[1] == "outcome-pilot":
    raise SystemExit(outcome_main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == "sparse-pilot":
    raise SystemExit(sparse_main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == "deck-specialist":
    raise SystemExit(deck_specialist_main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == "contextual-abstention-v3":
    raise SystemExit(contextual_abstention_main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == "atomic-rule-lab":
    raise SystemExit(atomic_rules_main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == "trajectory-v2":
    raise SystemExit(trajectory_v2_main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == "semantic-trace-v2-1":
    raise SystemExit(semantic_trace_main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == "semantic-failure-lab-v3":
    raise SystemExit(semantic_failure_lab_main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == "atomic-policy-compiler-v1":
    raise SystemExit(atomic_policy_compiler_main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == "validated-atomic-rules-v1":
    raise SystemExit(validated_atomic_rules_main(sys.argv[2:]))
raise SystemExit(pilot_main())
