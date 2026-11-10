"""reconcile author grant provenance branches

Revision ID: 2c5b6bb41e82
Revises: 9dc2d00ad972, 5d9362cfa5c1
Create Date: 2026-10-17 23:07:09.629028

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2c5b6bb41e82'
down_revision: Union[str, Sequence[str], None] = ('9dc2d00ad972', '5d9362cfa5c1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
