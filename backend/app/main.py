from fastapi import FastAPI
from app.api import scraper
from app.api import skill

app = FastAPI(title="KarChand API")

app.include_router(scraper.router)
app.include_router(skill.router)
# app.include_router(categories.router)
# app.include_router(projects.router)


@app.get("/")
def root():
    return {"status": "ok"}