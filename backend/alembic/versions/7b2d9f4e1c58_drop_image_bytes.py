"""listing_images.image_bytes — remove o blob; os bytes vivem no R2

Passo 2 de 2 da troca blob -> R2 (2026-09-10). SO rodar depois de:
  1. 5a1c7e2d9b04 aplicada (coluna asset_key existe);
  2. `scripts/migrate_image_bytes_to_r2.py` executado e conferido
     (toda linha com image_bytes tem asset_key);
  3. backup da tabela feito (pg_dump -t listing_images), hash anotado.

Downgrade recria a coluna VAZIA: os bytes nao voltam do R2 sozinhos.

Revision ID: 7b2d9f4e1c58
Revises: 5a1c7e2d9b04
Create Date: 2026-09-10 19:31:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '7b2d9f4e1c58'
down_revision = '5a1c7e2d9b04'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('listing_images', 'image_bytes')


def downgrade() -> None:
    op.add_column('listing_images', sa.Column('image_bytes', sa.LargeBinary(), nullable=True))
