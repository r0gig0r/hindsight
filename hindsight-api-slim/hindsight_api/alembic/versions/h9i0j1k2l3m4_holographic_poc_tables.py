"""holographic_poc_tables

Revision ID: h9i0j1k2l3m4
Revises: h3i4j5k6l7m8, c4x5y6z7a8b9
Create Date: 2026-05-09
"""

from collections.abc import Sequence

from alembic import context, op

from hindsight_api.alembic._dialect import run_for_dialect

revision: str = "h9i0j1k2l3m4"
down_revision: str | Sequence[str] | None = ("h3i4j5k6l7m8", "c4x5y6z7a8b9")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _get_schema_prefix() -> str:
    schema = context.config.get_main_option("target_schema")
    return f'"{schema}".' if schema else ""


def _pg_upgrade() -> None:
    schema = _get_schema_prefix()

    op.execute(f"ALTER TABLE {schema}memory_units ADD COLUMN IF NOT EXISTS trust_score REAL DEFAULT 0.5")
    op.execute(f"ALTER TABLE {schema}memory_units ADD COLUMN IF NOT EXISTS helpful_count INT DEFAULT 0")
    op.execute(f"ALTER TABLE {schema}memory_units ADD COLUMN IF NOT EXISTS unhelpful_count INT DEFAULT 0")
    op.execute(f"ALTER TABLE {schema}memory_units DROP CONSTRAINT IF EXISTS memory_units_trust_score_check")
    op.execute(
        f"ALTER TABLE {schema}memory_units ADD CONSTRAINT memory_units_trust_score_check "
        f"CHECK (trust_score IS NULL OR (trust_score >= 0.0 AND trust_score <= 1.0))"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}memory_feedback_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bank_id TEXT NOT NULL,
            memory_unit_id UUID NOT NULL REFERENCES {schema}memory_units(id) ON DELETE CASCADE,
            rating TEXT NOT NULL CHECK (rating IN ('helpful', 'unhelpful')),
            source TEXT NOT NULL CHECK (source IN ('user', 'agent', 'eval')),
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_feedback_events_bank_unit "
        f"ON {schema}memory_feedback_events (bank_id, memory_unit_id, created_at DESC)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}memory_conflicts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bank_id TEXT NOT NULL,
            unit_a_id UUID NOT NULL REFERENCES {schema}memory_units(id) ON DELETE CASCADE,
            unit_b_id UUID NOT NULL REFERENCES {schema}memory_units(id) ON DELETE CASCADE,
            shared_entity_ids UUID[] NOT NULL DEFAULT '{{}}'::uuid[],
            conflict_score REAL NOT NULL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'dismissed', 'resolved')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (bank_id, unit_a_id, unit_b_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_conflicts_bank_status "
        f"ON {schema}memory_conflicts (bank_id, status, conflict_score DESC)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}memory_structural_vectors (
            memory_unit_id UUID PRIMARY KEY REFERENCES {schema}memory_units(id) ON DELETE CASCADE,
            bank_id TEXT NOT NULL,
            roles JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            vector JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_structural_vectors_bank "
        f"ON {schema}memory_structural_vectors (bank_id)"
    )


def _pg_downgrade() -> None:
    schema = _get_schema_prefix()

    op.execute(f"DROP TABLE IF EXISTS {schema}memory_structural_vectors")
    op.execute(f"DROP TABLE IF EXISTS {schema}memory_conflicts")
    op.execute(f"DROP TABLE IF EXISTS {schema}memory_feedback_events")
    op.execute(f"ALTER TABLE {schema}memory_units DROP CONSTRAINT IF EXISTS memory_units_trust_score_check")
    op.execute(f"ALTER TABLE {schema}memory_units DROP COLUMN IF EXISTS unhelpful_count")
    op.execute(f"ALTER TABLE {schema}memory_units DROP COLUMN IF EXISTS helpful_count")
    op.execute(f"ALTER TABLE {schema}memory_units DROP COLUMN IF EXISTS trust_score")


def upgrade() -> None:
    run_for_dialect(pg=_pg_upgrade)


def downgrade() -> None:
    run_for_dialect(pg=_pg_downgrade)
