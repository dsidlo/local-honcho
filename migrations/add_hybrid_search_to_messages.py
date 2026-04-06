"""Add hybrid search columns and indexes to messages table."""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = 'add_hybrid_messages'
down_revision = None  # Adjust to current head
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add embedding column
    op.add_column('messages', sa.Column('embedding', Vector(1536), nullable=True))
    
    # Add content_tsv column
    op.add_column('messages', sa.Column(
        'content_tsv', 
        sa.dialects.postgresql.TSVECTOR, 
        sa.Computed("to_tsvector('english', coalesce(content, ''))", persisted=True), 
        nullable=True
    ))
    
    # Create HNSW index on embedding
    op.create_index(
        'ix_messages_embedding_hnsw',
        'messages',
        ['embedding'],
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
        postgresql_with={'m': 16, 'ef_construction': 64}
    )
    
    # Update GIN index to use content_tsv (if not already)
    op.drop_index('ix_messages_content_gin', table_name='messages', if_exists=True)
    op.create_index(
        'ix_messages_content_gin',
        'messages',
        ['content_tsv'],
        postgresql_using='gin'
    )


def downgrade() -> None:
    op.drop_index('ix_messages_embedding_hnsw', table_name='messages', if_exists=True)
    op.drop_index('ix_messages_content_gin', table_name='messages', if_exists=True)
    op.drop_column('messages', 'content_tsv')
    op.drop_column('messages', 'embedding')
