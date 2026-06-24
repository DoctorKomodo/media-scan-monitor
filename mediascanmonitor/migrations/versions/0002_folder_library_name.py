"""folder.library_name

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-24 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("folder", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("library_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("folder", schema=None) as batch_op:
        batch_op.drop_column("library_name")
