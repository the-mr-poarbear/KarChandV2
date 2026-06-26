from sqlalchemy.orm import Session
from app.models.organization import Organization
from typing import Optional


def get_organization_by_name(db: Session, name: str) -> Optional[Organization]:
    """Look up an organization by its exact name."""
    return db.query(Organization).filter(Organization.name == name).first()


def get_all_organizations(db: Session):
    """Get all organizations."""
    return db.query(Organization).all()