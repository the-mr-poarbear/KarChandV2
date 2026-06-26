import uuid
from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, UniqueConstraint
from app.core.database import Base


class Freelancer(Base):
    __tablename__ = "freelancers"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    scraped_id = Column(String, nullable=False, index=True)  # Karlancer's user id
    category_id = Column(String, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False, index=True)

    # Checkpoint flag: set True once this freelancer's completed-projects
    # pagination has fully finished without error. Lets a resumed run skip
    # entire already-completed freelancers, not just individual projects.
    projects_scraped = Column(Boolean, nullable=False, default=False, index=True)

    # Number of completed projects this freelancer has in this category,
    # per Karlancer's completed-projects API ("total" field). Set once the
    # full pagination for this freelancer finishes. Record-only — does not
    # gate any scraping behavior.
    completed_projects_count = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("scraped_id", "category_id", name="uq_freelancer_category"),
    )