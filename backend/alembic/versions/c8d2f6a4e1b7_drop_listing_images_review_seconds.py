"""drop listing_images.review_seconds

O tempo de revisao humana passou a viver so em `listing_review_events.
review_seconds` (Rev 1, migration b3e7a1c9d5f2), gravado na MESMA transacao
da aprovacao. A coluna homonima aqui em `listing_images` ficou redundante —
producao tem 5 linhas com valor nela; a decisao do dono do produto foi
descartar (sem migracao de dado): o historico so importa via evento.

Revision ID: c8d2f6a4e1b7
Revises: b3e7a1c9d5f2
Create Date: 2026-09-11 20:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'c8d2f6a4e1b7'
down_revision = 'b3e7a1c9d5f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('listing_images', 'review_seconds')


def downgrade() -> None:
    op.add_column('listing_images', sa.Column('review_seconds', sa.SmallInteger(), nullable=True))
