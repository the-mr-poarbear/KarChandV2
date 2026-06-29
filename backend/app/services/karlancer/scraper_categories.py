from urllib.parse import parse_qs, urlparse
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from playwright.sync_api import sync_playwright

from app.core.config import settings
from app.crud import category as category_crud
from app.schemas.category import CategoryCreate

class ScraperService:
    """Handles all scraping business logic - NO DB operations here"""
    
    def __init__(self, db: Session):
        self.db = db
        self.base_url = settings.KARLANCER_URL
        
    def scrape_and_update(self, organization_id: str) -> Dict:
        """Main orchestration method - UPDATE existing, ADD new, scoped to one organization"""
        result = {
            "status": "success",
            "categories_found": 0,
            "categories_added": 0,
            "categories_updated": 0,
            "categories_removed": 0,
            "errors": []
        }
        
        # Get existing categories from DB, scoped to this organization
        existing_categories = category_crud.get_categories_by_organization(self.db, organization_id)
        existing_scraped_ids = {cat.scraped_id for cat in existing_categories}
        
        # Scrape categories from Karlancer
        scraped_categories = self._scrape_categories()
        result["categories_found"] = len(scraped_categories)
        
        # Track scraped IDs to find removed categories later
        current_scraped_ids = set()
        
        # Process each scraped category
        for cat in scraped_categories:
            try:
                category_data = self._scrape_category_details(cat, organization_id)
                if not category_data:
                    continue
                
                current_scraped_ids.add(category_data.scraped_id)
                
                # Check if category exists (scoped to this organization)
                existing = category_crud.get_category_by_scraped_id(
                    self.db, 
                    category_data.scraped_id,
                    organization_id,
                )
                
                if existing:
                    # UPDATE existing category
                    category_crud.update_category_by_scraped_id(
                        self.db,
                        category_data.scraped_id,
                        organization_id,
                        category_data,
                    )
                    result["categories_updated"] += 1
                    print(f"✅ Updated: {category_data.name}")
                else:
                    # ADD new category
                    category_crud.create_or_update_category(
                        self.db,
                        category_data
                    )
                    result["categories_added"] += 1
                    print(f"➕ Added: {category_data.name}")
                    
            except Exception as e:
                result["errors"].append({
                    "category": cat.get("name", "unknown"),
                    "error": str(e)
                })
        
        # Find and handle removed categories (optional)
        removed_ids = existing_scraped_ids - current_scraped_ids
        if removed_ids:
            print(f"⚠️ Categories no longer in Karlancer: {len(removed_ids)}")
            # Option 1: Delete them (but projects will become orphaned)
            # for scraped_id in removed_ids:
            #     category_crud.delete_category_by_scraped_id(self.db, scraped_id, organization_id)
            # result["categories_removed"] = len(removed_ids)
            
            # Option 2: Mark as inactive (recommended - keep data)
            # You'd need an 'is_active' column for this
            # for scraped_id in removed_ids:
            #     category_crud.mark_category_inactive(self.db, scraped_id, organization_id)
            
            # Option 3: Just log them (do nothing - keep in DB)
            print(f"  Categories to review: {removed_ids}")
            result["categories_removed"] = len(removed_ids)
        
        self.db.commit()
        
        # Print summary
        print("\n=== Scraping Summary ===")
        print(f"Found: {result['categories_found']}")
        print(f"Added: {result['categories_added']}")
        print(f"Updated: {result['categories_updated']}")
        print(f"Removed from source: {result['categories_removed']}")
        if result['errors']:
            print(f"Errors: {len(result['errors'])}")
        
        return result
    
    def _scrape_categories(self) -> List[Dict]:
        """Scrape category links from mega menu"""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=settings.SCRAPER_HEADLESS)
            page = browser.new_page()
            
            try:
                print("Navigating to Karlancer...")
                page.goto(self.base_url)
                
                print("Hovering over earnMoney menu...")
                page.hover("#earnMoney")
                page.wait_for_selector("app-mega-menu a", timeout=settings.SCRAPER_TIMEOUT)
                
                print("Finding category links...")
                links = page.locator("app-mega-menu div > div > div:nth-of-type(2) > div > a")
                count = links.count()
                print(f"Found {count} categories")
                
                categories = []
                for i in range(count):
                    text = links.nth(i).inner_text().strip()
                    href = links.nth(i).get_attribute("href")
                    categories.append({"name": text, "url": href})
                    print(f"  {text} → {href}")
                
                return categories
                
            except Exception as e:
                print(f"Error scraping categories: {e}")
                raise
            finally:
                browser.close()
    
    def _scrape_category_details(self, category: Dict, organization_id: str) -> Optional[CategoryCreate]:
        """Scrape details for a specific category"""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=settings.SCRAPER_HEADLESS)
            page = browser.new_page()
            
            try:
                url = self.base_url + category["url"]
                print(f"Visiting category: {category['name']} → {url}")
                page.goto(url)
                
                # Wait for page to load
                page.wait_for_timeout(1000)
                
                # Click to get category ID from URL
                page.click("#pagenum")
                current_url = page.url
                
                # Extract category ID from URL
                parsed_url = urlparse(current_url)
                query_params = parse_qs(parsed_url.query)
                category_id = query_params.get("category_id", [""])[0]
                
                if not category_id:
                    print(f"No category_id found for {category['name']}")
                    return None
                
                print(f"Found category ID: {category_id}")
                
                # Get description
                try:
                    desc = page.locator("#top-contents div").inner_text().strip()
                except:
                    desc = "No description available"
            
                return CategoryCreate(
                    scraped_id=category_id,
                    organization_id=organization_id,
                    name=category["name"],
                    description=desc,
                )
                
            except Exception as e:
                print(f"Error scraping {category['name']}: {e}")
                return None
            finally:
                browser.close()