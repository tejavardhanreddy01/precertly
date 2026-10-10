import copy
import hashlib
from datetime import date

import pytest
from conftest import SLEEP_NOTE, FakeEmbedder
from sqlalchemy import func, select

from precertly.db.models import Case, ChartChunk, Criterion, Policy, Verdict
from precertly.fhir import Chunk, chunk_bundle
from precertly.ingest import (
    EMBEDDED_TYPES,
    IngestError,
    embedding_text,
    estimate_embedding,
    ingest_bundle,
)


async def _chunks(session, case_id):
    rows = await session.scalars(select(ChartChunk).where(ChartChunk.case_id == case_id))
    return {f"{r.fhir_resource_type}/{r.fhir_resource_id}#{r.chunk_index}": r for r in rows}


def test_embedding_text_prefixes_type_and_date():
    dated = Chunk(
        resource_type="Condition",
        resource_id="c",
        chunk_index=0,
        effective_date=date(2026, 2, 10),
        text="Condition: OSA",
    )
    assert embedding_text(dated) == "Condition 2026-02-10: Condition: OSA"
    undated = dated.model_copy(update={"effective_date": None})
    assert embedding_text(undated) == "Condition: Condition: OSA"


def test_estimate_counts_only_embeddable_chunks(sleep_bundle):
    estimate = estimate_embedding(chunk_bundle(sleep_bundle))
    assert (estimate.chunks, estimate.unique_texts) == (5, 5)
    assert estimate.approx_tokens == estimate.characters // 4


def test_ingest_stores_chunks_and_embeds_only_selected_types(db, sleep_bundle):
    embedder = FakeEmbedder()

    async def work(session):
        result = await ingest_bundle(session, sleep_bundle, embedder=embedder)
        case = await session.get(Case, result.case_id)
        return result, case, await _chunks(session, result.case_id)

    result, case, chunks = db(work)
    assert case.patient_ref == "Patient/pat-1"
    assert (result.chunks, result.embedded, result.embedding_calls) == (10, 5, 5)
    assert (result.cache_hits, result.unembedded) == (0, 0)
    assert "Claim/claim-1#0" not in chunks

    for chunk in chunks.values():
        assert (chunk.embedding is not None) == (chunk.fhir_resource_type in EMBEDDED_TYPES)
        assert (chunk.embedding_sha256 is not None) == (chunk.embedding is not None)

    note = chunks["DocumentReference/note-1#0"]
    assert note.text == SLEEP_NOTE  # raw text stored, prefix only in what was embedded
    assert (note.char_start, note.char_end) == (0, len(SLEEP_NOTE))
    embedded = f"DocumentReference 2026-02-10: {SLEEP_NOTE}"
    assert embedded in embedder.texts
    assert note.embedding_sha256 == hashlib.sha256(embedded.encode()).hexdigest()

    bmi = chunks["Observation/bmi-2026#0"]
    assert bmi.codes == ["http://loinc.org|39156-5"]
    assert (bmi.value, bmi.unit, bmi.effective_date) == (36.2, "kg/m2", date(2026, 3, 1))


def test_reingest_replaces_chunks_and_reuses_cached_vectors(db, sleep_bundle):
    changed = copy.deepcopy(sleep_bundle)
    changed["entry"][2]["resource"]["code"]["text"] = "Osteoarthritis of both knees"
    second_embedder = FakeEmbedder()

    async def work(session):
        first = await ingest_bundle(session, sleep_bundle, embedder=FakeEmbedder())
        second = await ingest_bundle(
            session, changed, case_id=first.case_id, embedder=second_embedder
        )
        cases = await session.scalar(select(func.count()).select_from(Case))
        return first, second, cases, await _chunks(session, first.case_id)

    first, second, cases, chunks = db(work)
    assert second.case_id == first.case_id and cases == 1
    assert len(chunks) == second.chunks == 10
    # only the edited condition goes back to the embedder
    assert (second.embedding_calls, second.cache_hits, second.embedded) == (1, 4, 5)
    assert len(second_embedder.texts) == 1 and "both knees" in second_embedder.texts[0]
    assert "both knees" in chunks["Condition/cond-knee#0"].text


def test_cache_is_shared_across_cases_and_works_without_an_embedder(db, sleep_bundle):
    async def work(session):
        cold = await ingest_bundle(session, sleep_bundle)
        await ingest_bundle(session, sleep_bundle, embedder=FakeEmbedder())
        warm = await ingest_bundle(session, sleep_bundle)
        return cold, warm

    cold, warm = db(work)
    assert (cold.embedded, cold.unembedded, cold.embedding_calls) == (0, 5, 0)
    assert (warm.embedded, warm.cache_hits, warm.unembedded) == (5, 5, 0)


def test_reingest_is_refused_once_the_case_has_verdicts(db, sleep_bundle):
    async def work(session):
        result = await ingest_bundle(session, sleep_bundle)
        policy = Policy(
            key="demo",
            version="1",
            title="Demo",
            document="NCD 0",
            source_url="https://example.com",
            procedure_codes=["00000"],
            variants=[],
        )
        session.add(policy)
        await session.flush()
        criterion = Criterion(policy_id=policy.id, key="a", text="A", kind="clinical")
        session.add(criterion)
        await session.flush()
        session.add(
            Verdict(
                case_id=result.case_id,
                criterion_id=criterion.id,
                outcome="met",
                rationale="r",
                model="m",
            )
        )
        await session.flush()
        with pytest.raises(IngestError, match="already has 1 verdict"):
            await ingest_bundle(session, sleep_bundle, case_id=result.case_id)
        return len(await _chunks(session, result.case_id))

    assert db(work) == 10  # the existing chunks are untouched


def test_reingest_rejects_another_patients_bundle(db, sleep_bundle):
    other = copy.deepcopy(sleep_bundle)
    other["entry"][0]["resource"]["id"] = "pat-2"

    async def work(session):
        result = await ingest_bundle(session, sleep_bundle)
        with pytest.raises(IngestError, match="belongs to Patient/pat-1"):
            await ingest_bundle(session, other, case_id=result.case_id)

    db(work)
