"""remove o write-back por seller (RF7): colunas write_* e r2_write_status/url_r2

Entrega das fotos no bucket proprio do seller, pos-publicacao: construida
em 2026-07-08, nunca teve credencial em nenhum seller (11 linhas
`skipped_no_config`, 0 `url_r2`). Removida em 2026-09-10 — ver
docs/superpowers/specs/2026-09-10-entrega-ao-seller-bucket-proprio-pausada.md.
Backup das duas tabelas antes de rodar (pg_dump), hash anotado.

Revision ID: 8e3a5c1d7f92
Revises: 7b2d9f4e1c58
Create Date: 2026-09-10 21:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '8e3a5c1d7f92'
down_revision = '7b2d9f4e1c58'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('seller_image_configs', 'write_bucket_name')
    op.drop_column('seller_image_configs', 'write_endpoint_url')
    op.drop_column('seller_image_configs', 'write_access_key_id_enc')
    op.drop_column('seller_image_configs', 'write_secret_access_key_enc')
    op.drop_column('listing_images', 'r2_write_status')
    op.drop_column('listing_images', 'url_r2')


def downgrade() -> None:
    op.add_column('listing_images', sa.Column('url_r2', sa.Text(), nullable=True))
    op.add_column('listing_images', sa.Column('r2_write_status', sa.String(length=20), nullable=True))
    op.add_column('seller_image_configs', sa.Column('write_secret_access_key_enc', sa.Text(), nullable=True))
    op.add_column('seller_image_configs', sa.Column('write_access_key_id_enc', sa.Text(), nullable=True))
    op.add_column('seller_image_configs', sa.Column('write_endpoint_url', sa.Text(), nullable=True))
    op.add_column('seller_image_configs', sa.Column('write_bucket_name', sa.String(length=200), nullable=True))
