# app/api/scraper.py
from fastapi import APIRouter, Depends, BackgroundTasks , HTTPException
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.services.scraper_categories import ScraperService
from pydantic import BaseModel
from app.services.scraper_freelancer import FreelancerScraperService
from app.services.scraper_project import ProjectScraperService
from app.crud import organization as organization_crud
from app.schemas.organization import OrganizationName

router = APIRouter(prefix="/scraper", tags=["scraper"])


def run_scrape(db: Session):
    """Wrapper so we control session lifecycle inside the background task"""
    db = SessionLocal()
    try:
        service = ScraperService(db)
        service.scrape_and_update()
    finally:
        db.close()


# @router.post("/run")
# def trigger_scrape(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
#     """
#     Triggers scraping in the background. Returns immediately;
#     scraping continues after the response is sent.
#     """
#     background_tasks.add_task(run_scrape, db)
#     return {"status": "started", "message": "Scraping job triggered in background"}





@router.post("/run-sync")
def trigger_scrape_sync(
    db: Session = Depends(get_db),
):
    """
    Runs scraping synchronously and returns the result.
    Useful for manual testing — will block until scraping finishes.
 
    Currently only scrapes Karlancer. The Karlancer organization must
    already exist in the DB (seeded) - if not, this returns 404.
    """
    KARLANCER = "Karlancer"
    org = organization_crud.get_organization_by_name(db, KARLANCER)
    if not org:
        raise HTTPException(
            status_code=404,
            detail=f"Organization '{KARLANCER}' not found. It must be seeded before scraping.",
        )
 
    service = ScraperService(db)
    result = service.scrape_and_update(organization_id=org.id)
    return result


@router.post("/freelancers")
def scrape_freelancers(skill_id: str, category_id: str, db: Session = Depends(get_db)):
    """
    Scrape all freelancers matching a given skill_id, tagging them with category_id.
    You supply both manually after checking the source site.
    """
    service = FreelancerScraperService(db)
    return service.scrape_skill(skill_id, category_id)
 
 
class SkillCategoryPair(BaseModel):
    skill_id: str
    category_id: str
 
 
@router.post("/freelancers/batch")
def scrape_freelancers_batch(pairs: list[SkillCategoryPair], db: Session = Depends(get_db)):
    """Scrape freelancers for multiple (skill_id, category_id) pairs in one call."""
    service = FreelancerScraperService(db)
    pair_tuples = [(p.skill_id, p.category_id) for p in pairs]
    return service.scrape_all_known_skills(pair_tuples)
 
 
@router.post("/freelancer-projects")
def scrape_freelancer_projects(scraped_user_id: str, category_id: str, db: Session = Depends(get_db)):
    """
    Fetch completed projects for one freelancer (by their Karlancer user id) within
    a given category, then fetch full detail for each and save to the Project table.
    """
    service = ProjectScraperService(db)
    return service.scrape_freelancer_projects(scraped_user_id, category_id)
 
 
@router.post("/freelancer-projects/all")
def scrape_all_freelancer_projects(
    max_workers: int = 5,
    update_existing: bool = False,
    resume: bool = False,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """
    Runs the full project-scraping pipeline (stages 2-4) for every freelancer
    already stored in the Freelancer table, using up to max_workers parallel
    threads. All workers share an adaptive rate limiter that backs off
    automatically on 429/403 responses or timeouts.
 
    update_existing: if False (default), projects already saved are skipped
    entirely without even calling the detail endpoint. Set True to re-fetch
    and overwrite projects already in the table.
 
    resume: if True, skips freelancers already fully completed in a previous
    run (tracked via the projects_scraped checkpoint), continuing from where
    you left off. If False (default), every freelancer is processed.
 
    offset: optional manual override — skip the first N freelancers in the
    list (applied after resume filtering, if both are set). Use this for
    manual control independent of the checkpoint, e.g. to skip ahead to a
    known point. Default 0 (no skipping).
    """
    service = ProjectScraperService(db)
    return service.scrape_all_freelancers(
        max_workers=max_workers,
        update_existing=update_existing,
        resume=resume,
        offset=offset,
    )
