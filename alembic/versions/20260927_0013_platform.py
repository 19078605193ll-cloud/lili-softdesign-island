"""Add learning_attempts, learning_attempt_parts, learning_attempt_assets; additive platform migration."""
from alembic import op
import sqlalchemy as sa

revision = '20260927_0013'
down_revision = '20260927_0012'
branch_labels = depends_on = None

def upgrade():
    op.execute('\nCREATE TABLE learning_attempts (\n\tid UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tpractice_question_id UUID NOT NULL, \n\tcontent_version INTEGER NOT NULL, \n\tidempotency_key VARCHAR(200) NOT NULL, \n\trequest_hash VARCHAR(64) NOT NULL, \n\tsnapshot JSONB NOT NULL, \n\tanswers JSONB NOT NULL, \n\tscore NUMERIC(8, 2) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_attempt_idempotency UNIQUE (user_id, idempotency_key), \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(practice_question_id) REFERENCES practice_questions (id) ON DELETE RESTRICT\n)\n\n')
    op.execute('CREATE INDEX ix_attempt_user_created ON learning_attempts (user_id, created_at, id)')
    op.execute('\nCREATE TABLE learning_attempt_parts (\n\tattempt_id UUID NOT NULL, \n\tquestion_id UUID NOT NULL, \n\tanswer VARCHAR(10) NOT NULL, \n\tcorrect BOOLEAN NOT NULL, \n\tscore NUMERIC(6, 2) NOT NULL, \n\tknowledge JSONB NOT NULL, \n\tPRIMARY KEY (attempt_id, question_id), \n\tFOREIGN KEY(attempt_id) REFERENCES learning_attempts (id) ON DELETE CASCADE, \n\tFOREIGN KEY(question_id) REFERENCES questions (id) ON DELETE RESTRICT\n)\n\n')
    op.execute('\nCREATE TABLE learning_attempt_assets (\n\tattempt_id UUID NOT NULL, \n\tasset_id UUID NOT NULL, \n\tPRIMARY KEY (attempt_id, asset_id), \n\tFOREIGN KEY(attempt_id) REFERENCES learning_attempts (id) ON DELETE CASCADE, \n\tFOREIGN KEY(asset_id) REFERENCES question_assets (id) ON DELETE RESTRICT\n)\n\n')


def downgrade():
    raise RuntimeError("Restore a paired backup; refusing to discard platform records")
