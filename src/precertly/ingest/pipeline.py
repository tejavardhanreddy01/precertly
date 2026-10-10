"""Ingest: FHIR bundle -> chunks -> chart_chunks rows, with embeddings where they help.

Only free-text-like resources are embedded (notes, conditions, procedures, medication
requests). Labs and vitals stay unembedded: they are found by code and date, or by
full-text search.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import ARRAY, String, any_, bindparam, delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from precertly.db.models import Case, ChartChunk, Verdict
from precertly.fhir import Chunk, chunk_bundle

EMBEDDED_TYPES = frozenset({"DocumentReference", "Condition", "Procedure", "MedicationRequest"})

# Rough planning figure for English clinical text; the real count comes back from Bedrock.
CHARS_PER_TOKEN = 4


class Embedder(Protocol):
    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]: ...


class IngestError(Exception):
    pass


class IngestResult(BaseModel):
    case_id: uuid.UUID
    chunks: int
    embedded: int  # chunks stored with a vector
    embedding_calls: int  # texts sent to the embedder in this run
    cache_hits: int  # chunks whose vector was reused from the database
    unembedded: int  # embeddable chunks left without a vector (no embedder given)


class EmbeddingEstimate(BaseModel):
    chunks: int
    unique_texts: int
    characters: int
    approx_tokens: int


def embedding_text(chunk: Chunk) -> str:
    """What gets embedded: the chunk text with its type and date in front.

    The stored text stays the raw chunk text, so quotes still verify against it.
    """
    when = f" {chunk.effective_date.isoformat()}" if chunk.effective_date else ""
    return f"{chunk.resource_type}{when}: {chunk.text}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def estimate_embedding(chunks: Iterable[Chunk]) -> EmbeddingEstimate:
    """Size of the embedding job for these chunks, before any cache. No network."""
    texts = [embedding_text(c) for c in chunks if c.resource_type in EMBEDDED_TYPES]
    unique = set(texts)
    characters = sum(len(t) for t in unique)
    return EmbeddingEstimate(
        chunks=len(texts),
        unique_texts=len(unique),
        characters=characters,
        approx_tokens=characters // CHARS_PER_TOKEN,
    )


def _patient_ref(bundle: dict[str, Any]) -> str:
    for entry in bundle.get("entry") or []:
        resource = entry.get("resource") or {}
        if resource.get("resourceType") == "Patient" and resource.get("id"):
            return f"Patient/{resource['id']}"
    raise IngestError("bundle has no Patient resource")


async def _cached_embeddings(session: AsyncSession, hashes: set[str]) -> dict[str, list[float]]:
    if not hashes:
        return {}
    rows = await session.execute(
        select(ChartChunk.embedding_sha256, ChartChunk.embedding)
        .where(ChartChunk.embedding_sha256 == any_(bindparam("hashes", type_=ARRAY(String))))
        .where(ChartChunk.embedding.is_not(None)),
        {"hashes": sorted(hashes)},
    )
    return {sha: list(embedding) for sha, embedding in rows}


async def ingest_bundle(
    session: AsyncSession,
    bundle: dict[str, Any],
    *,
    case_id: uuid.UUID | None = None,
    embedder: Embedder | None = None,
    created_by: str = "ingest",
) -> IngestResult:
    """Store a bundle's chunks under a case. The caller commits.

    Without case_id a new case is created. With one, the case's chunks are replaced,
    unless it already has verdicts: their evidence points at the existing chunks.
    Vectors are reused by sha256 of the embedded text; only texts never seen before go
    to the embedder, and without an embedder those chunks are stored unembedded.
    """
    patient_ref = _patient_ref(bundle)
    if case_id is None:
        case = Case(patient_ref=patient_ref, created_by=created_by)
        session.add(case)
        await session.flush()
    else:
        case = await session.get(Case, case_id)
        if case is None:
            raise IngestError(f"case {case_id} does not exist")
        verdicts = await session.scalar(
            select(func.count()).select_from(Verdict).where(Verdict.case_id == case_id)
        )
        if verdicts:
            raise IngestError(
                f"case {case_id} already has {verdicts} verdict(s) citing its chunks; "
                "re-ingesting would invalidate that evidence. Ingest into a new case instead."
            )
        if case.patient_ref != patient_ref:
            raise IngestError(
                f"case {case_id} belongs to {case.patient_ref}, but the bundle is {patient_ref}"
            )

    chunks = chunk_bundle(bundle)
    hashes = {
        i: _sha256(embedding_text(chunk))
        for i, chunk in enumerate(chunks)
        if chunk.resource_type in EMBEDDED_TYPES
    }
    # Look the cache up before deleting this case's rows: they are part of it.
    vectors = await _cached_embeddings(session, set(hashes.values()))
    cached = set(vectors)
    calls = 0
    if embedder is not None:
        missing = {
            sha: embedding_text(chunks[i]) for i, sha in hashes.items() if sha not in vectors
        }
        calls = len(missing)
        embedded = await embedder.embed_many(list(missing.values()))
        vectors.update(zip(missing, embedded, strict=True))

    await session.execute(delete(ChartChunk).where(ChartChunk.case_id == case.id))
    rows = [
        {
            "case_id": case.id,
            "fhir_resource_type": chunk.resource_type,
            "fhir_resource_id": chunk.resource_id,
            "chunk_index": chunk.chunk_index,
            "effective_date": chunk.effective_date,
            "text": chunk.text,
            "char_start": chunk.char_start,
            "char_end": chunk.char_end,
            "codes": chunk.codes,
            "value": chunk.value,
            "unit": chunk.unit,
            "embedding": vectors.get(hashes.get(i, "")),
            "embedding_sha256": hashes[i] if hashes.get(i) in vectors else None,
        }
        for i, chunk in enumerate(chunks)
    ]
    if rows:
        await session.execute(insert(ChartChunk), rows)

    stored = [sha for sha in hashes.values() if sha in vectors]
    return IngestResult(
        case_id=case.id,
        chunks=len(chunks),
        embedded=len(stored),
        embedding_calls=calls,
        cache_hits=sum(sha in cached for sha in stored),
        unembedded=len(hashes) - len(stored),
    )
