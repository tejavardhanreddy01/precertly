"""Evidence for a criterion from a case's chart chunks, via hybrid search."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from precertly.judgment.models import EvidenceChunk
from precertly.policies import Criterion
from precertly.retrieval import hybrid_search
from precertly.retrieval.search import QueryEmbedder


def retrieval_query(criterion: Criterion) -> str:
    """What to search the chart for: the criterion in plain words plus its policy text."""
    return f"{criterion.text} {criterion.quote}"


class ChartEvidence:
    """Top-k chart chunks per criterion for one ingested case."""

    def __init__(
        self,
        session: AsyncSession,
        case_id: uuid.UUID,
        embedder: QueryEmbedder | None,
        *,
        k: int = 8,
    ) -> None:
        self.session = session
        self.case_id = case_id
        self.embedder = embedder
        self.k = k

    async def for_criterion(self, criterion: Criterion) -> list[EvidenceChunk]:
        hits = await hybrid_search(
            self.session, self.case_id, retrieval_query(criterion), embedder=self.embedder, k=self.k
        )
        return [
            EvidenceChunk(id=hit.ref, text=hit.text, effective_date=hit.effective_date)
            for hit in hits
        ]
