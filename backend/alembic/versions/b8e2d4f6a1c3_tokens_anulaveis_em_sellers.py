"""tokens do seller anulaveis: desconectar apaga so o token, mantem o historico

`sellers.access_token_enc` e `sellers.refresh_token_enc` passam de NOT NULL
para NULL permitido. Decisao do Daniel (2026-09-13): desconectar uma conta do
Mercado Livre limpa os dois tokens e marca `is_active = False`; a linha do
seller e tudo que aponta pra ela (listings, products, listing_images,
listing_review_events, user_seller_access) ficam intactos. Gravar string
vazia dispensaria esta migracao, mas codificaria "sem token" num campo que
declara nao aceitar ausencia — regra nao escrita que o proximo leitor nao
tem como saber.

O downgrade preenche '' nas linhas com NULL antes de devolver o NOT NULL:
reverter a migracao nao pode falhar por causa de uma conta desconectada nem
apagar seller nenhum.

Revision ID: b8e2d4f6a1c3
Revises: a1d7c3e9f5b2
Create Date: 2026-09-13 21:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'b8e2d4f6a1c3'
down_revision = 'a1d7c3e9f5b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('sellers', 'access_token_enc', existing_type=sa.String(), nullable=True)
    op.alter_column('sellers', 'refresh_token_enc', existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE sellers SET access_token_enc = '' WHERE access_token_enc IS NULL")
    op.execute("UPDATE sellers SET refresh_token_enc = '' WHERE refresh_token_enc IS NULL")
    op.alter_column('sellers', 'access_token_enc', existing_type=sa.String(), nullable=False)
    op.alter_column('sellers', 'refresh_token_enc', existing_type=sa.String(), nullable=False)
