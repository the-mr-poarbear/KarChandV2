from fastapi import FastAPI
from app.api.karlancer import scraper as karlancer_scraper
from app.api.ponisha import scraper as ponisha_scraper
from app.api import usd 
from app.api.frontendServe import taxonomy_matches
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="KarChand API")

app.include_router(karlancer_scraper.router)
app.include_router(ponisha_scraper.router)
app.include_router(usd.router)
app.include_router(taxonomy_matches.router)

# app.include_router(categories.router)
# app.include_router(projects.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"status": "ok"}