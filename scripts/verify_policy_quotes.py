"""Check every policy quote against the governing document it cites.

uv run python scripts/verify_policy_quotes.py

Fetches each policy's governing NCD/LCD/article from the CMS Coverage API and confirms
that every criterion quote, and every not_covered entry, appears in it verbatim
(whitespace aside). Needs network, so it is run by hand, not in CI. Nothing fetched is
written to disk.
"""

from __future__ import annotations

import sys

from cms_coverage import fetch, squash

from precertly.policies import load_policies


def main() -> int:
    failures = 0
    for policy in load_policies().values():
        documents = {
            source.document: squash(" ".join(fetch(source.document, source.version).values()))
            for source in policy.sources
        }
        checked = 0
        for criterion in policy.criteria:
            text = documents[criterion.source_document]
            for part in criterion.quote_parts():
                checked += 1
                if squash(part) not in text:
                    failures += 1
                    print(f"NOT FOUND  {policy.id} / {criterion.id} in {criterion.source_document}")
                    print(f"           {part[:110]}")
        for entry in policy.not_covered:
            checked += 1
            if not any(squash(entry) in text for text in documents.values()):
                failures += 1
                print(f"NOT FOUND  {policy.id} / not_covered: {entry[:90]}")
        versions = ", ".join(f"{s.document} v{s.version}" for s in policy.sources)
        print(f"{policy.id}: {checked} passages checked against {versions}")
    print("all quotes found" if not failures else f"{failures} passage(s) not found")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
