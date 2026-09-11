"""cria listing_review_events

Um evento por aprovacao humana de imagens (individual ou em lote): quem,
modo, quantas imagens, quando. `approve_images`/`bulk_approve_images` gravam
a linha na MESMA transacao da aprovacao — a tabela existir e' pre-requisito
pra isso ser possivel. `review_seconds` e' NULL em modo `bulk` sempre (nunca
estima/reparte tempo); em modo `individual` guarda o valor recebido do
operador, que tambem pode ser None. A FK de `listing_id` tem `ON DELETE
CASCADE`: o evento apaga junto com o listing (`DELETE /listings/{id}`), como
os outros filhos de `listings`; a de `user_id` nao — apagar um usuario nao
pode sumir com auditoria.

Revision ID: b3e7a1c9d5f2
Revises: 9f4c2b7e1d63
Create Date: 2026-09-11 19:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'b3e7a1c9d5f2'
down_revision = '9f4c2b7e1d63'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'listing_review_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('listing_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('action', sa.String(length=40), nullable=False),
        sa.Column('mode', sa.String(length=20), nullable=False),
        sa.Column('approved_count', sa.Integer(), nullable=False),
        sa.Column('review_seconds', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_listing_review_events_listing_id', 'listing_review_events', ['listing_id']
    )


def downgrade() -> None:
    op.drop_index('ix_listing_review_events_listing_id', table_name='listing_review_events')
    op.drop_table('listing_review_events')
