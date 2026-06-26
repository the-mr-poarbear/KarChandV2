from sqlalchemy.orm import Session
from app.models.category import Category
from app.schemas.category import CategoryCreate
from typing import List, Optional
import uuid


def get_all_categories(db: Session) -> List[Category]:
    """Get all categories (no pagination)"""
    return db.query(Category).all()


def get_categories_by_organization(db: Session, organization_id: str) -> List[Category]:
    return db.query(Category).filter(Category.organization_id == organization_id).all()


def get_category_by_scraped_id(db: Session, scraped_id: str, organization_id: str) -> Optional[Category]:
    """scraped_id is only unique WITHIN one organization, so both must be given together."""
    return (
        db.query(Category)
        .filter(Category.scraped_id == scraped_id, Category.organization_id == organization_id)
        .first()
    )


def update_category_by_scraped_id(
    db: Session,
    scraped_id: str,
    organization_id: str,
    category_data: CategoryCreate,
) -> Optional[Category]:
    """Update a category by scraped_id, scoped to one organization"""
    db_category = get_category_by_scraped_id(db, scraped_id, organization_id)
    if not db_category:
        return None

    db_category.name = category_data.name
    db_category.description = category_data.description

    db.commit()
    db.refresh(db_category)
    return db_category


def delete_category_by_scraped_id(db: Session, scraped_id: str, organization_id: str) -> bool:
    """Delete a category by scraped_id, scoped to one organization"""
    db_category = get_category_by_scraped_id(db, scraped_id, organization_id)
    if not db_category:
        return False

    db.delete(db_category)
    db.commit()
    return True


def create_or_update_category(db: Session, category_data: CategoryCreate) -> Category:
    """Create or update category by (scraped_id, organization_id)"""
    existing = get_category_by_scraped_id(db, category_data.scraped_id, category_data.organization_id)

    if existing:
        existing.name = category_data.name
        existing.description = category_data.description
        db.commit()
        db.refresh(existing)
        return existing
    else:
        db_category = Category(
            id=str(uuid.uuid4()),
            scraped_id=category_data.scraped_id,
            organization_id=category_data.organization_id,
            name=category_data.name,
            description=category_data.description,
        )
        db.add(db_category)
        db.commit()
        db.refresh(db_category)
        return db_category