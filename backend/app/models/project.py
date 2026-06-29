from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Integer, Float, Index , UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from app.core.database import Base

class Project(Base):
    __tablename__ = "projects"

    # Internal ID (auto-generated UUID)
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    
    # Project data
    title = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    outer_link = Column(String, nullable=False)
    duration = Column(String, nullable=True)
    budget_min = Column(Float, nullable=True)
    budget_max = Column(Float, nullable=True)
    final_budget = Column(Float, nullable=True)
    
    # External ID from Karlancer
    scraped_project_id = Column(String, nullable=False, index=True)
    scraped_date_created = Column(DateTime, nullable=True)
    
    # Foreign Key to Category (set null on delete)
    category_id = Column(String, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship (no cascade delete)
    category = relationship("Category", back_populates="projects")
    skills = relationship("Skill", secondary="projects_skills", back_populates="projects")

    # Optional: Composite index for common queries
    __table_args__ = (
        Index('idx_projects_category_budget', 'category_id', 'budget_min', 'budget_max'),
        UniqueConstraint('scraped_project_id', 'category_id', name='uq_project_scraped_id_category'),
    )