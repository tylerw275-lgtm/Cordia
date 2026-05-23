from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter()

_BASE = "https://sg-11285c6e-7e46-4f9c-83d8-9fcb336d.vercel.app"

# Redirect app routes to the canonical Softgen-hosted compliance pages
@router.get("/opt-in", include_in_schema=False)
async def opt_in_page(request: Request):
    return RedirectResponse(f"{_BASE}/opt-in", status_code=301)

@router.get("/privacy", include_in_schema=False)
async def privacy_page(request: Request):
    return RedirectResponse(f"{_BASE}/privacy", status_code=301)

@router.get("/terms", include_in_schema=False)
async def terms_page(request: Request):
    return RedirectResponse(f"{_BASE}/terms", status_code=301)
