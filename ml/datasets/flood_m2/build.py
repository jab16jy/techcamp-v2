#!/usr/bin/env python
"""Build the M2 dataset: municipio × mes, features from the shared module, hash included.

    uv run --locked --project ml python ml/datasets/flood_m2/build.py

It takes no network and asks for no manual step: the four source parquets that
`python -m techcamp_ml.sources parse` writes are its only inputs, and it refuses rather
than build from a partial cache (docs/08 §Reglas de gobierno, "reproducible o no existe").
The parquet lands in `ml/data/flood_m2/dataset/` (out of git) and the manifest with its
sha256 in `ml/datasets/flood_m2/`, next to this script and the data card.
"""

from techcamp_ml.datasets.flood_m2 import main

if __name__ == "__main__":
    raise SystemExit(main())
