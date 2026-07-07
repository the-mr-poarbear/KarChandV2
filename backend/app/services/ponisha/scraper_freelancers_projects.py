import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import freelancer as freelancer_crud
from app.crud import skill as skill_crud
from app.models.category import Category
from app.models.project import Project
from app.models.project_skill import ProjectSkill
from app.schemas.skill import SkillCreate
from app.services.rate_limiter import AdaptiveRateLimiter

PONISHA_ORG_ID = "22222222-2222-2222-2222-222222222222"  # TODO: replace with real UUID


class PonishaFreelancerProjectScraperService:
    """
    Stage 2 of the Ponisha freelancer-based pipeline.

    For a stored Ponisha Freelancer row (scraped_id = username, category_id
    = internal Category.id):
      - list their completed (status=100) projects, paginated
      - for each project, fetch full detail to get accepted_price, skills,
        and skill_category
      - skip the project entirely if skill_category.id doesn't match the
        category we're scraping for (a freelancer's completed work can span
        multiple categories; we only keep what matches this run's category)
      - save the Project row (duration/days is always null here — not
        available from this data source), upsert Skill rows, link via
        ProjectSkill
    """

    def __init__(self, db: Session, rate_limiter: AdaptiveRateLimiter | None = None):
        self.db = db
        self.rate_limiter = rate_limiter or AdaptiveRateLimiter()

    def scrape_all_freelancers(
        self,
        max_workers: int = 5,
        update_existing: bool = False,
        category_id: str | None = None,
    ) -> dict:
        """
        Loops over stored Freelancer rows whose category belongs to Ponisha,
        running scrape_freelancer for each in parallel.

        category_id: optional. If given (your internal Category.id UUID),
        only freelancers tagged with that category are processed, and each
        one's projects are filtered to that same category — same behavior
        as scrape_freelancer already does for a single freelancer. If
        omitted, every stored Ponisha freelancer is processed regardless of
        which category they were tagged with.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from app.core.database import SessionLocal

        overall = {
            "status": "success",
            "freelancers_processed": 0,
            "projects_added": 0,
            "projects_updated": 0,
            "projects_skipped": 0,
            "projects_skipped_wrong_category": 0,
            "errors": [],
            "final_request_delay_seconds": None,
            "total_backoffs": 0,
        }

        query = (
            self.db.query(freelancer_crud.Freelancer)
            .join(Category, freelancer_crud.Freelancer.category_id == Category.id)
            .filter(Category.organization_id == PONISHA_ORG_ID)
        )
        if category_id is not None:
            query = query.filter(freelancer_crud.Freelancer.category_id == category_id)
        freelancers = query.all()

        if category_id is not None and not freelancers:
            overall["status"] = "error"
            overall["errors"].append({
                "error": f"No Ponisha freelancers found tagged with internal category id {category_id}"
            })
            return overall

        shared_limiter = self.rate_limiter

        def worker(username: str, category_id: str) -> dict:
            db = SessionLocal()
            try:
                service = PonishaFreelancerProjectScraperService(db, rate_limiter=shared_limiter)
                return service.scrape_freelancer(username, category_id, update_existing=update_existing)
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(worker, fl.scraped_id, fl.category_id): fl
                for fl in freelancers
            }

            for future in as_completed(futures):
                fl = futures[future]
                try:
                    single = future.result()
                    overall["freelancers_processed"] += 1
                    overall["projects_added"] += single["projects_added"]
                    overall["projects_updated"] += single["projects_updated"]
                    overall["projects_skipped"] += single["projects_skipped"]
                    overall["projects_skipped_wrong_category"] += single["projects_skipped_wrong_category"]
                    if single["errors"]:
                        overall["errors"].append({
                            "username": fl.scraped_id,
                            "category_id": fl.category_id,
                            "errors": single["errors"],
                        })
                except Exception as e:
                    overall["errors"].append({
                        "username": fl.scraped_id,
                        "category_id": fl.category_id,
                        "error": str(e),
                    })

        overall["final_request_delay_seconds"] = shared_limiter.current_delay()
        overall["total_backoffs"] = shared_limiter.total_backoffs
        return overall

    def scrape_freelancer(
        self, username: str, internal_category_id: str, update_existing: bool = False
    ) -> dict:
        """
        username: the Freelancer row's scraped_id (stores username for Ponisha).
        internal_category_id: YOUR Category.id (UUID) this freelancer was
        tagged with. Used both to resolve Ponisha's own category id (for
        filtering matches) and to save under the correct category_id.
        """
        result = {
            "status": "success",
            "username": username,
            "category_id": internal_category_id,
            "projects_found": 0,
            "projects_added": 0,
            "projects_updated": 0,
            "projects_skipped": 0,
            "projects_skipped_wrong_category": 0,
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

        expected_ponisha_category_id = category.scraped_id

        page = 1
        while True:
            try:
                payload = self._fetch_completed_projects_page(username, page)
            except Exception as e:
                result["errors"].append({"page": page, "error": str(e)})
                break

            projects = payload.get("data", [])

            if not update_existing:
                projects_to_process, already_skipped = self._filter_new_projects(projects, internal_category_id)
                result["projects_skipped"] += already_skipped
                for proj in projects:
                    result["projects_found"] += 1
            else:
                projects_to_process = projects
                result["projects_found"] += len(projects)

            for proj in projects_to_process:
                try:
                    outcome = self._process_project(
                        proj, internal_category_id, expected_ponisha_category_id, update_existing=update_existing
                    )
                    if outcome == "added":
                        result["projects_added"] += 1
                    elif outcome == "updated":
                        result["projects_updated"] += 1
                    elif outcome == "wrong_category":
                        result["projects_skipped_wrong_category"] += 1
                    else:  # "skipped" - shouldn't normally happen here since
                        # already-existing ones were filtered out above, but
                        # kept as a safe fallback (e.g. update_existing=True
                        # case where _process_project still does its own check)
                        result["projects_skipped"] += 1
                except Exception as e:
                    self.db.rollback()
                    result["errors"].append({"project_id": proj.get("id", "unknown"), "error": str(e)})

            pagination = payload.get("meta", {}).get("pagination", {})
            total_pages = int(pagination.get("total_pages", page))

            self.db.commit()

            if page >= total_pages:
                break
            page += 1

        return result

    def _filter_new_projects(
        self, projects: list[dict], internal_category_id: str
    ) -> tuple[list[dict], int]:
        """
        Given one page of project list entries, returns (new_projects,
        skipped_count) — filtering out any whose scraped_project_id already
        exists for this category, using ONE bulk query for the whole page
        instead of one query per project. This avoids fetching project
        detail (a separate HTTP call) for anything we'd just skip anyway.
        """
        page_ids = [str(p.get("id")) for p in projects if p.get("id") is not None]
        if not page_ids:
            return [], 0

        existing_ids = {
            row[0]
            for row in (
                self.db.query(Project.scraped_project_id)
                .filter(
                    Project.scraped_project_id.in_(page_ids),
                    Project.category_id == internal_category_id,
                )
                .all()
            )
        }

        new_projects = []
        skipped_count = 0
        for proj in projects:
            scraped_id = str(proj.get("id"))
            if scraped_id in existing_ids:
                print(f"   ⏭️  Skipped (already exists): {proj.get('title', 'Untitled')}")
                skipped_count += 1
            else:
                new_projects.append(proj)

        return new_projects, skipped_count

    def _process_project(
        self,
        proj: dict,
        internal_category_id: str,
        expected_ponisha_category_id: str,
        update_existing: bool = False,
    ) -> str:
        scraped_project_id = str(proj.get("id"))
        title = proj.get("title", "Untitled")

        print(f"📦 Fetching: {title}")

        if not update_existing:
            already_exists = self._find_existing_project(scraped_project_id, internal_category_id)
            if already_exists:
                print(f"   ⏭️  Skipped (already exists): {title}")
                return "skipped"

        detail = self._fetch_project_detail(scraped_project_id)

        actual_category = detail.get("skill_category", {}) or {}
        actual_ponisha_category_id = str(actual_category.get("id")) if actual_category.get("id") else None

        if actual_ponisha_category_id != str(expected_ponisha_category_id):
            print(f"   ⚠️  Skipped (category mismatch: expected {expected_ponisha_category_id}, "
                  f"got {actual_ponisha_category_id}): {title}")
            return "wrong_category"

        existing = self._find_existing_project(scraped_project_id, internal_category_id)

        if existing:
            if not update_existing:
                return "skipped"
            self._update_project_fields(existing, detail, internal_category_id)
            self._sync_project_skills(existing, detail.get("skills", []))
            print(f"   ✅ Updated: {title}")
            return "updated"
        else:
            new_project = Project(scraped_project_id=scraped_project_id)
            self._update_project_fields(new_project, detail, internal_category_id)
            self.db.add(new_project)
            self.db.flush()
            self._sync_project_skills(new_project, detail.get("skills", []))
            print(f"   ✅ Added: {title}")
            return "added"

    def _find_existing_project(self, scraped_project_id: str, internal_category_id: str) -> Project | None:
        return (
            self.db.query(Project)
            .filter(
                Project.scraped_project_id == scraped_project_id,
                Project.category_id == internal_category_id,
            )
            .first()
        )

    def _update_project_fields(self, project: Project, detail: dict, internal_category_id: str) -> None:
        project.title = detail.get("title")
        project.description = detail.get("description")
        project.outer_link = f"https://ponisha.ir/projects/{detail.get('slug')}"
        project.duration = None  # not available from this data source
        project.budget_min = detail.get("amount_min")
        project.budget_max = detail.get("amount_max")
        project.final_budget = detail.get("accepted_price")
        project.category_id = internal_category_id
        project.scraped_date_created = self._to_datetime(detail.get("created_at"))

    @staticmethod
    def _to_datetime(value):
        from datetime import datetime
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

    def _fetch_completed_projects_page(self, username: str, page: int) -> dict:
        url = f"{settings.PONISHA_API_URL}/api/v1/projects/{username}/list"
        params = {"status": 100, "paginated": 1, "page": page, "per_page": 20}

        max_429_retries = 5
        for attempt in range(max_429_retries + 1):
            self.rate_limiter.wait()
            try:
                response = httpx.get(url, params=params, timeout=10)
            except httpx.TimeoutException:
                print(f"⏱️  Timeout fetching completed projects for {username}, page {page}")
                raise

            if response.status_code == 429:
                self.rate_limiter.report_failure(response.status_code, source="ponisha-freelancer-projects")
                if attempt < max_429_retries:
                    print(f"🔁 429 fetching projects for {username} page {page}, "
                          f"retrying (attempt {attempt + 1}/{max_429_retries})")
                    continue
            elif response.status_code == 403:
                self.rate_limiter.report_failure(response.status_code, source="ponisha-freelancer-projects")

            break

        response.raise_for_status()
        self.rate_limiter.report_success()
        return response.json()

    def _fetch_project_detail(self, scraped_project_id: str) -> dict:
        url = f"{settings.PONISHA_API_URL}/api/v1/projects/{scraped_project_id}"

        max_429_retries = 5
        for attempt in range(max_429_retries + 1):
            self.rate_limiter.wait()
            try:
                response = httpx.get(url, timeout=10)
            except httpx.TimeoutException:
                print(f"⏱️  Timeout fetching project detail for {scraped_project_id}")
                raise

            if response.status_code == 429:
                self.rate_limiter.report_failure(response.status_code, source="ponisha-project-detail")
                if attempt < max_429_retries:
                    print(f"🔁 429 fetching detail for project {scraped_project_id}, "
                          f"retrying (attempt {attempt + 1}/{max_429_retries})")
                    continue
            elif response.status_code == 403:
                self.rate_limiter.report_failure(response.status_code, source="ponisha-project-detail")

            break

        response.raise_for_status()
        self.rate_limiter.report_success()
        payload = response.json()
        return payload["data"]