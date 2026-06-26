from sqlalchemy import Column, String, ForeignKey, UniqueConstraint
from app.core.database import Base
import uuid


class ProjectSkill(Base):
    __tablename__ = "projects_skills"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_id = Column(String, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("project_id", "skill_id", name="uq_project_skill"),
    )