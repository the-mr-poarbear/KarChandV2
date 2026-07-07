from sqlalchemy import Column, String, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from datetime import datetime, timezone


class Category(Base):
    __tablename__ = "categories"
    # Your internal ID (auto-generated UUID)
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))

    # External ID from the source platform (Karlancer, or others going forward)
    scraped_id = Column(String, nullable=False, index=True)

    # Which platform/organization this category came from
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)

    # Category data
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(
    DateTime,
    default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
)
    updated_at = Column(
    DateTime,
    default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
)

    projects = relationship("Project", back_populates="category", cascade="all, delete-orphan")
    organization = relationship("Organization", back_populates="categories")

    __table_args__ = (
        # scraped_id only needs to be unique WITHIN one organization — the same
        # raw id string could coincidentally appear across two different source
        # platforms without actually meaning the same category.
        UniqueConstraint("scraped_id", "organization_id", name="uq_category_scraped_id_org"),
    )