"""add job heartbeat and attempts

Revision ID: 79aff3a11558
Revises: 9f7bb0d5e6d9
Create Date: 2026-10-23 02:33:50.845581

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '79aff3a11558'
down_revision: Union[str, Sequence[str], None] = '9f7bb0d5e6d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('jobs', sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'jobs', sa.Column('attempts', sa.Integer(), server_default='0', nullable=False)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('jobs', 'attempts')
    op.drop_column('jobs', 'heartbeat_at')
