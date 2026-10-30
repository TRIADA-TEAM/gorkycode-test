import hashlib
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings


STATIC_DIR = Path(__file__).parent / "static"
static_hasher = hashlib.sha256()
for static_path in sorted((*STATIC_DIR.glob("*.css"), *STATIC_DIR.glob("*.js"))):
    static_hasher.update(static_path.name.encode())
    static_hasher.update(static_path.read_bytes())
STATIC_VERSION = static_hasher.hexdigest()[:12]
INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace(
    "__STATIC_VERSION__", STATIC_VERSION
)
logging.basicConfig(
    level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


app = FastAPI(
    title="Голосовой помощник для задач, встреч и напоминаний",
    version="0.1.0",
)
app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def disable_prototype_asset_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/", include_in_schema=False)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)
