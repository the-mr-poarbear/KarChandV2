from app.services.Frontend.extract_taxonomy import Extract_Taxonomy
from sqlalchemy.orm import Session
from app.services.Frontend.project_matcher import find_similar_projects
from app.services.rag_system.retrieve_and_estimate import estimate

def mixed_estimator(query: str , db:Session)->dict:
    res = Extract_Taxonomy(query , db)
    print(res)
    taxonomy = res["suggested_settings"]
    rows = find_similar_projects(taxonomy=taxonomy , db=db , sendRaw=True)
    resFinal = estimate(query=query , db=db , rows=rows)
    resFinal["suggested_settings"]=taxonomy
    return resFinal