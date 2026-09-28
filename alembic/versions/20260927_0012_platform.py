"""Add jobs, task_outbox; additive platform migration."""
from alembic import op
import sqlalchemy as sa

revision = '20260927_0012'
down_revision = '20260927_0011'
branch_labels = depends_on = None

def upgrade():
    op.execute("\nCREATE TABLE jobs (\n\tid UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tbatch_id UUID NOT NULL, \n\tkind VARCHAR(40) NOT NULL, \n\tscope VARCHAR(200) NOT NULL, \n\tidempotency_key VARCHAR(200) NOT NULL, \n\tinput_hash VARCHAR(64) NOT NULL, \n\tpayload JSONB NOT NULL, \n\tstatus VARCHAR(20) DEFAULT 'queued' NOT NULL, \n\tgeneration INTEGER DEFAULT '0' NOT NULL, \n\tretries INTEGER DEFAULT '0' NOT NULL, \n\tavailable_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tlease_until TIMESTAMP WITH TIME ZONE, \n\tresult JSONB, \n\terror_code VARCHAR(80), \n\terror_detail TEXT, \n\trequest_id VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_job_idempotency UNIQUE (user_id, scope, idempotency_key), \n\tCONSTRAINT ck_job_status CHECK (status IN ('queued','running','retry_wait','succeeded','failed','superseded','cancelled')), \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(batch_id) REFERENCES question_import_batches (id) ON DELETE CASCADE\n)\n\n")
    op.execute('CREATE INDEX ix_job_batch ON jobs (batch_id)')
    op.execute('CREATE INDEX ix_job_dispatch ON jobs (status, available_at)')
    op.execute('\nCREATE TABLE task_outbox (\n\tjob_id UUID NOT NULL, \n\tsent_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (job_id), \n\tFOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE\n)\n\n')


def downgrade():
    raise RuntimeError("Restore a paired backup; refusing to discard platform records")
