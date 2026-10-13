"""Add versioned recall content and isolated learner records."""

from alembic import op
from sqlalchemy import text

revision = "20261012_0018"
down_revision = "20261002_0017"
branch_labels = depends_on = None


def upgrade():
    # Frozen PostgreSQL DDL: independent of future ORM changes.
    op.execute("""
CREATE TABLE recalls (
	id UUID NOT NULL, 
	subject_id UUID NOT NULL, 
	slug VARCHAR(160) NOT NULL, 
	published_version_id UUID, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (subject_id, slug), 
	FOREIGN KEY(subject_id) REFERENCES exam_subjects (id)
)
""")
    op.execute("""
CREATE TABLE recall_versions (
	id UUID NOT NULL, 
	recall_id UUID NOT NULL, 
	number INTEGER NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	type VARCHAR(20) NOT NULL, 
	schema_version INTEGER NOT NULL, 
	content JSONB NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	revision INTEGER NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	reviewed_hash VARCHAR(64), 
	source VARCHAR(20) NOT NULL, 
	provenance JSONB NOT NULL, 
	created_by UUID NOT NULL, 
	reviewed_by UUID, 
	published_by UUID, 
	reviewed_at TIMESTAMP WITH TIME ZONE, 
	published_at TIMESTAMP WITH TIME ZONE, 
	review_note TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (recall_id, number), 
	UNIQUE (id, recall_id), 
	CHECK (type IN ('table','sentence','relation')), 
	CHECK (status IN ('draft','pending','approved','published')), 
	CHECK (source IN ('manual','ai')), 
	FOREIGN KEY(recall_id) REFERENCES recalls (id), 
	FOREIGN KEY(created_by) REFERENCES users (id), 
	FOREIGN KEY(reviewed_by) REFERENCES users (id), 
	FOREIGN KEY(published_by) REFERENCES users (id)
)
""")
    op.execute("""
CREATE INDEX ix_recall_versions_recall_id ON recall_versions (recall_id)
""")
    op.execute("""
CREATE TABLE recall_knowledge_links (
	version_id UUID NOT NULL, 
	knowledge_node_id UUID NOT NULL, 
	PRIMARY KEY (version_id, knowledge_node_id), 
	FOREIGN KEY(version_id) REFERENCES recall_versions (id), 
	FOREIGN KEY(knowledge_node_id) REFERENCES knowledge_nodes (id)
)
""")
    op.execute("""
CREATE INDEX ix_recall_knowledge_links_knowledge_node_id ON recall_knowledge_links (knowledge_node_id)
""")
    op.execute("""
CREATE TABLE recall_blanks (
	version_id UUID NOT NULL, 
	key VARCHAR(80) NOT NULL, 
	position INTEGER NOT NULL, 
	label VARCHAR(200) NOT NULL, 
	answer TEXT NOT NULL, 
	acceptable_answers JSONB NOT NULL, 
	normalization JSONB NOT NULL, 
	hints JSONB NOT NULL, 
	PRIMARY KEY (version_id, key), 
	UNIQUE (version_id, position), 
	FOREIGN KEY(version_id) REFERENCES recall_versions (id)
)
""")
    op.execute("""
CREATE TABLE recall_sessions (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	recall_id UUID NOT NULL, 
	version_id UUID NOT NULL, 
	phase VARCHAR(20) NOT NULL, 
	queue JSONB NOT NULL, 
	position INTEGER NOT NULL, 
	revision INTEGER NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(version_id, recall_id) REFERENCES recall_versions (id, recall_id), 
	UNIQUE (id, version_id), 
	CHECK (phase IN ('first','retest_ready','retest','completed')), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(recall_id) REFERENCES recalls (id)
)
""")
    op.execute("""
CREATE INDEX ix_recall_session_recent ON recall_sessions (user_id, updated_at)
""")
    op.execute("""
CREATE UNIQUE INDEX uq_recall_active_session ON recall_sessions (user_id, recall_id) WHERE phase <> 'completed'
""")
    op.execute("""
CREATE TABLE recall_session_blanks (
	session_id UUID NOT NULL, 
	blank_key VARCHAR(80) NOT NULL, 
	version_id UUID NOT NULL, 
	draft TEXT NOT NULL, 
	round_error_count INTEGER NOT NULL, 
	pass_error_count INTEGER NOT NULL, 
	needs_retest BOOLEAN NOT NULL, 
	mastered BOOLEAN NOT NULL, 
	PRIMARY KEY (session_id, blank_key), 
	FOREIGN KEY(session_id, version_id) REFERENCES recall_sessions (id, version_id), 
	FOREIGN KEY(version_id, blank_key) REFERENCES recall_blanks (version_id, key), 
	CHECK (round_error_count >= 0 AND pass_error_count BETWEEN 0 AND 3)
)
""")
    op.execute("""
CREATE TABLE recall_submissions (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	recall_id UUID NOT NULL, 
	session_id UUID NOT NULL, 
	blank_key VARCHAR(80) NOT NULL, 
	idempotency_key VARCHAR(200) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	answer TEXT NOT NULL, 
	normalized TEXT NOT NULL, 
	correct BOOLEAN NOT NULL, 
	hint_level INTEGER NOT NULL, 
	result JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(session_id, blank_key) REFERENCES recall_session_blanks (session_id, blank_key), 
	UNIQUE (user_id, idempotency_key), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(recall_id) REFERENCES recalls (id)
)
""")
    op.execute("""
CREATE INDEX ix_recall_errors ON recall_submissions (user_id, recall_id, blank_key, created_at)
""")
    op.execute("""
ALTER TABLE recalls ADD CONSTRAINT fk_recall_published_version FOREIGN KEY(published_version_id, id) REFERENCES recall_versions (id, recall_id)
""")


def downgrade():
    if op.get_bind().scalar(text("SELECT count(*) FROM recalls")):
        raise RuntimeError(
            "Recall data exists; disable the feature instead of a lossy downgrade"
        )
    op.drop_constraint("fk_recall_published_version", "recalls", type_="foreignkey")
    for name in (
        "recall_submissions",
        "recall_session_blanks",
        "recall_sessions",
        "recall_blanks",
        "recall_knowledge_links",
        "recall_versions",
        "recalls",
    ):
        op.drop_table(name)
