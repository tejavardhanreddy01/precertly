"""Ingest: FHIR bundle -> chunks -> chart_chunks rows, with embeddings where they help.

Only free-text-like resources are embedded (notes, conditions, procedures, medication
requests). Labs and vitals stay unembedded: they are found by code and date, or by
full-text search.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import ARRAY, String, any_, bindparam, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from precertly.db.models import Case, ChartChunk, Verdict
from precertly.fhir import Chunk, chunk_bundle

EMBEDDED_TYPES = frozenset({"DocumentReference", "Condition", "Procedure", "MedicationRequest"})

# Texts sent to the embedder between saves. A long run that fails keeps all finished batches.
EMBED_BATCH = 100

logger = logging.getLogger(__name__)

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
    unembedded: int  # embeddable chunks left without a vector
    # Set when the embedder failed part-way. Chunks and finished vectors are still stored;
    # re-ingest the same case to embed the rest.
    embedding_error: str | None = None


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
    commit_batches: bool = False,
) -> IngestResult:
    """Store a bundle's chunks under a case. The caller commits.

    With commit_batches the chunks and each finished batch of embeddings are committed as
    they complete, so an interrupted long run loses at most one batch.

    Ingest is idempotent per patient: without case_id the patient's existing case is
    reused, and a new case is created only for a patient that has none. Reusing a case
    replaces its chunks, unless it already has verdicts: their evidence points at the
    existing chunks.
    Vectors are reused by sha256 of the embedded text; only texts never seen before go
    to the embedder, and without an embedder those chunks are stored unembedded. If the
    embedder fails, what was embedded so far is kept and the error is reported in the
    result instead of raised.
    """
    patient_ref = _patient_ref(bundle)
    if case_id is None:
        existing = list(await session.scalars(select(Case).where(Case.patient_ref == patient_ref)))
        if len(existing) > 1:
            raise IngestError(
                f"{patient_ref} has {len(existing)} cases; pass case_id to choose one"
            )
        case = existing[0] if existing else None
    else:
        case = await session.get(Case, case_id)
        if case is None:
            raise IngestError(f"case {case_id} does not exist")
        if case.patient_ref != patient_ref:
            raise IngestError(
                f"case {case_id} belongs to {case.patient_ref}, but the bundle is {patient_ref}"
            )

    if case is None:
        case = Case(patient_ref=patient_ref, created_by=created_by)
        session.add(case)
        await session.flush()
    else:
        verdicts = await session.scalar(
            select(func.count()).select_from(Verdict).where(Verdict.case_id == case.id)
        )
        if verdicts:
            raise IngestError(
                f"case {case.id} already has {verdicts} verdict(s) citing its chunks; "
                "re-ingesting would invalidate that evidence."
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

    ids = [uuid.uuid4() for _ in chunks]
    await session.execute(delete(ChartChunk).where(ChartChunk.case_id == case.id))
    rows = [
        {
            "id": ids[i],
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
    if commit_batches:
        await session.commit()

    calls = 0
    error: str | None = None
    if embedder is not None:
        # sha -> the rows that need it; identical texts are embedded once.
        missing: dict[str, list[int]] = {}
        for i, sha in hashes.items():
            if sha not in vectors:
                missing.setdefault(sha, []).append(i)
        todo = list(missing)
        for start in range(0, len(todo), EMBED_BATCH):
            batch = todo[start : start + EMBED_BATCH]
            try:
                embedded = await embedder.embed_many(
                    [embedding_text(chunks[missing[sha][0]]) for sha in batch]
                )
            except Exception as failure:  # keep what is done; the caller decides what next
                error = f"{type(failure).__name__}: {failure}"
                break
            vectors.update(zip(batch, embedded, strict=True))
            calls += len(batch)
            await session.execute(
                update(ChartChunk),
                [
                    {"id": ids[i], "embedding": vectors[sha], "embedding_sha256": sha}
                    for sha in batch
                    for i in missing[sha]
                ],
            )
            if commit_batches:
                await session.commit()
            logger.info("embedded %d of %d new texts", calls, len(todo))

    stored = [sha for sha in hashes.values() if sha in vectors]
    return IngestResult(
        case_id=case.id,
        chunks=len(chunks),
        embedded=len(stored),
        embedding_calls=calls,
        cache_hits=sum(sha in cached for sha in stored),
        unembedded=len(hashes) - len(stored),
        embedding_error=error,
    )
