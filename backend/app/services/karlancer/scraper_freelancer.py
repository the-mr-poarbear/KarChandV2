import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import freelancer as freelancer_crud
from app.schemas.freelancer import FreelancerCreate


class FreelancerScraperService:
    """
    Stage 1 of the pipeline.

    For a given skill_id (tied to a category), fetches all freelancers who list
    that skill, paginating through results, and stores {scraped_id, category_id}
    pairs. Only the freelancer's id is kept — no other profile data.
    """

    def __init__(self, db: Session):
        self.db = db

    def scrape_all_known_skills(self, skill_category_pairs: list[tuple[str, str]]) -> dict:
        """
        Orchestrates scrape_skill() across many (skill_id, category_id) pairs.
        Caller supplies the pairs explicitly — e.g. built from your Skill/Category
        tables, or any other source you already have mapping skills to categories.
        """
        overall = {
            "status": "success",
            "skills_processed": 0,
            "freelancers_added": 0,
            "freelancers_already_existed": 0,
            "errors": [],
        }

        for skill_id, category_id in skill_category_pairs:
            single = self.scrape_skill(skill_id, category_id)
            overall["skills_processed"] += 1
            overall["freelancers_added"] += single["freelancers_added"]
            overall["freelancers_already_existed"] += single["freelancers_already_existed"]
            if single["errors"]:
                overall["errors"].append({"skill_id": skill_id, "errors": single["errors"]})

        return overall

    def scrape_skill(self, skill_id: str, category_id: str) -> dict:
        """Fetch and store all freelancers matching a single skill_id, tagged with category_id."""
        result = {
            "status": "success",
            "skill_id": skill_id,
            "category_id": category_id,
            "freelancers_found": 0,
            "freelancers_added": 0,
            "freelancers_already_existed": 0,
            "errors": [],
        }

        page = 1
        consecutive_failures = 0
        max_consecutive_failures = 5  # stop only after repeated failures, not one blip
        max_retries_per_page = 3

        while True:
            payload = None
            last_error = None

            for attempt in range(1, max_retries_per_page + 1):
                try:
                    payload = self._fetch_freelancers_page(skill_id, page)
                    break  # success
                except Exception as e:
                    last_error = e
                    print(f"⚠️  Page {page} attempt {attempt}/{max_retries_per_page} failed: {e}")

            if payload is None:
                # All retries for this page failed.
                result["errors"].append({"page": page, "error": str(last_error)})
                consecutive_failures += 1
                print(f"❌ Page {page} failed after {max_retries_per_page} attempts. "
                      f"Consecutive failed pages: {consecutive_failures}")
                if consecutive_failures >= max_consecutive_failures:
                    print(f"🛑 Stopping: {max_consecutive_failures} consecutive page failures for skill {skill_id}.")
                    break
                page += 1  # skip this page, try the next one rather than stopping entirely
                continue

            consecutive_failures = 0  # reset on any success
            data = payload.get("data", {})
            freelancers = data.get("data", [])

            for fl in freelancers:
                try:
                    scraped_id = str(fl.get("id"))
                    if not scraped_id or scraped_id == "None":
                        continue

                    result["freelancers_found"] += 1
                    freelancer_data = FreelancerCreate(scraped_id=scraped_id, category_id=category_id)

                    existing = freelancer_crud.get_freelancer(self.db, scraped_id, category_id)
                    freelancer_crud.create_or_get_freelancer(self.db, freelancer_data)

                    if existing:
                        result["freelancers_already_existed"] += 1
                    else:
                        result["freelancers_added"] += 1

                except Exception as e:
                    result["errors"].append({
                        "freelancer_id": fl.get("id", "unknown"),
                        "error": str(e),
                    })

            last_page = data.get("last_page", page)
            print(f"  Skill {skill_id} — page {page}/{last_page} — {len(freelancers)} freelancers")

            # Commit per page (not just once at the end) — same reasoning as the
            # project scraper: keeps the session's pending state small across a
            # long pagination run (417 pages here), avoiding progressive slowdown.
            self.db.commit()

            if page >= int(last_page):
                break
            page += 1
        return result

    def _fetch_freelancers_page(self, skill_id: str, page: int) -> dict:
        url = f"{settings.KARLANCER_URL}/api/publics/search/freelancers"
        params = {
            "order": "best",
            "skillIds[0]": skill_id,
            "page": page,
        }
        response = httpx.get(url, params=params, timeout=10)
        response.raise_for_status()
        payload = response.json()

        if payload.get("status") != "success":
            raise ValueError(f"Freelancers API returned error: {payload.get('error')}")

        return payload