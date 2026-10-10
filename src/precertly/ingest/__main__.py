"""Ingest one bundle or a directory of bundles, one case per bundle.

uv run python -m precertly.ingest data/synthea/output/fhir --estimate   # no DB, no AWS
uv run python -m precertly.ingest data/synthea/output/fhir              # store, no new embeddings
uv run python -m precertly.ingest data/synthea/output/fhir --embed      # real Bedrock calls

Each run without --case-id creates new cases; pass --case-id to replace one case's chunks.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from precertly.db import get_engine, session_factory
from precertly.fhir import chunk_bundle, load_bundle
from precertly.ingest.pipeline import IngestError, estimate_embedding, ingest_bundle
from precertly.llm import BedrockEmbedder


async def _ingest(paths: list[Path], case_id: uuid.UUID | None, embed: bool) -> int:
    embedder = BedrockEmbedder() if embed else None
    sessions = session_factory()
    try:
        for path in paths:
            async with sessions() as session:
                try:
                    result = await ingest_bundle(
                        session, load_bundle(path), case_id=case_id, embedder=embedder
                    )
                except IngestError as error:
                    print(f"{path.name}: {error}", file=sys.stderr)
                    return 1
                await session.commit()
            print(
                f"{path.name}: case {result.case_id}  chunks={result.chunks}  "
                f"embedded={result.embedded} (new={result.embedding_calls}, "
                f"cached={result.cache_hits})  unembedded={result.unembedded}"
            )
    finally:
        await get_engine().dispose()
    if embedder is not None:
        tokens = sum(call.input_tokens for call in embedder.calls)
        retries = sum(call.attempts - 1 for call in embedder.calls)
        print(f"Bedrock: {len(embedder.calls)} embedding calls, {tokens} tokens, {retries} retries")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="a bundle .json file or a directory of them")
    parser.add_argument("--estimate", action="store_true", help="print the embedding job size")
    parser.add_argument("--embed", action="store_true", help="embed new texts with Bedrock")
    parser.add_argument("--case-id", type=uuid.UUID, help="replace this case's chunks")
    args = parser.parse_args()

    paths = sorted(args.path.glob("*.json")) if args.path.is_dir() else [args.path]
    if not paths:
        parser.error(f"no .json bundles found in {args.path}")
    if args.case_id and len(paths) != 1:
        parser.error("--case-id needs a single bundle file")

    if args.estimate:
        chunks = [chunk for path in paths for chunk in chunk_bundle(load_bundle(path))]
        estimate = estimate_embedding(chunks)
        print(
            f"{len(paths)} bundles, {len(chunks)} chunks, {estimate.chunks} to embed "
            f"({estimate.unique_texts} unique texts)\n"
            f"{estimate.characters} characters, about {estimate.approx_tokens} tokens "
            "at ~4 chars/token (before any cached vectors)"
        )
        return 0
    return asyncio.run(_ingest(paths, args.case_id, args.embed))


if __name__ == "__main__":
    sys.exit(main())
