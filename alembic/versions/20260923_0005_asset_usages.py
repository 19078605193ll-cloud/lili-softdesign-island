"""Add durable question asset usage relationships.

Revision ID: 20260923_0005
Revises: 20260922_0004
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_0005"
down_revision: str | None = "20260922_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "question_assets_document_id_fkey", "question_assets", type_="foreignkey"
    )
    op.create_foreign_key(
        "question_assets_document_id_fkey",
        "question_assets",
        "question_source_documents",
        ["document_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "question_asset_usages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("import_item_id", sa.Uuid(), nullable=True),
        sa.Column("import_group_id", sa.Uuid(), nullable=True),
        sa.Column("question_id", sa.Uuid(), nullable=True),
        sa.Column("question_group_id", sa.Uuid(), nullable=True),
        sa.Column("placement", sa.String(30), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("alt_text", sa.String(300), server_default="原题素材", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(CASE WHEN import_item_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN import_group_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN question_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN question_group_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_question_asset_usage_one_owner",
        ),
        sa.CheckConstraint(
            "placement IN ('stem', 'group_material', 'option', 'explanation')",
            name="ck_question_asset_usage_placement",
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_question_asset_usage_sort_order"),
        sa.ForeignKeyConstraint(["asset_id"], ["question_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["import_item_id"], ["question_import_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["import_group_id"], ["question_import_groups.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["question_group_id"], ["question_groups.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_asset_usage_import_item", "question_asset_usages", ["import_item_id"]
    )
    op.create_index(
        "ix_asset_usage_import_group", "question_asset_usages", ["import_group_id"]
    )
    op.create_index(
        "ix_asset_usage_question", "question_asset_usages", ["question_id"]
    )
    op.create_index(
        "ix_asset_usage_question_group", "question_asset_usages", ["question_group_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_asset_usage_question_group", table_name="question_asset_usages")
    op.drop_index("ix_asset_usage_question", table_name="question_asset_usages")
    op.drop_index("ix_asset_usage_import_group", table_name="question_asset_usages")
    op.drop_index("ix_asset_usage_import_item", table_name="question_asset_usages")
    op.drop_table("question_asset_usages")
    op.drop_constraint(
        "question_assets_document_id_fkey", "question_assets", type_="foreignkey"
    )
    op.create_foreign_key(
        "question_assets_document_id_fkey",
        "question_assets",
        "question_source_documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )
