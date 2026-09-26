"""widen ck_reading_quality to the bit-flag combinations

`reading.quality` holds two independent signals as flags (1 ts corregido,
2 fuera de rango, 3 ambos — docs/03-modelo-datos.md:174), so `3` is a valid
stored value and the constraint has to allow it.

Revision ID: a3f1c7d92b40
Revises: 231a40930eb5
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a3f1c7d92b40'
down_revision: Union[str, Sequence[str], None] = '231a40930eb5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('ALTER TABLE reading DROP CONSTRAINT ck_reading_quality')
    op.execute('ALTER TABLE reading ADD CONSTRAINT ck_reading_quality CHECK (quality in (0,1,2,3))')


def downgrade() -> None:
    op.execute('ALTER TABLE reading DROP CONSTRAINT ck_reading_quality')
    # `3` is both signals at once; the previous representation had one value
    # per signal and kept only the stronger one, so fold `3` into `2` before
    # narrowing the constraint, or the ALTER fails on existing rows.
    op.execute('UPDATE reading SET quality = 2 WHERE quality = 3')
    op.execute('ALTER TABLE reading ADD CONSTRAINT ck_reading_quality CHECK (quality in (0,1,2))')
