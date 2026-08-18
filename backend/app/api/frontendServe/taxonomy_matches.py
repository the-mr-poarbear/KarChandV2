from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.Frontend.extract_taxonomy import Extract_Taxonomy
from app.services.Frontend.project_matcher import find_similar_projects
from app.services.rag_system.retrieve_and_estimate import estimate
from app.services.Frontend.mixed_estimator import mixed_estimator
from pydantic import BaseModel
from typing import Literal

router = APIRouter(prefix="/api", tags=["API"])


class ExtractTaxonomyRequest(BaseModel):
    query: str


@router.post("/extract_taxonomy_ml")
def Search(payload: ExtractTaxonomyRequest , db: Session = Depends(get_db)):
    return Extract_Taxonomy(payload.query , db)

@router.post("/extract_taxonomy_rag")
def Search(payload: ExtractTaxonomyRequest , db: Session = Depends(get_db)):
    return estimate(payload.query , db)

@router.post("/mixed")
def Search(payload: ExtractTaxonomyRequest , db: Session = Depends(get_db)):
    return mixed_estimator(payload.query , db)


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
def match_projects(payload: MatchProjectsRequest , db: Session = Depends(get_db)):
    return find_similar_projects(payload.taxonomy.model_dump(), top_n=payload.top_n ,db=db )