from pydantic import field_validator
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # Required - will raise error if not in .env
    KARLANCER_URL: str
    DATABASE_URL: str
    GOOGLE_API_KEY: str
    LLM_PROVIDER: str
    KARLANCER_TOKEN:str
    PONISHA_CATEGORIES_URL:str
    PONISHA_SEARCH_URL:str
    PONISHA_API_URL:str
    LLM_BASE_URL:str
    LLM_API_KEY:str
    LLM_API_KEY_2:str
    LLM_API_KEY_3:str
    LLM_API_KEY_4:str
    NAVAVSAAN_API_KEY:str
    
    # Optional with default
    DATABASE_URL: str = "sqlite:///./app.db"
    
    # Optional
    API_KEY: Optional[str] = None
    DEBUG: bool = False
    
    # Scraper-specific settings
    SCRAPER_HEADLESS: bool = True
    SCRAPER_TIMEOUT: int = 30000

    
    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'
        case_sensitive = True  # Match .env exactly

settings = Settings()

# Now you can use settings anywhere:
# print(settings.KARLANCER_URL)  # "https://karlancer.com"