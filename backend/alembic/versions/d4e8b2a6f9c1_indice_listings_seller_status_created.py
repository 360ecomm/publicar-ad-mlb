"""indice composto ix_listings_seller_status_created em listings

A tela de fila de trabalho (work queue) consulta `listings` sempre no mesmo
formato: `WHERE seller_id = ? [AND status IN (...)] ORDER BY created_at DESC
LIMIT n`; a contagem por status (`status-counts`) e' `GROUP BY status` por
seller. Ruling do controlador: UM indice composto
`(seller_id, status, created_at DESC)`, em vez de tres separados:

- o composto atende o filtro por seller (+ status opcional) e, no caso de
  UM status so, ja entrega na ordem pedida (sem Sort no plano); com varios
  status, o planner faz scans por valor e um sort pequeno so sobre o
  recorte filtrado, nunca sobre a tabela inteira;
- a contagem `GROUP BY status` por seller e' index-only scan no prefixo
  `(seller_id, status)` do mesmo indice;
- `seller_id` sozinho ja fica coberto pelo prefixo do composto — nao
  precisa de indice proprio;
- indices separados por coluna forcariam o planner a um BitmapAnd pra
  combinar os filtros e nao ajudariam o ORDER BY (cada indice separado tem
  sua propria ordem, nenhuma delas e' a combinacao seller+status+created_at);
- `search` com `ilike '%x%'` nao usa btree (precisaria de extensao
  `pg_trgm`) — fora do escopo aqui, porque a busca roda sobre o recorte ja
  reduzido por seller+status, nao sobre a tabela toda.

`op.create_index` aqui roda um `CREATE INDEX` comum (sem `CONCURRENTLY`), que
toma SHARE lock na tabela e bloqueia escritas ate terminar de construir.
Aceitavel neste projeto porque `listings` tem ~10 linhas em producao. NAO
copiar este padrao pra uma tabela grande: la precisaria de `CONCURRENTLY`
rodando fora da transacao do Alembic (bloco autocommit), porque
`CONCURRENTLY` nao pode rodar dentro de uma transacao.

Revision ID: d4e8b2a6f9c1
Revises: c8d2f6a4e1b7
Create Date: 2026-09-11 21:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e8b2a6f9c1'
down_revision = 'c8d2f6a4e1b7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'ix_listings_seller_status_created', 'listings',
        ['seller_id', 'status', sa.text('created_at DESC')],
    )


def downgrade() -> None:
    op.drop_index('ix_listings_seller_status_created', table_name='listings')
