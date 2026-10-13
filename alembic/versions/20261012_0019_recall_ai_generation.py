"""Add AI supplement review metadata to recall versions."""

from alembic import op

revision = "20261012_0019"
down_revision = "20261012_0018"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE recall_versions
ADD COLUMN supplement_review JSONB NOT NULL DEFAULT '{}'::jsonb,
ADD COLUMN supplement_reviewed_by UUID,
ADD COLUMN supplement_reviewed_at TIMESTAMP WITH TIME ZONE,
ADD CONSTRAINT fk_recall_supplement_reviewer
    FOREIGN KEY(supplement_reviewed_by) REFERENCES users (id)
""")


def downgrade():
    op.drop_constraint(
        "fk_recall_supplement_reviewer", "recall_versions", type_="foreignkey"
    )
    op.drop_column("recall_versions", "supplement_reviewed_at")
    op.drop_column("recall_versions", "supplement_reviewed_by")
    op.drop_column("recall_versions", "supplement_review")
