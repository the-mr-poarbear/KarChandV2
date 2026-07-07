import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import freelancer as freelancer_crud
from app.models.skill import Skill
from app.schemas.freelancer import FreelancerCreate
from app.services.rate_limiter import AdaptiveRateLimiter

PONISHA_ORG_ID = "22222222-2222-2222-2222-222222222222"  # TODO: replace with real UUID


class PonishaFreelancerScraperService:
    """
    Stage 1 of the Ponisha freelancer-based pipeline (workaround for the
    Elasticsearch 10,000-result window limit on direct project search).

    Freelancers are returned by Ponisha sorted by completed_project_count
    descending, both across pages and within each page. We exploit this: for
    each page, we check only the LAST item first. If it has >=1 completed
    project, the whole page qualifies and is stored without checking anyone
    else individually. If it has 0, we scan backward within that page to find
    the real cutoff, store everyone up to and including it, then stop the
    scrape entirely — everything past that point, including all later pages,
    is assumed to also be zero.
    """

    def __init__(self, db: Session, rate_limiter: AdaptiveRateLimiter | None = None):
        self.db = db
        self.rate_limiter = rate_limiter or AdaptiveRateLimiter()

    def scrape_skill(self, skill_id: str, category_id: str) -> dict:
        """
        skill_id: YOUR internal Skill.id (UUID). Resolved to Ponisha's own
        scraped_id before calling their search API.
        category_id: YOUR internal Category.id (UUID). Used ONLY to tag the
        stored Freelancer row — never sent to Ponisha's API.

        Optimization: freelancers within a page are sorted by
        completed_project_count descending, same as across pages. So the
        LAST item on a page is the worst case for that page. We check it
        first:
          - if it has >=1 project, the whole page qualifies — store everyone,
            no individual checks needed, move to the next page.
          - if it has 0, scan backward from the end of the page to find the
            cutoff (the last freelancer who DOES have >=1 project), store
            everyone up to and including that one, then stop the scrape
            entirely (everything after this point, including later pages,
            is assumed to also be zero).
        """
        result = {
            "status": "success",
            "skill_id": skill_id,
            "category_id": category_id,
            "freelancers_checked": 0,
            "freelancers_with_projects": 0,
            "freelancers_added": 0,
            "freelancers_already_existed": 0,
            "stopped_reason": None,
            "errors": [],
        }

        skill = (
            self.db.query(Skill)
            .filter(Skill.id == skill_id, Skill.organization_id == PONISHA_ORG_ID)
            .first()
        )
        if not skill:
            result["status"] = "error"
            result["errors"].append({"error": f"No Ponisha skill found with internal id {skill_id}"})
            return result

        ponisha_skill_id = skill.scraped_id

        page = 1
        max_retries_per_page = 3
        consecutive_page_failures = 0
        max_consecutive_page_failures = 5

        while True:
            payload = None
            last_error = None

            for attempt in range(1, max_retries_per_page + 1):
                try:
                    payload = self._fetch_freelancers_page(ponisha_skill_id, page)
                    break
                except Exception as e:
                    last_error = e
                    print(f"⚠️  Freelancer page {page} attempt {attempt}/{max_retries_per_page} failed: {e}")

            if payload is None:
                result["errors"].append({"page": page, "error": str(last_error)})
                consecutive_page_failures += 1
                print(f"❌ Freelancer page {page} failed after {max_retries_per_page} attempts. "
                      f"Consecutive failed pages: {consecutive_page_failures}")
                if consecutive_page_failures >= max_consecutive_page_failures:
                    result["stopped_reason"] = "too_many_consecutive_page_failures"
                    print(f"🛑 Stopping: {max_consecutive_page_failures} consecutive page failures "
                          f"for skill {skill_id}.")
                    break
                page += 1
                continue

            consecutive_page_failures = 0
            freelancers = payload.get("data", [])

            if not freelancers:
                result["stopped_reason"] = "no_more_freelancers"
                break

            last_username = freelancers[-1].get("username")
            last_has_projects = self._has_completed_projects(last_username) if last_username else False
            result["freelancers_checked"] += 1

            if last_has_projects:
                # Whole page qualifies — store everyone, no individual checks needed.
                print(f"  Page {page}: last item ({last_username}) has projects — storing full page "
                      f"({len(freelancers)} freelancers)")
                self._store_freelancers(freelancers, category_id, result)
                self.db.commit()

                pagination = payload.get("meta", {}).get("pagination", {})
                total_pages = int(pagination.get("total_pages", page))
                if page >= total_pages:
                    result["stopped_reason"] = "reached_last_page"
                    break
                page += 1
                continue

            # Last item has zero — scan backward to find the real cutoff within this page.
            print(f"  Page {page}: last item ({last_username}) has 0 projects — scanning backward for cutoff")
            cutoff_index = None
            for i in range(len(freelancers) - 2, -1, -1):  # start one before the last, go to index 0
                username = freelancers[i].get("username")
                if not username:
                    continue
                result["freelancers_checked"] += 1
                if self._has_completed_projects(username):
                    cutoff_index = i
                    print(f"   Found cutoff at index {i} ({username}): has projects")
                    break
                print(f"   Index {i} ({username}): 0 projects, continuing backward")

            if cutoff_index is not None:
                qualifying = freelancers[: cutoff_index + 1]
                print(f"  Storing {len(qualifying)} freelancers up to and including the cutoff")
                self._store_freelancers(qualifying, category_id, result)
            else:
                print(f"  No freelancer on page {page} has any completed projects — storing none from this page")

            self.db.commit()
            result["stopped_reason"] = "found_zero_cutoff"
            print(f"🛑 Stopping entirely: found the zero-project cutoff on page {page}.")
            break

        return result

    def _store_freelancers(self, freelancers: list[dict], category_id: str, result: dict) -> None:
        """Stores each freelancer's username as scraped_id, tagged with category_id."""
        for fl in freelancers:
            try:
                username = fl.get("username")
                if not username:
                    continue

                result["freelancers_with_projects"] += 1

                freelancer_data = FreelancerCreate(scraped_id=username, category_id=category_id)
                existing = freelancer_crud.get_freelancer(self.db, username, category_id)
                freelancer_crud.create_or_get_freelancer(self.db, freelancer_data)

                if existing:
                    result["freelancers_already_existed"] += 1
                else:
                    result["freelancers_added"] += 1
                    print(f"   ✅ {username}: stored")

            except Exception as e:
                self.db.rollback()
                result["errors"].append({"freelancer": fl.get("username", "unknown"), "error": str(e)})

    def _has_completed_projects(self, username: str) -> bool:
        """
        Cheap existence check: does this freelancer have at least one
        completed (status=100) project? Uses per_page=1 since we only need
        to know if the list is non-empty, not the actual project data.
        """
        url = f"{settings.PONISHA_API_URL}/api/v1/projects/{username}/list"
        params = {"status": 100, "paginated": 1, "page": 1, "per_page": 1}

        max_429_retries = 5
        for attempt in range(max_429_retries + 1):
            self.rate_limiter.wait()
            try:
                response = httpx.get(url, params=params, timeout=10)
            except httpx.TimeoutException:
                print(f"⏱️  Timeout checking completed projects for {username}")
                raise

            if response.status_code == 429:
                self.rate_limiter.report_failure(response.status_code, source="ponisha-freelancer-projects-check")
                if attempt < max_429_retries:
                    print(f"🔁 429 checking projects for {username}, retrying "
                          f"(attempt {attempt + 1}/{max_429_retries})")
                    continue
            elif response.status_code == 403:
                self.rate_limiter.report_failure(response.status_code, source="ponisha-freelancer-projects-check")

            break

        response.raise_for_status()
        self.rate_limiter.report_success()
        payload = response.json()

        return len(payload.get("data", [])) > 0

    def _fetch_freelancers_page(self, ponisha_skill_id: str, page: int) -> dict:
        url = f"{settings.PONISHA_SEARCH_URL}/v1/freelancers/search"
        body = {
            "query": "",
            "page": page,
            "per_page": 20,
            "sort": "completed_project_count",
            "order": "desc",
            "filters": {
                "has_portfolio": False,
                "skills": [ponisha_skill_id],
                "category": [],
                "sub_category": [],
                "city": [],
                "country": [],
            },
        }

        max_429_retries = 5
        for attempt in range(max_429_retries + 1):
            self.rate_limiter.wait()
            try:
                response = httpx.post(url, json=body, timeout=10)
            except httpx.TimeoutException:
                print(f"⏱️  Timeout searching freelancers for skill {ponisha_skill_id}, page {page}")
                raise

            if response.status_code == 429:
                self.rate_limiter.report_failure(response.status_code, source="ponisha-freelancers")
                if attempt < max_429_retries:
                    print(f"🔁 429 on freelancer search for skill {ponisha_skill_id} page {page}, "
                          f"retrying (attempt {attempt + 1}/{max_429_retries})")
                    continue
            elif response.status_code == 403:
                self.rate_limiter.report_failure(response.status_code, source="ponisha-freelancers")

            break

        response.raise_for_status()
        self.rate_limiter.report_success()
        return response.json()