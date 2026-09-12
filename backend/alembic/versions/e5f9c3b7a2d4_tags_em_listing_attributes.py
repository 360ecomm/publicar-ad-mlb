"""tags em listing_attributes

Coluna `tags` (JSONB, nullable) com o dicionario de tags do ML inteiro,
como ele devolve (`hidden`, `read_only`, `fixed`, `required`...). O
`category_service` ja lia `attr["tags"]` e jogava fora tudo que nao fosse
`required`/`conditional_required` — inclusive `hidden` e `read_only`, que
sao o que permite esconder do operador os 67 (de 80) campos internos do ML
em MLB7863. `ListingAttribute.is_editable` deriva disso.

Um campo por tag exigiria migracao a cada tag nova do ML; o JSONB acompanha
sozinho (mesmo padrao de `allowed_values`).

Nada e' preenchido retroativamente: linhas antigas ficam NULL, e NULL e'
editavel (na duvida, mostrar).

Revision ID: e5f9c3b7a2d4
Revises: d4e8b2a6f9c1
Create Date: 2026-09-12 02:30:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'e5f9c3b7a2d4'
down_revision = 'd4e8b2a6f9c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'listing_attributes',
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('listing_attributes', 'tags')
