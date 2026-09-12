"""indice unico parcial do placeholder de regeneracao de UMA posicao

`uq_listing_images_generating_slot (listing_id, sort_order) WHERE status = 'generating'`.

Trava contra duplo clique em "gerar de novo" numa posicao: o endpoint insere
um `ListingImage(status='generating')` e faz commit ANTES de enfileirar a
task; o segundo insert falha aqui (IntegrityError) e o service devolve 409
"Regeneracao em andamento na posicao N; aguarde." Mesma tecnica dos slots
de capa/ficha (3d8f1b2c9e47). `status='generating'` era o default da coluna
e nenhum caminho o gravava, entao nenhuma linha existente entra no
predicado. Ver docs/superpowers/specs/2026-09-12-regenerar-posicao.md.

DDL identico ao de `ListingImage.__table_args__` (fonte do `create_all` nos
testes).

Revision ID: a1d7c3e9f5b2
Revises: f7b3e9c1d2a5
Create Date: 2026-09-12 20:00:00.000000
"""
from alembic import op

revision = 'a1d7c3e9f5b2'
down_revision = 'f7b3e9c1d2a5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'uq_listing_images_generating_slot', 'listing_images', ['listing_id', 'sort_order'],
        unique=True, postgresql_where="status = 'generating'",
    )


def downgrade() -> None:
    op.drop_index('uq_listing_images_generating_slot', table_name='listing_images')
