from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.scraper_skills import SkillScraperService

router = APIRouter(prefix="/skills", tags=["skills"])


@router.post("/scrape")
def trigger_skill_scrape(db: Session = Depends(get_db)):
    service = SkillScraperService(db)
    return service.scrape_and_update()