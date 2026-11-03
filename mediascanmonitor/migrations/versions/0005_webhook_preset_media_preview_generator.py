"""webhookpreset += media_preview_generator

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03 00:00:00.000000

SQLite stores the ``webhookpreset`` enum as a plain VARCHAR sized to its longest member (no
CHECK constraint), so adding a longer member is a column-type change: the batch rebuild
re-declares the column with the widened enum. Existing rows keep their values.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = sa.Enum("custom", "sonarr_radarr", name="webhookpreset")
_NEW = sa.Enum("custom", "sonarr_radarr", "media_preview_generator", name="webhookpreset")


def upgrade() -> None:
    with op.batch_alter_table("server", schema=None) as batch_op:
        batch_op.alter_column(
            "webhook_payload_preset",
            existing_type=_OLD,
            type_=_NEW,
            existing_nullable=False,
            existing_server_default="custom",
        )


def downgrade() -> None:
    # Rows on the removed member fall back to the operator's own template.
    op.execute(
        "UPDATE server SET webhook_payload_preset = 'custom' "
        "WHERE webhook_payload_preset = 'media_preview_generator'"
    )
    with op.batch_alter_table("server", schema=None) as batch_op:
        batch_op.alter_column(
            "webhook_payload_preset",
            existing_type=_NEW,
            type_=_OLD,
            existing_nullable=False,
            existing_server_default="custom",
        )
