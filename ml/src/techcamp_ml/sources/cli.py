"""`python -m techcamp_ml.sources fetch|parse [--source …]` (T3, docs/08 §Estructura de `ml/`)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from techcamp_ml.sources.pipeline import SOURCE_NAMES, fetch_sources, parse_sources, summarise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="techcamp_ml.sources", description=__doc__)
    parser.add_argument("command", choices=("fetch", "parse", "summary"))
    parser.add_argument(
        "--source",
        action="append",
        choices=SOURCE_NAMES,
        help="repeatable; default is every source",
    )
    args = parser.parse_args(argv)
    sources = args.source or list(SOURCE_NAMES)

    if args.command == "fetch":
        for source, count in fetch_sources(sources).items():
            print(f"fetch {source}: {count} raw copies")
        print("now run the parse step")
    elif args.command == "parse":
        for source, path in parse_sources(sources).items():
            print(f"parse {source}: {path}")
    else:
        print(summarise().to_string(index=False))
    return 0
