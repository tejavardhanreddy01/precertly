import pytest
from sqlalchemy import insert, inspect, select, text
from sqlalchemy.exc import IntegrityError

from precertly.db.models import Base, Case, ChartChunk, Criterion, Policy, Verdict


def test_migration_creates_every_model_table_and_column(db):
    async def columns(session):
        connection = await session.connection()
        return await connection.run_sync(
            lambda sync: {
                table: {c["name"] for c in inspect(sync).get_columns(table)}
                for table in inspect(sync).get_table_names()
            }
        )

    migrated = db(columns)
    for table in Base.metadata.sorted_tables:
        assert migrated[table.name] == {c.name for c in table.columns}, table.name


def test_chunk_gets_a_search_vector_and_an_optional_embedding(db):
    async def work(session):
        case = Case(patient_ref="Patient/p1", created_by="test")
        session.add(case)
        await session.flush()
        session.add(
            ChartChunk(
                case_id=case.id,
                fhir_resource_type="Condition",
                fhir_resource_id="c1",
                chunk_index=0,
                text="Obstructive sleep apnea",
                embedding=[0.0] * 1023 + [1.0],
            )
        )
        await session.flush()
        row = (
            await session.execute(
                select(ChartChunk.embedding, ChartChunk.codes).where(
                    text("tsv @@ to_tsquery('english', 'apnea')")
                )
            )
        ).one()
        return row

    embedding, codes = db(work)
    assert len(embedding) == 1024 and embedding[-1] == 1.0
    assert codes == []


def test_verdict_outcome_cannot_be_a_denial(db):
    async def work(session):
        policy = Policy(
            key="demo",
            version="1",
            title="Demo",
            document="NCD 0",
            source_url="https://example.com",
            procedure_codes=["00000"],
            variants=[],
        )
        case = Case(patient_ref="Patient/p1", created_by="test")
        session.add_all([policy, case])
        await session.flush()
        criterion = Criterion(policy_id=policy.id, key="a", text="A", kind="clinical")
        session.add(criterion)
        await session.flush()
        await session.execute(
            insert(Verdict).values(
                case_id=case.id,
                criterion_id=criterion.id,
                outcome="denied",
                rationale="x",
                model="m",
            )
        )

    with pytest.raises(IntegrityError):
        db(work)
