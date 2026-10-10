"""Evidence retrieval over one case's chart chunks.

hybrid_search: Postgres full-text and pgvector cosine search, merged with reciprocal rank
fusion. Full-text covers every chunk; vector search only the embedded ones (notes,
conditions, procedures, medication requests).

observations: structured lookup of labs and vitals by code and date range, no text search.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Hashable, Sequence
from datetime import date
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import ARRAY, Select, Text, any_, bindparam, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from precertly.db.models import ChartChunk

RRF_K = 60  # the constant from the original RRF paper; damps the weight of top ranks
MAX_QUERY_TERMS = 64


class QueryEmbedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    ref: str  # "{resource_type}/{resource_id}#{chunk_index}", same as Chunk.id
    resource_type: str
    resource_id: str
    effective_date: date | None
    text: str
    char_start: int | None
    char_end: int | None
    score: float
    fts_rank: int | None  # 1-based rank in each list, None if absent from it
    vector_rank: int | None


class ObservationHit(BaseModel):
    chunk_id: uuid.UUID
    ref: str
    effective_date: date | None
    codes: list[str]
    value: float | None
    unit: str | None
    text: str


def reciprocal_rank_fusion[T: Hashable](
    rankings: Sequence[Sequence[T]], k: int = RRF_K
) -> list[tuple[T, float]]:
    """Fuse ranked lists: score(item) = sum over lists of 1 / (k + rank), best first.

    Ties keep the order of first appearance, so the result is deterministic.
    """
    scores: dict[T, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: -pair[1])


def or_tsquery(query: str) -> str | None:
    """'a | b | c' for to_tsquery. Terms are OR-ed so a long criterion sentence still
    matches chunks holding only part of the evidence; ts_rank_cd orders them."""
    terms = list(dict.fromkeys(re.findall(r"[a-z0-9]+", query.lower())))
    return " | ".join(terms[:MAX_QUERY_TERMS]) or None


def _ref(chunk: ChartChunk) -> str:
    return f"{chunk.fhir_resource_type}/{chunk.fhir_resource_id}#{chunk.chunk_index}"


def _scoped(statement: Select, case_id: uuid.UUID, resource_types: Sequence[str] | None) -> Select:
    statement = statement.where(ChartChunk.case_id == case_id)
    if resource_types:
        statement = statement.where(ChartChunk.fhir_resource_type.in_(resource_types))
    return statement


async def hybrid_search(
    session: AsyncSession,
    case_id: uuid.UUID,
    query: str,
    *,
    embedder: QueryEmbedder | None = None,
    k: int = 10,
    candidates: int = 50,
    resource_types: Sequence[str] | None = None,
) -> list[SearchHit]:
    """Top-k chunks of a case for a query. Without an embedder this is full-text only."""
    rankings: list[list[uuid.UUID]] = []

    fts_ids: list[uuid.UUID] = []
    if tsquery := or_tsquery(query):
        parsed = func.to_tsquery("english", tsquery)
        statement = (
            select(ChartChunk.id)
            .where(ChartChunk.tsv.op("@@")(parsed))
            .order_by(func.ts_rank_cd(ChartChunk.tsv, parsed).desc(), ChartChunk.id)
            .limit(candidates)
        )
        fts_ids = list(await session.scalars(_scoped(statement, case_id, resource_types)))
        rankings.append(fts_ids)

    vector_ids: list[uuid.UUID] = []
    if embedder is not None:
        vector = await embedder.embed(query)
        # Keep scanning the HNSW index until enough rows pass the case filter; without
        # this an approximate scan can return fewer than `candidates` for a small case.
        await session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
        statement = (
            select(ChartChunk.id)
            .where(ChartChunk.embedding.is_not(None))
            .order_by(ChartChunk.embedding.cosine_distance(vector), ChartChunk.id)
            .limit(candidates)
        )
        vector_ids = list(await session.scalars(_scoped(statement, case_id, resource_types)))
        rankings.append(vector_ids)

    fused = reciprocal_rank_fusion(rankings)[:k]
    if not fused:
        return []
    rows = await session.scalars(
        select(ChartChunk).where(ChartChunk.id.in_([chunk_id for chunk_id, _ in fused]))
    )
    chunks = {chunk.id: chunk for chunk in rows}
    fts_rank = {chunk_id: rank for rank, chunk_id in enumerate(fts_ids, start=1)}
    vector_rank = {chunk_id: rank for rank, chunk_id in enumerate(vector_ids, start=1)}
    return [
        SearchHit(
            chunk_id=chunk_id,
            ref=_ref(chunks[chunk_id]),
            resource_type=chunks[chunk_id].fhir_resource_type,
            resource_id=chunks[chunk_id].fhir_resource_id,
            effective_date=chunks[chunk_id].effective_date,
            text=chunks[chunk_id].text,
            char_start=chunks[chunk_id].char_start,
            char_end=chunks[chunk_id].char_end,
            score=score,
            fts_rank=fts_rank.get(chunk_id),
            vector_rank=vector_rank.get(chunk_id),
        )
        for chunk_id, score in fused
    ]


async def observations(
    session: AsyncSession,
    case_id: uuid.UUID,
    codes: Sequence[str],
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
) -> list[ObservationHit]:
    """Observations of a case with any of the codes, newest first.

    A code is either a full "system|code" token or a bare code ("39156-5"), which matches
    in any system. Date bounds are inclusive; undated observations only match when no
    bound is given.
    """
    full = [code for code in codes if "|" in code]
    bare = [code for code in codes if "|" not in code]
    code = func.unnest(ChartChunk.codes).column_valued("code")
    matches = []
    if full:
        matches.append(ChartChunk.codes.overlap(bindparam("full", full, type_=ARRAY(Text))))
    if bare:
        matches.append(
            exists(
                select(1).where(
                    func.split_part(code, "|", 2) == any_(bindparam("bare", bare, ARRAY(Text)))
                )
            )
        )
    if not matches:
        return []

    statement = (
        select(ChartChunk)
        .where(ChartChunk.case_id == case_id, ChartChunk.fhir_resource_type == "Observation")
        .where(or_(*matches))
        .order_by(ChartChunk.effective_date.desc().nulls_last(), ChartChunk.fhir_resource_id)
        .limit(limit)
    )
    if date_from is not None:
        statement = statement.where(ChartChunk.effective_date >= date_from)
    if date_to is not None:
        statement = statement.where(ChartChunk.effective_date <= date_to)
    return [
        ObservationHit(
            chunk_id=chunk.id,
            ref=_ref(chunk),
            effective_date=chunk.effective_date,
            codes=chunk.codes,
            value=chunk.value,
            unit=chunk.unit,
            text=chunk.text,
        )
        for chunk in await session.scalars(statement)
    ]
