"""add organizations table, organization_id to categories and skills

Revision ID: 3686a0ac2eba
Revises: 1008aa6295d2
Create Date: 2026-06-26 17:10:01.559786

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3686a0ac2eba'
down_revision: Union[str, None] = '1008aa6295d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


KARLANCER_ORG_ID = "11111111-1111-1111-1111-111111111111"  # fixed, known UUID


def upgrade():
    # 1. Create organizations table
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # 2. Seed the Karlancer organization row
    op.execute(
        f"INSERT INTO organizations (id, name) VALUES ('{KARLANCER_ORG_ID}', 'Karlancer')"
    )

    # ---- categories ----

    # 3. Add organization_id as NULLABLE first (existing rows have no value yet)
    op.add_column("categories", sa.Column("organization_id", sa.String(), nullable=True))

    # 4. Backfill all existing categories to point at Karlancer
    op.execute(f"UPDATE categories SET organization_id = '{KARLANCER_ORG_ID}'")

    # 5. Now enforce NOT NULL, since every row has a value
    op.alter_column("categories", "organization_id", nullable=False)

    # 6. FK + composite unique constraint
    op.create_foreign_key(
        "fk_categories_organization_id", "categories", "organizations",
        ["organization_id"], ["id"], ondelete="CASCADE"
    )
    op.create_unique_constraint(
        "uq_category_scraped_id_org", "categories", ["scraped_id", "organization_id"]
    )
    # No old unique index to drop here — categories.scraped_id only ever had a
    # plain (non-unique) index, confirmed via \d categories.

    # ---- skills ----

    # 7. Add organization_id as NULLABLE first
    op.add_column("skills", sa.Column("organization_id", sa.String(), nullable=True))

    # 8. Backfill existing skills to point at Karlancer
    op.execute(f"UPDATE skills SET organization_id = '{KARLANCER_ORG_ID}'")

    # 9. Enforce NOT NULL
    op.alter_column("skills", "organization_id", nullable=False)

    # 10. Drop the OLD unique index on scraped_id alone — confirmed via \d skills
    #     it's named "ix_skills_scraped_id" and IS unique, so it must go before
    #     adding the new composite constraint (otherwise you'd have two
    #     conflicting uniqueness rules on scraped_id).
    op.drop_index("ix_skills_scraped_id", table_name="skills")

    # 11. FK + composite unique constraint
    op.create_foreign_key(
        "fk_skills_organization_id", "skills", "organizations",
        ["organization_id"], ["id"], ondelete="CASCADE"
    )
    op.create_unique_constraint(
        "uq_skill_scraped_id_org", "skills", ["scraped_id", "organization_id"]
    )

    # Recreate a plain (non-unique) index on skills.scraped_id for lookup speed,
    # matching the pattern categories already uses (index=True without uniqueness)
    op.create_index("ix_skills_scraped_id", "skills", ["scraped_id"], unique=False)


def downgrade():
    op.drop_index("ix_skills_scraped_id", table_name="skills")
    op.drop_constraint("uq_skill_scraped_id_org", "skills", type_="unique")
    op.drop_constraint("fk_skills_organization_id", "skills", type_="foreignkey")
    op.drop_column("skills", "organization_id")
    op.create_index("ix_skills_scraped_id", "skills", ["scraped_id"], unique=True)

    op.drop_constraint("uq_category_scraped_id_org", "categories", type_="unique")
    op.drop_constraint("fk_categories_organization_id", "categories", type_="foreignkey")
    op.drop_column("categories", "organization_id")

    op.drop_table("organizations")