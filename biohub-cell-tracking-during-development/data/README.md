# Data directory

Competition data is intentionally not committed to Git.

Expected local layout:

```text
data/
├── train/
│   ├── <dataset>.zarr
│   └── <dataset>.geff
└── test/
    └── <dataset>.zarr
```

The official baseline expects OME-Zarr image volumes with dimensions `(T, Z, Y, X)` and GEFF tracking graphs for training labels.

## Kaggle credentials

Keep credentials on the host in `~/.kaggle/` or provide a Kaggle-supported environment variable. `docker-compose.yml` mounts `~/.kaggle` read-only at `/root/.kaggle`.

Never place credentials in this repository.

## Download

After joining the competition and accepting its rules, inspect/download data from inside the container with the Kaggle CLI. Start by listing competition files rather than blindly downloading the full dataset.
