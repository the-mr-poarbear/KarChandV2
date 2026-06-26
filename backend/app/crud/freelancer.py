from sqlalchemy.orm import Session
from app.models.freelancer import Freelancer
from app.schemas.freelancer import FreelancerCreate


def get_freelancer(db: Session, scraped_id: str, category_id: str) -> Freelancer | None:
    return (
        db.query(Freelancer)
        .filter(Freelancer.scraped_id == scraped_id, Freelancer.category_id == category_id)
        .first()
    )


def get_all_freelancers(db: Session) -> list[Freelancer]:
    return db.query(Freelancer).order_by(Freelancer.id).all()


def get_freelancers_by_category(db: Session, category_id: str) -> list[Freelancer]:
    return db.query(Freelancer).filter(Freelancer.category_id == category_id).order_by(Freelancer.id).all()


def get_unscraped_freelancers(db: Session) -> list[Freelancer]:
    """Freelancers whose projects_scraped checkpoint is still False — i.e. not yet fully processed."""
    return (
        db.query(Freelancer)
        .filter(Freelancer.projects_scraped == False)  # noqa: E712
        .order_by(Freelancer.id)
        .all()
    )


def mark_projects_scraped(db: Session, freelancer_id: str, completed_projects_count: int | None = None) -> None:
    """
    Set the checkpoint flag once a freelancer's full pagination has completed
    without error. Optionally records the total completed_projects_count
    reported by Karlancer's API for this freelancer/category.
    """
    update_values = {"projects_scraped": True}
    if completed_projects_count is not None:
        update_values["completed_projects_count"] = completed_projects_count

    db.query(Freelancer).filter(Freelancer.id == freelancer_id).update(update_values)
    db.commit()


def create_or_get_freelancer(db: Session, freelancer_data: FreelancerCreate) -> Freelancer:
    """Create a freelancer/category pairing if it doesn't already exist. No fields to update beyond the key itself."""
    existing = get_freelancer(db, freelancer_data.scraped_id, freelancer_data.category_id)
    if existing:
        return existing

    db_freelancer = Freelancer(
        scraped_id=freelancer_data.scraped_id,
        category_id=freelancer_data.category_id,
    )
    db.add(db_freelancer)
    db.commit()
    db.refresh(db_freelancer)
    return db_freelancer