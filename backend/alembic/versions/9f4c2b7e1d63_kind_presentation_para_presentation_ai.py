"""kind `presentation` -> `presentation_ai` em listing_images (so dados)

As 5 posicoes do esquema sao geradas por IA e o sufixo `_ai` marca a origem;
`presentation` era a unica sem ele. O worker passou a gravar `presentation_ai`
(POSITION_KIND_PRESENTATION em image_tasks.py); esta revisao alinha as linhas
existentes. `cover_deterministic` (fallback sem IA) fica como esta, de
proposito. Sem mudanca de schema: `kind` e' String(20) e `presentation_ai`
tem 15 caracteres. Em producao, na data da criacao, 5 linhas.

Revision ID: 9f4c2b7e1d63
Revises: 8e3a5c1d7f92
Create Date: 2026-09-11 18:30:00.000000
"""
from alembic import op

revision = '9f4c2b7e1d63'
down_revision = '8e3a5c1d7f92'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE listing_images SET kind = 'presentation_ai' WHERE kind = 'presentation'")


def downgrade() -> None:
    op.execute("UPDATE listing_images SET kind = 'presentation' WHERE kind = 'presentation_ai'")
