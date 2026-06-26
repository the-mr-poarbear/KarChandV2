import uuid
from sqlalchemy import Column, String
from sqlalchemy.orm import relationship
from app.core.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True)

    categories = relationship("Category", back_populates="organization")
    skills = relationship("Skill", back_populates="organization")