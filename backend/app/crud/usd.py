from datetime import date

from sqlalchemy.orm import Session

from app.models.usd import USD
from app.schemas.usd import USDCreate


def get_by_date(db: Session, day: date) -> USD | None:
    return db.query(USD).filter(USD.date == day).first()


def create(db: Session, usd: USDCreate) -> USD:
    obj = USD(**usd.model_dump())

    db.add(obj)
    db.commit()
    db.refresh(obj)

    return obj


def update(db: Session, obj: USD, usd: USDCreate) -> USD:
    for key, value in usd.model_dump().items():
        setattr(obj, key, value)

    db.commit()
    db.refresh(obj)

    return obj


def upsert(db: Session, usd: USDCreate) -> USD:
    existing = get_by_date(db, usd.date)

    if existing:
        return update(db, existing, usd)

    return create(db, usd)