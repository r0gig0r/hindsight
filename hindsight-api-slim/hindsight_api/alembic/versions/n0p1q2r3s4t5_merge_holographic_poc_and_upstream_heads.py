"""Merge holographic POC and upstream migration heads

Revision ID: n0p1q2r3s4t5
Revises: h9i0j1k2l3m4, m3rg3h3ad5f6
Create Date: 2026-05-09
"""

from collections.abc import Sequence

from hindsight_api.alembic._dialect import run_for_dialect

revision: str = "n0p1q2r3s4t5"
down_revision: tuple[str, ...] = ("h9i0j1k2l3m4", "m3rg3h3ad5f6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _pg_upgrade() -> None:
    pass


def _pg_downgrade() -> None:
    pass


def upgrade() -> None:
    run_for_dialect(pg=_pg_upgrade)


def downgrade() -> None:
    run_for_dialect(pg=_pg_downgrade)
