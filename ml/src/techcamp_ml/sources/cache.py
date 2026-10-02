"""The raw cache and its provenance manifest.

Every downloaded byte lands in `ml/.cache/raw/<source>/<file>` with a manifest row
holding the URL, the fetch date and the sha256, so a dataset can be rebuilt (and a
reviewer can tell what was actually downloaded) without the network.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import pandas as pd

from techcamp_ml.sources.layout import Layout

MANIFEST_COLUMNS = ("source", "file", "url", "fetched_at", "sha256", "bytes")


@dataclass(frozen=True, slots=True)
class RawEntry:
    """One row of `MANIFEST.tsv`."""

    source: str
    file: str
    url: str
    fetched_at: str
    sha256: str
    bytes: int


def sha256_of(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def save_raw(
    layout: Layout,
    source: str,
    name: str,
    url: str,
    payload: bytes,
    *,
    fetched_at: datetime | None = None,
) -> RawEntry:
    """Save one raw copy and record (or refresh) its manifest row."""
    path = layout.raw_copy(source, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)

    stamp = (fetched_at or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = RawEntry(source, name, url, stamp, sha256_of(payload), len(payload))
    manifest = read_manifest(layout)
    kept = manifest[~((manifest["source"] == source) & (manifest["file"] == name))]
    layout.manifest.parent.mkdir(parents=True, exist_ok=True)
    pd.concat([kept, pd.DataFrame([asdict(entry)])], ignore_index=True).to_csv(
        layout.manifest, sep="\t", index=False, columns=list(MANIFEST_COLUMNS)
    )
    return entry


def read_raw(layout: Layout, source: str, name: str) -> bytes:
    """The bytes of a saved raw copy; missing means "fetch it", not "empty"."""
    path = layout.raw_copy(source, name)
    if not path.exists():
        raise FileNotFoundError(
            f"raw copy {source}/{name} is not in the cache; run the fetch step first"
        )
    return path.read_bytes()


def read_manifest(layout: Layout) -> pd.DataFrame:
    """The manifest as a frame, empty when nothing was ever downloaded."""
    if not layout.manifest.exists():
        return pd.DataFrame(columns=list(MANIFEST_COLUMNS))
    frame = pd.read_csv(layout.manifest, sep="\t", dtype=str, keep_default_na=False)
    return frame[list(MANIFEST_COLUMNS)]


def cached_files(layout: Layout, source: str) -> list[str]:
    """The raw file names already saved for a source, sorted."""
    directory = layout.raw / source
    if not directory.is_dir():
        return []
    return sorted(path.name for path in directory.iterdir() if path.is_file())
