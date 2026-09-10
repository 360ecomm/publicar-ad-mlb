"""indices unicos parciais nos slots de capa e de ficha tecnica

Fecham a corrida de promote_cover / promote_specs: duas promocoes
simultaneas de alvos diferentes no mesmo anuncio travavam linhas disjuntas
(`with_for_update`) e terminavam ambas aprovadas no mesmo slot. Com o
indice, a segunda falha no commit (IntegrityError) e o service devolve 409.

`approved` no predicado: o pipeline grava `cover_ai` em sort_order 0 com
approved=False e uma regeracao pode deixar duas linhas assim; o invariante
e' "no maximo UMA APROVADA por slot". Dados de producao conferidos antes:
nenhum listing viola.

Revision ID: 3d8f1b2c9e47
Revises: 9c4d2e7f1a55
Create Date: 2026-09-10 16:30:00.000000
"""
from alembic import op

revision = '3d8f1b2c9e47'
down_revision = '9c4d2e7f1a55'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'uq_listing_images_cover_slot', 'listing_images', ['listing_id'], unique=True,
        postgresql_where="approved AND sort_order = 0 AND kind IN ('cover_deterministic', 'cover_ai')",
    )
    op.create_index(
        'uq_listing_images_specs_slot', 'listing_images', ['listing_id'], unique=True,
        postgresql_where="approved AND sort_order < 90 AND kind IN ('card_specs', 'specs_ai')",
    )


def downgrade() -> None:
    op.drop_index('uq_listing_images_specs_slot', table_name='listing_images')
    op.drop_index('uq_listing_images_cover_slot', table_name='listing_images')
