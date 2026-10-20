"""Write the recorded-mode static export, or check that it can be written.

    uv run python -m precertly.export [--out web/public/data]
    uv run python -m precertly.export --check

The files are the policies, the 29 labeled cases and the recorded run of the 11 facet cases,
for a static site: no database, no network, no model call. --check builds into a temporary
directory, runs every invariant, prints the size of every file and exits non-zero on the
first failure.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from precertly.export.build import DEFAULT_OUT, ExportError, build_files, write_files
from precertly.export.checks import CHECKS, CheckFailed, sizes


def _report(files: dict[str, str]) -> None:
    listing = sizes(files)
    for name, size in listing.items():
        print(f"{size:>10,}  {name}")
    print(f"{sum(listing.values()):>10,}  total, {len(listing)} files")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--check", action="store_true", help="build to a temp dir and check")
    args = parser.parse_args(argv)
    try:
        files = build_files()
        if args.check:
            with tempfile.TemporaryDirectory() as directory:
                write_files(files, Path(directory))
                for name, check in CHECKS:
                    check(files)
                    print(f"ok    {name}")
            _report(files)
            return 0
    except (ExportError, CheckFailed) as error:
        print(f"FAIL  {error}", file=sys.stderr)
        return 1
    write_files(files, args.out)
    _report(files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
