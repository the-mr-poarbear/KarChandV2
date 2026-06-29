import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import skill as skill_crud
from app.schemas.skill import SkillCreate


class SkillScraperService:
    """Fetches skills from Karlancer's public API and syncs them to the DB"""

    def __init__(self, db: Session):
        self.db = db

    def scrape_and_update(self) -> dict:
        result = {
            "status": "success",
            "skills_found": 0,
            "skills_added": 0,
            "skills_already_existed": 0,
            "errors": [],
        }

        raw_groups = self._fetch_skills()

        for group_name, items in raw_groups.items():
            for item in items:
                try:
                    name = item.get("name")
                    if not name:
                        continue

                    result["skills_found"] += 1
                    skill_data = SkillCreate(name=name)

                    existing = skill_crud.get_skill_by_name(self.db, name)
                    skill_crud.create_or_update_skill(self.db, skill_data)

                    if existing:
                        result["skills_already_existed"] += 1
                    else:
                        result["skills_added"] += 1

                except Exception as e:
                    result["errors"].append({
                        "group": group_name,
                        "item": item.get("name", "unknown"),
                        "error": str(e),
                    })

        self.db.commit()
        return result

    def _fetch_skills(self) -> dict:
        url = f"{settings.KARLANCER_URL}/api/publics/skills/"
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        payload = response.json()

        if payload.get("status") != "success":
            raise ValueError(f"Skills API returned error: {payload.get('error')}")

        return payload["data"]