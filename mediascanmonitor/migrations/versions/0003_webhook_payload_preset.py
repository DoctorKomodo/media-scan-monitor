"""server.webhook_payload_preset

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-24 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("server", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "webhook_payload_preset",
                sa.Enum("custom", "sonarr_radarr", name="webhookpreset"),
                nullable=False,
                server_default="custom",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("server", schema=None) as batch_op:
        batch_op.drop_column("webhook_payload_preset")
