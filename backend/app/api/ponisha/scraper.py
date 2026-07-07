from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.ponisha.scraper_categories import PonishaCategoryScraperService
from app.services.ponisha.scraper_project import PonishaProjectScraperService
from app.services.ponisha.scraper_freelancer import PonishaFreelancerScraperService
from app.services.ponisha.scraper_freelancers_projects import PonishaFreelancerProjectScraperService

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


@router.post("/freelancers")
def scrape_ponisha_freelancers(skill_id: str, category_id: str, db: Session = Depends(get_db)):
    """
    Stage 1: scrape freelancers matching skill_id (your internal Skill.id
    UUID), keeping only those with at least one completed project. Exploits
    the descending sort order (within and across pages) to check only the
    last item of each page first, stopping entirely once the zero-project
    cutoff is found. category_id (your internal Category.id UUID) tags the
    stored rows for later use — never sent to Ponisha's API.
    """
    service = PonishaFreelancerScraperService(db)
    return service.scrape_skill(skill_id, category_id)
 
 
@router.post("/freelancer-projects")
def scrape_ponisha_freelancer_projects(
    username: str,
    category_id: str,
    update_existing: bool = False,
    db: Session = Depends(get_db),
):
    """
    Stage 2: scrape one freelancer's completed projects, keeping only those
    matching category_id (your internal Category.id UUID).
    """
    service = PonishaFreelancerProjectScraperService(db)
    return service.scrape_freelancer(username, category_id, update_existing=update_existing)
 
 
@router.post("/freelancer-projects/all")
def scrape_all_ponisha_freelancer_projects(
    max_workers: int = 5,
    update_existing: bool = False,
    category_id: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Stage 2 for stored Ponisha freelancers, in parallel.
 
    category_id: optional. If given (your internal Category.id UUID), only
    freelancers tagged with that category are processed, and their projects
    are filtered to that same category. If omitted, every stored Ponisha
    freelancer is processed.
    """
    service = PonishaFreelancerProjectScraperService(db)
    return service.scrape_all_freelancers(
        max_workers=max_workers, update_existing=update_existing, category_id=category_id
    )
