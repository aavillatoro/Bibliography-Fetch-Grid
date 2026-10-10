"""add paper metadata

Revision ID: 7e56f68be2c6
Revises: 081942db9274
Create Date: 2026-09-23 16:52:56.567394

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e56f68be2c6'
down_revision: Union[str, Sequence[str], None] = '081942db9274'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('papers', sa.Column('work_type', sa.String(length=40), nullable=True))
    op.add_column('papers', sa.Column('venue', sa.Text(), nullable=True))
    op.add_column('papers', sa.Column('abstract', sa.Text(), nullable=True))
    op.add_column('papers', sa.Column('open_access_url', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('papers', 'open_access_url')
    op.drop_column('papers', 'abstract')
    op.drop_column('papers', 'venue')
    op.drop_column('papers', 'work_type')
