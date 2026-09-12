"""indices nas FKs listing_id de listing_images, listing_titles e listing_jobs

Levantamento das 19 colunas de FK do esquema (2026-09-12): nenhuma tabela
filha de `listings` tinha indice util em `listing_id`, fora
`listing_attributes` (coberta pelo UNIQUE composto), `listing_descriptions`
(UNIQUE) e `listing_review_events` (indice proprio). Decisao de Daniel:
criar TRES, nao um por FK — indice custa escrita, e so entra onde ha
consulta real filtrando pela coluna.

- `ix_listing_images_listing_id`: os dois indices que ja existiam na coluna
  sao PARCIAIS (`uq_listing_images_cover_slot`, `uq_listing_images_specs_slot`,
  com WHERE de slot) e nao servem para "todas as imagens deste anuncio". A
  subconsulta correlata de `Listing.approved_image_count` (column_property)
  fazia Seq Scan de 16k linhas 200 vezes por pagina de listagem: 153 ms
  medidos com 2000 anuncios x 8 imagens; com o indice, 1,2 ms. Mais 11
  pontos de leitura por anuncio (detalhe, aprovacao, variantes, promocao).
- `ix_listing_titles_listing_id`: 5 pontos de leitura (detalhe,
  select_title, aprovacao de titulos em massa, retry com DELETE por
  listing_id); cada anuncio gera varios titulos, a tabela cresce mais que
  `listings`.
- `ix_listing_jobs_listing_id`: lida so no detalhe (`GET /listings/{id}`),
  mas e' a tela que o operador mais abre; a tabela so cresce (nada apaga
  job) e recebe INSERT num UNICO ponto do codigo, entao o custo de escrita
  do indice e' minimo. Por isso fica, e nao sai numa lista minima.

Ficam DE FORA, de proposito, as outras quatro FKs sem indice:
`listings.created_by`, `batch_imports.created_by`,
`listing_review_events.user_id` e `batch_import_rows.listing_id` — nenhuma
consulta do codigo filtra por elas (so existem como referencia ou pra
cascata de DELETE, que e' rara: apenas draft/failed). Indice ali so
custaria escrita. Nao "consertar" a ausencia sem uma consulta real que
justifique.

`op.create_index` aqui roda um `CREATE INDEX` comum (sem `CONCURRENTLY`),
que toma SHARE lock na tabela e bloqueia escritas ate terminar. Inofensivo
nas tabelas atuais (poucas centenas de linhas em producao). NAO copiar este
padrao pra uma tabela grande: la precisaria de `CONCURRENTLY` rodando fora
da transacao do Alembic (bloco autocommit), porque `CONCURRENTLY` nao pode
rodar dentro de uma transacao.

O DDL e' identico ao dos `__table_args__` dos tres models (`ListingImage`,
`ListingTitle`, `ListingJob`), que sao a fonte pra `create_all` nos testes.

Revision ID: f7b3e9c1d2a5
Revises: e5f9c3b7a2d4
Create Date: 2026-09-12 16:30:00.000000
"""
from alembic import op

revision = 'f7b3e9c1d2a5'
down_revision = 'e5f9c3b7a2d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_listing_images_listing_id', 'listing_images', ['listing_id'])
    op.create_index('ix_listing_titles_listing_id', 'listing_titles', ['listing_id'])
    op.create_index('ix_listing_jobs_listing_id', 'listing_jobs', ['listing_id'])


def downgrade() -> None:
    op.drop_index('ix_listing_jobs_listing_id', table_name='listing_jobs')
    op.drop_index('ix_listing_titles_listing_id', table_name='listing_titles')
    op.drop_index('ix_listing_images_listing_id', table_name='listing_images')
