"""Where the raw cache, its manifest and the built parquets live.

`ml/.cache/` and `ml/data/` are gitignored build products: the raw copies are the
provenance of the dataset (URL, fetch date, sha256) and the parquets are rebuilt
from them, so neither belongs in git (docs/08 §Reglas de gobierno).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ML_ROOT = Path(__file__).resolve().parents[3]
"""The `ml/` project this package lives in (src/techcamp_ml/sources -> ml)."""


@dataclass(frozen=True, slots=True)
class Layout:
    """The three directories a build touches, rooted at one `ml/` directory."""

    ml_root: Path = ML_ROOT

    @property
    def raw(self) -> Path:
        """`ml/.cache/raw/<source>/`: one file per request, byte for byte."""
        return self.ml_root / ".cache" / "raw"

    @property
    def manifest(self) -> Path:
        return self.raw / "MANIFEST.tsv"

    @property
    def data(self) -> Path:
        """`ml/data/flood_m2/sources/`: one parquet per source."""
        return self.ml_root / "data" / "flood_m2" / "sources"

    def raw_copy(self, source: str, name: str) -> Path:
        return self.raw / source / name


DEFAULT_LAYOUT = Layout()


def write_parquet(layout: Layout, name: str, frame: pd.DataFrame) -> Path:
    """Write one tidy parquet and return its path."""
    path = layout.data / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path
