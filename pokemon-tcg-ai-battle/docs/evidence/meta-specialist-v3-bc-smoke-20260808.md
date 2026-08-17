# Meta Specialist v3 BC smoke evidence

Date: 2026-08-08 (JST)

The full BC path now exists in `src/mage_ptcg/meta_specialist/bc_trainer_v3.py`.
It revalidates each local teacher record with the existing v2 parser, projects
the canonical model input through the v3 adapter, resolves the committed
semantic action to the corresponding stable-action candidate index, and splits
by `episode_id_hash:near_duplicate_id`. The training objective is a mean of
per-episode mean losses, so a long episode cannot dominate a short one.

Smoke command:

```text
PYTHONPATH=src python - <<'PY'
from mage_ptcg.meta_specialist.bc_trainer_v3 import load_bc_examples_from_teacher_records_v3, split_episode_groups_v3, train_bc_v3
from mage_ptcg.meta_specialist.neural_model_v3 import SpecialistModelV3
x = load_bc_examples_from_teacher_records_v3('runs/meta-specialist-teacher-records/t1-rocket', limit=64)
train, valid = split_episode_groups_v3(x, validation_fraction=0.25)
model = SpecialistModelV3(card_vocabulary_size=4096, hidden_dim=16, embedding_dim=16, seed=1)
result = train_bc_v3(model, train, valid, epochs=1, learning_rate=1e-3)
PY
```

Observed: 64 usable records, 64 episode/near-duplicate groups in this prefix,
48 train / 16 validation examples, best epoch 0, validation NLL 1.6676543,
and a checkpoint state containing 71 tensors.

This is a path/integrity smoke test, not the formal θ0. The plan still requires
all lanes, full teacher revalidation, episode-group and connected-component
split manifests, at least three initialization/shuffle seeds, validation early
stopping, critic warm-up/calibration, and a sealed checkpoint hash before any
learner comparison is considered valid.
