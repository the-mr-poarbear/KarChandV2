import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import category as category_crud
from app.schemas.category import CategoryCreate

# TODO: replace with the real UUID from `SELECT id, name FROM organizations;`
PONISHA_ORG_ID = "22222222-2222-2222-2222-222222222222"


class PonishaCategoryScraperService:
    """
    Fetches top-level categories from Ponisha's Next.js data endpoint.
    Subcategories (the 'subs' array on each entry) are intentionally ignored —
    only top-level categories are stored.
    """

    def __init__(self, db: Session):
        self.db = db

    def scrape_and_update(self) -> dict:
        result = {
            "status": "success",
            "categories_found": 0,
            "categories_added": 0,
            "categories_updated": 0,
            "errors": [],
        }

        raw_categories = self._fetch_categories()

        for cat in raw_categories:
            try:
                scraped_id = str(cat.get("id"))
                name = cat.get("title")

                if not scraped_id or not name:
                    continue

                result["categories_found"] += 1
                category_data = CategoryCreate(
                    scraped_id=scraped_id,
                    organization_id=PONISHA_ORG_ID,
                    name=name,
                    description=None,  # Ponisha's categories.json has no description field
                )

                existing = category_crud.get_category_by_scraped_id(
                    self.db, scraped_id, PONISHA_ORG_ID
                )
                category_crud.create_or_update_category(self.db, category_data)

                if existing:
                    result["categories_updated"] += 1
                    print(f"✅ Updated category: {name}")
                else:
                    result["categories_added"] += 1
                    print(f"➕ Added category: {name}")

            except Exception as e:
                result["errors"].append({
                    "category": cat.get("title", "unknown"),
                    "error": str(e),
                })

        return result

    def _fetch_categories(self) -> list[dict]:
        """
        Note: the build-id segment in this URL (e.g. 'U6kRXRelD76odln5BJtdj')
        is Next.js's deployment build id and WILL change whenever Ponisha
        redeploys their frontend. If this starts returning 404s, the build id
        in PONISHA_CATEGORIES_URL needs to be refreshed by checking the current
        URL in a browser's network tab.
        """
        url = settings.PONISHA_CATEGORIES_URL
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        payload = response.json()

        try:
            queries = payload["pageProps"]["dehydratedState"]["queries"]
            top_level = queries[0]["state"]["data"]
        except (KeyError, IndexError, TypeError) as e:
            raise ValueError(f"Unexpected categories.json response shape: {e}")

        return top_level