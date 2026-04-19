import asyncio
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import db, scraper

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Binny's Deals Dashboard")
db.init()

_scrape_lock = asyncio.Lock()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "categories": db.list_categories(),
            "last_scraped_at": db.last_scraped_at(),
        },
    )


@app.get("/api/deals")
async def api_deals(
    category: str | None = None,
    min_percent_off: float | None = None,
    max_price: float | None = None,
    sort: str = "percent_off",
):
    return JSONResponse(
        {
            "last_scraped_at": db.last_scraped_at(),
            "deals": db.list_deals(
                category=category,
                min_percent_off=min_percent_off,
                max_price=max_price,
                sort=sort,
            ),
        }
    )


@app.post("/api/refresh")
async def api_refresh(max_pages: int = 5):
    if _scrape_lock.locked():
        return JSONResponse({"status": "already_running"}, status_code=409)
    async with _scrape_lock:
        count = await scraper.scrape_all(max_pages=max_pages)
    return {"status": "ok", "count": count, "last_scraped_at": db.last_scraped_at()}
