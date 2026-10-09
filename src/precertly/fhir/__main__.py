"""Chunk one bundle or a directory of bundles and print a summary.

uv run python -m precertly.fhir data/synthea/output/fhir
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from precertly.fhir.chunks import SUPPORTED_TYPES, chunk_bundle, load_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="a bundle .json file or a directory of them")
    args = parser.parse_args()

    paths = sorted(args.path.glob("*.json")) if args.path.is_dir() else [args.path]
    if not paths:
        parser.error(f"no .json bundles found in {args.path}")

    chunks_by_type: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    note_chunks = 0
    for path in paths:
        bundle = load_bundle(path)
        for chunk in chunk_bundle(bundle):
            chunks_by_type[chunk.resource_type] += 1
            note_chunks += chunk.char_start is not None
        for entry in bundle.get("entry") or []:
            kind = (entry.get("resource") or {}).get("resourceType", "?")
            if kind not in SUPPORTED_TYPES:
                skipped[kind] += 1

    total = sum(chunks_by_type.values())
    print(f"{len(paths)} bundles, {total} chunks ({note_chunks} from decoded notes)")
    for kind, count in chunks_by_type.most_common():
        print(f"  {kind:<26}{count:>8}")
    print(f"skipped {sum(skipped.values())} resources")
    for kind, count in skipped.most_common():
        print(f"  {kind:<26}{count:>8}")


if __name__ == "__main__":
    main()
