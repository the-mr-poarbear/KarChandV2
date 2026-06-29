from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.ponisha.scraper_categories import PonishaCategoryScraperService
from app.services.ponisha.scraper_project import PonishaProjectScraperService

router = APIRouter(prefix="/ponisha", tags=["ponisha"])


@router.post("/categories")
def scrape_ponisha_categories(db: Session = Depends(get_db)):
    """Scrape top-level categories from Ponisha (subcategories are ignored)."""
    service = PonishaCategoryScraperService(db)
    return service.scrape_and_update()


@router.post("/projects")
def scrape_ponisha_category_projects(
    category_id: str,
    update_existing: bool = False,
    min_bidders: int = 3,
    db: Session = Depends(get_db),
):
    """
    Scrape projects (open and closed) for one Ponisha category (your internal
    Category.id UUID). Projects with fewer than min_bidders total bids are
    skipped. Projects without a valid accepted bid yet are still saved, with
    final_budget left as null.
    """
    service = PonishaProjectScraperService(db)
    return service.scrape_category(category_id, update_existing=update_existing, min_bidders=min_bidders)


@router.post("/projects/all")
def scrape_all_ponisha_projects(
    max_workers: int = 5,
    update_existing: bool = False,
    min_bidders: int = 3,
    db: Session = Depends(get_db),
):
    """
    Scrape projects across every stored Ponisha category, using up to
    max_workers parallel threads (one category per worker at a time). All
    workers share an adaptive rate limiter that backs off automatically on
    429/403 responses or timeouts. Same filtering rules as /projects.
    """
    service = PonishaProjectScraperService(db)
    return service.scrape_all_categories(
        max_workers=max_workers, update_existing=update_existing, min_bidders=min_bidders
    )