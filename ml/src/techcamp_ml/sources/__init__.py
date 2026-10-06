"""M2 dataset sources: download, cache, parse. Docs: docs/08 §Fuentes de datos de M2.

Nothing here talks to the network except `fetching.fetch`, and the parsers are pure:
they turn a saved raw copy into a tidy frame, so a dataset is rebuilt offline from
`ml/.cache/raw` alone ("Reproducible o no existe", docs/08 §Reglas de gobierno).
"""

from techcamp_ml.sources.cache import read_manifest, read_raw, save_raw
from techcamp_ml.sources.layout import DEFAULT_LAYOUT, Layout

__all__ = [
    "DEFAULT_LAYOUT",
    "Layout",
    "read_manifest",
    "read_raw",
    "save_raw",
]
