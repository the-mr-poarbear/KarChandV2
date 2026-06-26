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


class ProjectScraperService:
    """
    Stages 2-4 of the pipeline.

    For a given freelancer (scraped_id) and category_id:
      - fetch their completed projects in that category (stage 2)
      - for each project, fetch full project detail via its slug id (stage 3)
      - save/update the Project row, using the budget from stage 2 as final_budget (stage 4)
      - build up the Skill table incrementally from each project's skills array (stage 4)
    """

    def __init__(self, db: Session, rate_limiter: AdaptiveRateLimiter | None = None):
        self.db = db
        self.rate_limiter = rate_limiter or AdaptiveRateLimiter()

    def scrape_all_freelancers(
        self,
        max_workers: int = 5,
        update_existing: bool = False,
        resume: bool = False,
        offset: int = 0,
    ) -> dict:
        """
        Loops over stored Freelancer rows and runs scrape_freelancer_projects
        for each one, in parallel using a thread pool. All workers share one
        AdaptiveRateLimiter so backoff applies globally, not per-thread.

        update_existing: if False (default), projects already in the table are
        skipped entirely — not even the detail endpoint is called for them,
        saving a request per already-scraped project. If True, existing
        projects are re-fetched and overwritten.

        resume: if True, only freelancers whose projects_scraped checkpoint is
        still False are processed — i.e. continues from where a previous run
        left off, skipping freelancers already fully completed. If False
        (default), every freelancer is processed regardless of checkpoint
        state.

        offset: optional manual override — skip the first `offset` freelancers
        in the resulting list (after resume filtering, if resume=True is also
        set) before processing. Use this if you know exactly where a previous
        run stopped and want to skip ahead manually, independent of the
        checkpoint flag — e.g. for spot-checking or recovering from a crash
        where you don't trust the checkpoint state. Default 0 (no skipping).

        Each worker opens its own DB session — SQLAlchemy sessions are not
        thread-safe, so self.db is never touched by worker threads directly.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from app.core.database import SessionLocal

        overall = {
            "status": "success",
            "freelancers_processed": 0,
            "freelancers_skipped_checkpoint": 0,
            "projects_added": 0,
            "projects_updated": 0,
            "projects_skipped": 0,
            "errors": [],
            "final_request_delay_seconds": None,
            "total_backoffs": 0,
        }

        if resume:
            freelancers = freelancer_crud.get_unscraped_freelancers(self.db)
            total_all = len(freelancer_crud.get_all_freelancers(self.db))
            overall["freelancers_skipped_checkpoint"] = total_all - len(freelancers)
        else:
            freelancers = freelancer_crud.get_all_freelancers(self.db)

        if offset > 0:
            freelancers = freelancers[offset:]
        overall["offset_applied"] = offset
        overall["freelancers_remaining_after_offset"] = len(freelancers)

        shared_limiter = self.rate_limiter

        def worker(freelancer_id: str, scraped_id: str, category_id: str) -> tuple[str, dict]:
            db = SessionLocal()
            try:
                service = ProjectScraperService(db, rate_limiter=shared_limiter)
                single = service.scrape_freelancer_projects(
                    scraped_id, category_id, update_existing=update_existing
                )
                # Only mark the checkpoint if pagination completed without errors —
                # a partial/failed run should remain eligible for a future resume.
                if not single["errors"]:
                    freelancer_crud.mark_projects_scraped(
                        db, freelancer_id, completed_projects_count=single.get("completed_projects_count")
                    )
                return freelancer_id, single
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(worker, fl.id, fl.scraped_id, fl.category_id): fl
                for fl in freelancers
            }

            for future in as_completed(futures):
                fl = futures[future]
                try:
                    _, single = future.result()
                    overall["freelancers_processed"] += 1
                    overall["projects_added"] += single["projects_added"]
                    overall["projects_updated"] += single["projects_updated"]
                    overall["projects_skipped"] += single["projects_skipped"]
                    if single["errors"]:
                        overall["errors"].append({
                            "scraped_user_id": fl.scraped_id,
                            "category_id": fl.category_id,
                            "errors": single["errors"],
                        })
                except Exception as e:
                    overall["errors"].append({
                        "scraped_user_id": fl.scraped_id,
                        "category_id": fl.category_id,
                        "error": str(e),
                    })

        overall["final_request_delay_seconds"] = shared_limiter.current_delay()
        overall["total_backoffs"] = shared_limiter.total_backoffs
        return overall

    def scrape_freelancer_projects(
        self, scraped_user_id: str, category_id: str, update_existing: bool = False
    ) -> dict:
        """
        category_id here is YOUR internal Category.id (UUID) — matching what's
        stored on the Freelancer row. We resolve it to Karlancer's own category
        id before calling their API, since that's what their endpoint expects.

        update_existing: if False (default), projects already in the table
        (matched by scraped_project_id) are skipped without calling the detail
        endpoint at all. If True, they're re-fetched and overwritten.
        """
        result = {
            "status": "success",
            "scraped_user_id": scraped_user_id,
            "category_id": category_id,
            "projects_found": 0,
            "projects_added": 0,
            "projects_updated": 0,
            "projects_skipped": 0,
            "completed_projects_count": None,
            "errors": [],
        }

        category = self.db.query(Category).filter(Category.id == category_id).first()
        if not category:
            result["status"] = "error"
            result["errors"].append({"error": f"No category found with internal id {category_id}"})
            return result

        karlancer_category_id = category.scraped_id

        page = 1
        while True:
            try:
                payload = self._fetch_completed_projects_page(scraped_user_id, karlancer_category_id, page)
            except Exception as e:
                result["errors"].append({"page": page, "error": str(e)})
                break

            data = payload.get("data", {})
            projects = data.get("data", [])

            # "total" is Karlancer's own reported count for this freelancer/category.
            # Captured every page (cheap) so the final value is whatever the last
            # successful page reported, regardless of how many pages there were.
            total = data.get("total")
            if total is not None:
                try:
                    result["completed_projects_count"] = int(total)
                except (TypeError, ValueError):
                    pass

            for proj in projects:
                try:
                    result["projects_found"] += 1
                    outcome = self._process_project(proj, update_existing=update_existing)
                    if outcome == "added":
                        result["projects_added"] += 1
                    elif outcome == "updated":
                        result["projects_updated"] += 1
                    else:  # "skipped"
                        result["projects_skipped"] += 1

                except Exception as e:
                    result["errors"].append({
                        "project_id": proj.get("project_id", proj.get("id", "unknown")),
                        "error": str(e),
                    })

            # Commit after each page rather than waiting until the whole freelancer
            # is done. This keeps the session's pending-object set small, which
            # keeps autoflush (triggered by every subsequent query) fast. Without
            # this, a freelancer with many pages slows down progressively as the
            # session's pending state grows.
            self.db.commit()

            last_page = data.get("last_page", page)
            if page >= int(last_page):
                break
            page += 1

        self.db.commit()
        return result

    def _process_project(self, completed_project_entry: dict, update_existing: bool = False) -> str:
        """
        Given one entry from the completed-projects list, fetch full detail
        and upsert the Project row + its skills.

        Returns "added", "updated", or "skipped". When update_existing is
        False, existence is checked using the id already present on the
        completed-projects entry — BEFORE calling the detail endpoint — so
        already-scraped projects cost zero extra HTTP requests.
        """
        entry_title = completed_project_entry.get("title", "Untitled")
        entry_project_id = str(completed_project_entry.get("project_id") or completed_project_entry.get("id") or "")

        print(f"📦 Fetching: {entry_title}")

        if not update_existing and entry_project_id:
            already_exists = (
                self.db.query(Project.id)
                .filter(Project.scraped_project_id == entry_project_id)
                .first()
            )
            if already_exists:
                print(f"   ⏭️  Skipped (already exists): {entry_title}")
                return "skipped"

        final_budget = completed_project_entry.get("budget")
        project_url = completed_project_entry.get("url", "")

        slug_id = self._extract_slug_id(project_url)
        if not slug_id:
            raise ValueError(f"Could not extract slug id from url: {project_url}")

        detail = self._fetch_project_detail(slug_id)
        print(f"   ✅ Fetched detail: {detail.get('title', entry_title)}")

        existing = self.db.query(Project).filter(Project.scraped_project_id == str(detail["id"])).first()

        if existing:
            if not update_existing:
                # Edge case: entry_project_id didn't match but the detail-confirmed id does.
                # Treat consistently with the "skip, don't touch" contract.
                return "skipped"
            self._update_project_fields(existing, detail, final_budget)
            self._sync_project_skills(existing, detail.get("skills", []))
            return "updated"
        else:
            new_project = Project(scraped_project_id=str(detail["id"]))
            self._update_project_fields(new_project, detail, final_budget)
            self.db.add(new_project)
            self.db.flush()  # get new_project.id before linking skills
            self._sync_project_skills(new_project, detail.get("skills", []))
            return "added"

    def _update_project_fields(self, project: Project, detail: dict, final_budget) -> None:
        project.title = detail.get("title")
        project.description = detail.get("description")
        project.outer_link = detail.get("url")
        project.duration = detail.get("job_duration")
        project.budget_min = detail.get("min_budget")
        project.budget_max = detail.get("max_budget")
        project.final_budget = final_budget
        project.scraped_date_created = detail.get("created_at")

        scraped_category_id = detail.get("category_id")
        if scraped_category_id:
            category = (
                self.db.query(Category)
                .filter(Category.scraped_id == str(scraped_category_id))
                .first()
            )
            if not category:
                raise ValueError(
                    f"Project detail references category scraped_id={scraped_category_id}, "
                    f"but no matching Category row exists. Run the category scraper first."
                )
            project.category_id = category.id
        else:
            project.category_id = None

    def _sync_project_skills(self, project: Project, skills: list) -> None:
        """Upsert each skill from the project detail, link it to the project if not already linked."""
        # Get organization_id from project's category
        # Make sure project.category is loaded (use joinedload if needed)
        organization_id = project.category.organization_id if project.category else None
        
        if not organization_id:
            # Handle case where category or organization_id is missing
            raise ValueError(f"Project {project.id} has no organization associated")
        
        for sk in skills:
            scraped_skill_id = sk.get("id")
            name = sk.get("name") or sk.get("display")
            if not scraped_skill_id or not name:
                continue

            skill_data = SkillCreate(
                scraped_id=str(scraped_skill_id), 
                name=name, 
                organization_id=organization_id  # Now using the organization_id
            )
            skill_obj = skill_crud.create_or_update_skill(self.db, skill_data)

            already_linked = (
                self.db.query(ProjectSkill)
                .filter(ProjectSkill.project_id == project.id, ProjectSkill.skill_id == skill_obj.id)
                .first()
            )
            if not already_linked:
                self.db.add(ProjectSkill(project_id=project.id, skill_id=skill_obj.id))
                
    @staticmethod
    def _extract_slug_id(url: str) -> str | None:
        """The id is always the last '-'-separated segment of the slug."""
        if not url:
            return None
        last_segment = url.rstrip("/").split("/")[-1]
        parts = last_segment.split("-")
        return parts[-1] if parts else None

    def _fetch_completed_projects_page(self, scraped_user_id: str, category_id: str, page: int) -> dict:
        url = f"{settings.KARLANCER_URL}/api/publics/users/{scraped_user_id}/completed-projects"
        params = {"q": "", "page": page, "category_id": category_id}

        self.rate_limiter.wait()
        try:
            response = httpx.get(url, params=params, timeout=10)
        except httpx.TimeoutException:
            self.rate_limiter.report_failure(source="completed-projects")
            raise

        if response.status_code in (429, 403):
            self.rate_limiter.report_failure(response.status_code, source="completed-projects")
        response.raise_for_status()
        payload = response.json()

        if payload.get("status") != "success":
            raise ValueError(f"Completed-projects API returned error: {payload.get('error')}")

        return payload

    def _fetch_project_detail(self, slug_id: str) -> dict:
        url = f"{settings.KARLANCER_URL}/api/publics/projects/{slug_id}?logged_in=1"
        headers = {"Authorization": f"Bearer {settings.KARLANCER_TOKEN}"}

        self.rate_limiter.wait()
        try:
            response = httpx.get(url, timeout=10, headers=headers)
        except httpx.TimeoutException:
            self.rate_limiter.report_failure(source="project-detail")
            raise

        if response.status_code in (429, 403):
            self.rate_limiter.report_failure(response.status_code, source="project-detail")
        response.raise_for_status()
        payload = response.json()

        if payload.get("status") != "success":
            raise ValueError(f"Project detail API returned error: {payload.get('error')}")

        return payload["data"]