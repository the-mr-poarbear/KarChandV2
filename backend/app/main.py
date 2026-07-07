from fastapi import FastAPI
from app.api.karlancer import scraper as karlancer_scraper
from app.api.ponisha import scraper as ponisha_scraper
from app.api import usd 

app = FastAPI(title="KarChand API")

app.include_router(karlancer_scraper.router)
app.include_router(ponisha_scraper.router)
app.include_router(usd.router)
# app.include_router(categories.router)
# app.include_router(projects.router)


@app.get("/")
def root():
    return {"status": "ok"}