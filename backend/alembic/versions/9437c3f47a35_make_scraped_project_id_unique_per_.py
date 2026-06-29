"""make scraped_project_id unique per category

Revision ID: 9437c3f47a35
Revises: 3686a0ac2eba
Create Date: 2026-06-27 08:57:06.694068

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9437c3f47a35'
down_revision: Union[str, None] = '3686a0ac2eba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Drop the old unique INDEX on scraped_project_id alone (confirmed via
    # \d projects: it's "ix_projects_scraped_project_id", a unique index,
    # NOT a named constraint - hence drop_index, not drop_constraint).
    op.drop_index("ix_projects_scraped_project_id", table_name="projects")
 
    # New composite uniqueness: a scraped_project_id only needs to be unique
    # WITHIN one category. A project never changes category once created,
    # and categories are never shared between organizations, so this safely
    # allows the same raw scraped_project_id to exist under two different
    # categories (e.g. one from Karlancer, one from Ponisha) without collision.
    op.create_unique_constraint(
        "uq_project_scraped_id_category", "projects", ["scraped_project_id", "category_id"]
    )
 
    # Recreate a plain (non-unique) index on scraped_project_id for lookup
    # speed, since the column is still queried directly elsewhere.
    op.create_index("ix_projects_scraped_project_id", "projects", ["scraped_project_id"], unique=False)
 
 
def downgrade():
    op.drop_index("ix_projects_scraped_project_id", table_name="projects")
    op.drop_constraint("uq_project_scraped_id_category", "projects", type_="unique")
    op.create_index("ix_projects_scraped_project_id", "projects", ["scraped_project_id"], unique=True)
