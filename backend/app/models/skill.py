import uuid
from sqlalchemy import Column, String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.core.database import Base


class Skill(Base):
    __tablename__ = "skills"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    scraped_id = Column(String, nullable=False, index=True)  # source platform's skill id

    # Which platform/organization this skill came from. Some sources use
    # "skills" the way others use "categories" - tying this directly to
    # Organization (rather than only reachable via Category) supports that.
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String, nullable=False)

    projects = relationship("Project", secondary="projects_skills", back_populates="skills")
    organization = relationship("Organization", back_populates="skills")

    __table_args__ = (
        # scraped_id only needs to be unique WITHIN one organization - same
        # reasoning as Category.
        UniqueConstraint("scraped_id", "organization_id", name="uq_skill_scraped_id_org"),
    )
