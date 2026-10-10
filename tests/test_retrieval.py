import copy
from datetime import date

import pytest
from conftest import FakeEmbedder

from precertly.ingest import ingest_bundle
from precertly.retrieval import hybrid_search, observations, or_tsquery, reciprocal_rank_fusion


def test_rrf_rewards_agreement_between_lists():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "d"]], k=60)
    assert [item for item, _ in fused] == ["b", "a", "d", "c"]
    scores = dict(fused)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] == pytest.approx(1 / 61)


def test_rrf_of_nothing_is_empty():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_or_tsquery_strips_operators_and_dedupes():
    assert or_tsquery("AHI >= 15 events/hour; AHI & (apnea)!") == "ahi | 15 | events | hour | apnea"
    assert or_tsquery("  ?!  ") is None


def _search(db, bundle, query, **options):
    async def work(session):
        result = await ingest_bundle(session, bundle, embedder=FakeEmbedder())
        return await hybrid_search(session, result.case_id, query, **options)

    return db(work)


def test_full_text_only_search_reaches_unembedded_observations(db, sleep_bundle):
    hits = _search(db, sleep_bundle, "body mass index", k=3)
    assert {h.resource_type for h in hits} == {"Observation"}
    assert all(h.vector_rank is None and h.fts_rank is not None for h in hits)


def test_hybrid_search_fuses_both_rankings(db, sleep_bundle):
    hits = _search(
        db,
        sleep_bundle,
        "apnea-hypopnea index from a sleep study (polysomnography)",
        embedder=FakeEmbedder(),
        k=5,
    )
    top = hits[0]
    assert top.ref == "DocumentReference/note-1#0"
    assert (top.fts_rank, top.vector_rank) == (1, 1)
    assert top.text.startswith("Patient reports loud snoring")
    assert (top.char_start, top.char_end) == (0, len(top.text))
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert "Procedure/proc-psg#0" in [h.ref for h in hits]


def test_vector_search_finds_chunks_full_text_misses(db, sleep_bundle):
    # "zzz" matches nothing in full text; vector search still returns its nearest chunks
    hits = _search(db, sleep_bundle, "zzz", embedder=FakeEmbedder(), k=20)
    assert hits and all(h.fts_rank is None for h in hits)
    assert {h.resource_type for h in hits} <= {
        "DocumentReference",
        "Condition",
        "Procedure",
        "MedicationRequest",
    }


def test_search_is_scoped_to_the_case_and_resource_types(db, sleep_bundle):
    other = copy.deepcopy(sleep_bundle)
    other["entry"][0]["resource"]["id"] = "pat-2"

    async def work(session):
        mine = await ingest_bundle(session, sleep_bundle, embedder=FakeEmbedder())
        await ingest_bundle(session, other, embedder=FakeEmbedder())
        everything = await hybrid_search(
            session, mine.case_id, "sleep apnea", embedder=FakeEmbedder(), k=100
        )
        conditions = await hybrid_search(
            session,
            mine.case_id,
            "sleep apnea",
            embedder=FakeEmbedder(),
            resource_types=["Condition"],
        )
        return mine.chunks, everything, conditions

    chunks, everything, conditions = db(work)
    assert len({h.chunk_id for h in everything}) == len(everything) <= chunks
    assert {h.resource_type for h in conditions} == {"Condition"}
    assert conditions[0].ref == "Condition/cond-osa#0"


def _observations(db, bundle, codes, **options):
    async def work(session):
        result = await ingest_bundle(session, bundle)
        return await observations(session, result.case_id, codes, **options)

    return db(work)


def test_observations_by_code_newest_first(db, sleep_bundle):
    hits = _observations(db, sleep_bundle, ["39156-5"])
    assert [h.ref for h in hits] == [
        "Observation/bmi-2026#0",
        "Observation/bmi-2025#0",
        "Observation/bmi-2024#0",
    ]
    assert (hits[0].value, hits[0].unit, hits[0].effective_date) == (
        36.2,
        "kg/m2",
        date(2026, 3, 1),
    )


def test_observations_date_range_is_inclusive(db, sleep_bundle):
    hits = _observations(
        db,
        sleep_bundle,
        ["http://loinc.org|39156-5"],
        date_from=date(2025, 3, 1),
        date_to=date(2026, 2, 28),
    )
    assert [h.ref for h in hits] == ["Observation/bmi-2025#0"]


def test_observations_accept_several_codes_and_ignore_unknown_ones(db, sleep_bundle):
    hits = _observations(db, sleep_bundle, ["29463-7", "http://loinc.org|39156-5", "nope"], limit=2)
    assert [h.ref for h in hits] == ["Observation/bmi-2026#0", "Observation/weight-2026#0"]
    assert _observations(db, sleep_bundle, ["http://snomed.info/sct|39156-5"]) == []
    assert _observations(db, sleep_bundle, []) == []
