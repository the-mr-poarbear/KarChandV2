import httpx
from datetime import datetime
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import skill as skill_crud
from app.models.category import Category
from app.models.project import Project
from app.models.project_skill import ProjectSkill
from app.schemas.skill import SkillCreate
from app.services.rate_limiter import AdaptiveRateLimiter

PONISHA_ORG_ID = "22222222-2222-2222-2222-222222222222"  # TODO: replace with real UUID


class PonishaProjectScraperService:
    """
    For each stored Ponisha category:
      - search projects (open AND closed) in that category, paginated
      - skip projects with fewer than min_bidders total bids (project_bids_count)
      - for each remaining project, fetch its bids list and find the accepted
        bid (first item in the array, with non-zero amount) for final price + days
      - if no valid accepted bid is found, save the project anyway with
        final_budget = None, rather than skipping it
      - save the Project row, upsert Skill rows (tagged with Ponisha's org id),
        and link them via ProjectSkill
    """

    def __init__(self, db: Session, rate_limiter: AdaptiveRateLimiter | None = None):
        self.db = db
        self.rate_limiter = rate_limiter or AdaptiveRateLimiter()

    def scrape_all_categories(
        self, max_workers: int = 5, update_existing: bool = False, min_bidders: int = 3
    ) -> dict:
        """
        Loops over every stored Ponisha Category and runs scrape_category for
        each one, in parallel using a thread pool. All workers share one
        AdaptiveRateLimiter so backoff applies globally, not per-thread.

        Each worker opens its own DB session — SQLAlchemy sessions are not
        thread-safe, so self.db is never touched by worker threads directly.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from app.core.database import SessionLocal

        overall = {
            "status": "success",
            "categories_processed": 0,
            "projects_added": 0,
            "projects_updated": 0,
            "projects_skipped": 0,
            "projects_skipped_low_bidders": 0,
            "errors": [],
            "final_request_delay_seconds": None,
            "total_backoffs": 0,
        }

        categories = (
            self.db.query(Category)
            .filter(Category.organization_id == PONISHA_ORG_ID)
            .all()
        )

        shared_limiter = self.rate_limiter

        def worker(category_id: str) -> dict:
            db = SessionLocal()
            try:
                service = PonishaProjectScraperService(db, rate_limiter=shared_limiter)
                return service.scrape_category(
                    category_id, update_existing=update_existing, min_bidders=min_bidders
                )
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(worker, category.id): category
                for category in categories
            }

            for future in as_completed(futures):
                category = futures[future]
                try:
                    single = future.result()
                    overall["categories_processed"] += 1
                    overall["projects_added"] += single["projects_added"]
                    overall["projects_updated"] += single["projects_updated"]
                    overall["projects_skipped"] += single["projects_skipped"]
                    overall["projects_skipped_low_bidders"] += single["projects_skipped_low_bidders"]
                    if single["errors"]:
                        overall["errors"].append({"category_id": category.id, "errors": single["errors"]})
                except Exception as e:
                    overall["errors"].append({"category_id": category.id, "error": str(e)})

        overall["final_request_delay_seconds"] = shared_limiter.current_delay()
        overall["total_backoffs"] = shared_limiter.total_backoffs
        return overall

    @staticmethod
    def _describe_exception(e: Exception) -> str:
        """
        SQLAlchemy exceptions (IntegrityError, DataError, etc.) often wrap the
        real database message in `.orig` — str(e) alone can be long and
        unclear. This pulls out the underlying driver error when present,
        falling back to a normal traceback otherwise.
        """
        import traceback

        orig = getattr(e, "orig", None)
        if orig is not None:
            return f"{type(e).__name__}: {orig}"

        return f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    def scrape_category(
        self, internal_category_id: str, update_existing: bool = False, min_bidders: int = 3
    ) -> dict:
        """
        internal_category_id is YOUR Category.id (UUID). We resolve it to
        Ponisha's own category id (scraped_id) before calling their search API.

        min_bidders: projects with fewer than this many total bids
        (project_bids_count from the search response) are skipped entirely —
        too few bidders means too little signal on a realistic market price.
        """
        result = {
            "status": "success",
            "category_id": internal_category_id,
            "projects_found": 0,
            "projects_added": 0,
            "projects_updated": 0,
            "projects_skipped": 0,
            "projects_skipped_low_bidders": 0,
            "errors": [],
        }

        category = (
            self.db.query(Category)
            .filter(Category.id == internal_category_id, Category.organization_id == PONISHA_ORG_ID)
            .first()
        )
        if not category:
            result["status"] = "error"
            result["errors"].append({"error": f"No Ponisha category found with internal id {internal_category_id}"})
            return result

        ponisha_category_id = category.scraped_id

        page = 1
        while True:
            try:
                payload = self._search_projects(ponisha_category_id, page)
            except Exception as e:
                result["errors"].append({"page": page, "error": str(e)})
                break

            projects = payload.get("data", [])

            for proj in projects:
                try:
                    result["projects_found"] += 1

                    bid_count = int(proj.get("project_bids_count") or 0)
                    if bid_count < min_bidders:
                        result["projects_skipped_low_bidders"] += 1
                        continue

                    outcome = self._process_project(proj, category.id, update_existing=update_existing)
                    if outcome == "added":
                        result["projects_added"] += 1
                    elif outcome == "updated":
                        result["projects_updated"] += 1
                    else:  # "skipped"
                        result["projects_skipped"] += 1
                except Exception as e:
                    self.db.rollback()  # clear the failed transaction so the next project isn't blocked
                    error_detail = self._describe_exception(e)
                    print(f"❌ Project {proj.get('id', 'unknown')} ({proj.get('title', '?')}) failed: {error_detail}")
                    result["errors"].append({
                        "project_id": proj.get("id", "unknown"),
                        "title": proj.get("title", "unknown"),
                        "error": error_detail,
                    })

            pagination = payload.get("meta", {}).get("pagination", {})
            total_pages = int(pagination.get("total_pages", page))

            self.db.commit()  # commit per page, same reasoning as the Karlancer scraper

            if page >= total_pages:
                break
            page += 1

        return result

    def _process_project(self, proj: dict, internal_category_id: str, update_existing: bool = False) -> str:
        scraped_project_id = str(proj.get("id"))
        title = proj.get("title", "Untitled")

        print(f"📦 Fetching: {title}")

        if not update_existing:
            already_exists = self._find_existing_project(scraped_project_id, internal_category_id)
            if already_exists:
                print(f"   ⏭️  Skipped (already exists): {title}")
                return "skipped"

        bids = self._fetch_bids_page(scraped_project_id)
        accepted_bid = self._extract_accepted_bid(bids)

        if accepted_bid is None:
            final_budget = None
            days = self._average_days(bids)  # fallback: average of real bidders' days on page 1
            print(f"   ℹ️  No valid accepted bid yet — saving with final_budget=null, "
                  f"days=avg({days}): {title}")
        else:
            final_budget = accepted_bid.get("amount")
            days = accepted_bid.get("days")

        existing = self._find_existing_project(scraped_project_id, internal_category_id)

        if existing:
            if not update_existing:
                return "skipped"
            self._update_project_fields(existing, proj, internal_category_id, final_budget, days)
            self._sync_project_skills(existing, proj.get("skills", []))
            print(f"   ✅ Updated: {title}")
            return "updated"
        else:
            new_project = Project(scraped_project_id=scraped_project_id)
            self._update_project_fields(new_project, proj, internal_category_id, final_budget, days)
            self.db.add(new_project)
            self.db.flush()  # get new_project.id before linking skills
            self._sync_project_skills(new_project, proj.get("skills", []))
            print(f"   ✅ Added: {title}")
            return "added"

    def _find_existing_project(self, scraped_project_id: str, internal_category_id: str) -> Project | None:
        """
        Looks up a Project by (scraped_project_id, category_id) — matching the
        composite unique constraint on the Project table. A raw id alone is
        NOT enough: two different categories (including across different
        organizations) could coincidentally share the same scraped_project_id,
        but a project never changes category once created, so category_id is
        a safe, permanent scoping key.
        """
        return (
            self.db.query(Project)
            .filter(
                Project.scraped_project_id == scraped_project_id,
                Project.category_id == internal_category_id,
            )
            .first()
        )

    def _update_project_fields(
        self, project: Project, proj: dict, internal_category_id: str, final_budget, days
    ) -> None:
        project.title = proj.get("title")
        project.description = proj.get("description")
        project.outer_link = f"https://ponisha.ir/projects/{proj.get('slug')}"
        project.duration = f"{days}" if days is not None else None
        project.budget_min = proj.get("amount_min")
        project.budget_max = proj.get("amount_max")
        project.final_budget = final_budget
        project.category_id = internal_category_id
        project.scraped_date_created = self._to_datetime(proj.get("approved_at"))

    @staticmethod
    def _to_datetime(value) -> datetime | None:
        """Ponisha returns Unix timestamps (seconds) as integers, not ISO strings."""
        if value is None:
            return None
        try:
            return datetime.fromtimestamp(int(value))
        except (ValueError, TypeError, OSError):
            return None

    def _sync_project_skills(self, project: Project, skills: list) -> None:
        for sk in skills:
            scraped_skill_id = sk.get("id")
            name = sk.get("title")
            if not scraped_skill_id or not name:
                continue

            skill_data = SkillCreate(
                scraped_id=str(scraped_skill_id),
                organization_id=PONISHA_ORG_ID,
                name=name,
            )
            skill_obj = skill_crud.create_or_update_skill(self.db, skill_data)

            already_linked = (
                self.db.query(ProjectSkill)
                .filter(ProjectSkill.project_id == project.id, ProjectSkill.skill_id == skill_obj.id)
                .first()
            )
            if not already_linked:
                self.db.add(ProjectSkill(project_id=project.id, skill_id=skill_obj.id))

    def _fetch_bids_page(self, scraped_project_id: str) -> list[dict]:
        """
        Fetches page 1 of a project's bids list. Returns the raw list of bids
        (empty list if none). Used both to find the accepted bid and, as a
        fallback, to compute an average 'days' across all real bidders.
        """
        url = f"{settings.PONISHA_API_URL}/api/v1/projects/{scraped_project_id}/bids/list"
        params = {"paginated": 1, "per_page": 12, "page": 1}

        max_429_retries = 5
        for attempt in range(max_429_retries + 1):
            self.rate_limiter.wait()
            try:
                response = httpx.get(url, params=params, timeout=10)
            except httpx.TimeoutException:
                # Timeouts are NOT treated as a rate-limit signal — could be normal
                # server slowness or a local network blip, unrelated to request rate.
                print(f"⏱️  Timeout fetching bids for project {scraped_project_id}")
                raise

            if response.status_code == 429:
                self.rate_limiter.report_failure(response.status_code, source="ponisha-bids")
                if attempt < max_429_retries:
                    print(f"🔁 429 on bids for project {scraped_project_id}, "
                          f"retrying (attempt {attempt + 1}/{max_429_retries})")
                    continue
                # exhausted retries — fall through to raise_for_status below
            elif response.status_code == 403:
                self.rate_limiter.report_failure(response.status_code, source="ponisha-bids")

            break  # got a non-429 response (success or a non-retryable failure)

        response.raise_for_status()
        self.rate_limiter.report_success()
        payload = response.json()

        return payload.get("data", [])

    @staticmethod
    def _extract_accepted_bid(bids: list[dict]) -> dict | None:
        """
        Returns the accepted bid dict (containing amount + days) if the first
        bid in the list has a non-zero amount, else None.
        """
        if not bids:
            return None

        first_bid = bids[0]
        amount = first_bid.get("amount")
        if not amount or float(amount) == 0:
            return None

        return first_bid

    @staticmethod
    def _average_days(bids: list[dict]) -> float | None:
        """
        Average 'days' across all bids that have a days value. Used as a
        fallback when there's no valid accepted bid yet (i.e. every bid's
        amount is still 0, since no price has been finalized) — so duration
        isn't left empty in that case. We deliberately do NOT filter by
        amount here: amount==0 is the expected, normal state for every bid
        on a project with no accepted bid yet, not a sign of an invalid bid.
        """
        valid_days = [bid.get("days") for bid in bids if bid.get("days") is not None]
        if not valid_days:
            return None
        return round(sum(valid_days) / len(valid_days))

    def _search_projects(self, ponisha_category_id: str, page: int) -> dict:
        url = f"{settings.PONISHA_SEARCH_URL}/v1/projects/search"
        body = {
            "query": "",
            "page": page,
            "per_page": 20,
            "sort": "billboarded_at",
            "order": "desc",
            "filters": {
                "skills": [],
                "category": [ponisha_category_id],
                "promotions": [],
            },
        }

        max_429_retries = 5
        for attempt in range(max_429_retries + 1):
            self.rate_limiter.wait()
            try:
                response = httpx.post(url, json=body, timeout=10)
            except httpx.TimeoutException:
                print(f"⏱️  Timeout searching projects for category {ponisha_category_id}, page {page}")
                raise

            if response.status_code == 429:
                self.rate_limiter.report_failure(response.status_code, source="ponisha-search")
                if attempt < max_429_retries:
                    print(f"🔁 429 on search for category {ponisha_category_id} page {page}, "
                          f"retrying (attempt {attempt + 1}/{max_429_retries})")
                    continue
            elif response.status_code == 403:
                self.rate_limiter.report_failure(response.status_code, source="ponisha-search")

            break

        response.raise_for_status()
        self.rate_limiter.report_success()
        return response.json()