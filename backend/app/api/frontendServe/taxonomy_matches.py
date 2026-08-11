from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.Frontend.extract_taxonomy import Extract_Taxonomy
from app.services.Frontend.project_matcher import find_similar_projects
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["API"])


class ExtractTaxonomyRequest(BaseModel):
    query: str


@router.post("/extract_taxonomy")
def Search(payload: ExtractTaxonomyRequest):
    return Extract_Taxonomy(payload.query)


class TaxonomySettings(BaseModel):
    features: list[str] = []
    boolean_modifiers: list[str] = []
    application_types: list[str] = []
    technology_modifiers: list[str] = []
    enum_modifiers: dict[str, str] = {}
    numeric_modifiers: dict[str, float] = {}


class MatchProjectsRequest(BaseModel):
    taxonomy: TaxonomySettings
    top_n: int = 10


@router.post("/match_projects")
def match_projects(payload: MatchProjectsRequest):
    return find_similar_projects(payload.taxonomy.dict(), top_n=payload.top_n)