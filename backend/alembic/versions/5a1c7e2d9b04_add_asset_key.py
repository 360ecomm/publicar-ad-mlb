"""listing_images.asset_key — referencia dos bytes no bucket R2 de ativos

Passo 1 de 2 da troca blob -> R2 (2026-09-10). Adiciona a coluna, sem
mexer em `image_bytes`: o codigo novo ja grava so `asset_key`, e o script
`scripts/migrate_image_bytes_to_r2.py` copia os blobs existentes para o R2
preenchendo esta coluna. So depois disso roda a 7b2d9f4e1c58, que apaga
`image_bytes` (com backup antes).

Revision ID: 5a1c7e2d9b04
Revises: 3d8f1b2c9e47
Create Date: 2026-09-10 19:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '5a1c7e2d9b04'
down_revision = '3d8f1b2c9e47'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('listing_images', sa.Column('asset_key', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('listing_images', 'asset_key')
