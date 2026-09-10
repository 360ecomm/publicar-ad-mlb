"""drop_image_engine_state

O subsistema de "motor de imagem" (texto-imagem com troca OpenAI/Gemini,
status pending_image_engine_confirmation, endpoint de confirmacao) foi
removido em 2026-09-10: so existia para servir o fallback texto-imagem,
substituido pelo standby `pending_raw_photos`. A tabela guardava 1 linha de
estado sem valor de negocio.

Revision ID: 9c4d2e7f1a55
Revises: 2f769b55c74e
Create Date: 2026-09-10 13:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '9c4d2e7f1a55'
down_revision = '2f769b55c74e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table('image_engine_state')


def downgrade() -> None:
    op.create_table(
        'image_engine_state',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('current_engine', sa.String(length=20), nullable=False, server_default='openai'),
        sa.Column('last_openai_error', sa.String(length=500), nullable=True),
        sa.Column('last_switch_to_openai_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.execute(
        "INSERT INTO image_engine_state (id, current_engine, updated_at) "
        "VALUES (gen_random_uuid(), 'openai', now())"
    )
