"""Add users, roles, user_roles, audit_events; additive platform migration."""
from alembic import op
import sqlalchemy as sa

revision = '20260927_0010'
down_revision = '20260926_0009'
branch_labels = depends_on = None

def upgrade():
    op.execute("\nCREATE TABLE users (\n\tid UUID NOT NULL, \n\tusername VARCHAR(100) NOT NULL, \n\tpassword_hash TEXT NOT NULL, \n\tactive BOOLEAN DEFAULT 'true' NOT NULL, \n\tauth_version INTEGER DEFAULT '1' NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (username)\n)\n\n")
    op.execute('\nCREATE TABLE roles (\n\tname VARCHAR(40) NOT NULL, \n\tPRIMARY KEY (name)\n)\n\n')
    op.execute('\nCREATE TABLE user_roles (\n\tuser_id UUID NOT NULL, \n\trole VARCHAR(40) NOT NULL, \n\tPRIMARY KEY (user_id, role), \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, \n\tFOREIGN KEY(role) REFERENCES roles (name) ON DELETE RESTRICT\n)\n\n')
    op.execute('\nCREATE TABLE audit_events (\n\tid UUID NOT NULL, \n\tuser_id UUID, \n\taction VARCHAR(100) NOT NULL, \n\tobject_id VARCHAR(200) NOT NULL, \n\trequest_id VARCHAR(64) NOT NULL, \n\tsummary JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT\n)\n\n')
    op.execute('CREATE INDEX ix_audit_created ON audit_events (created_at)')
    op.execute("INSERT INTO roles (name) VALUES ('editor'), ('reviewer'), ('publisher'), ('administrator')")


def downgrade():
    raise RuntimeError("Restore a paired backup; refusing to discard platform records")
