from datetime import date, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings

router = APIRouter()

_jinja = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html"]),
)

_CTX = {
    "twilio_number": settings.twilio_phone_number or "+1 (888) 995-8208",
    "effective_date": "May 23, 2026",
    "contact_email": "info@crownbakeries.com",
}


@router.get("/opt-in", response_class=HTMLResponse, include_in_schema=False)
async def opt_in_page(request: Request):
    html = _jinja.get_template("opt_in.html").render(**_CTX)
    return HTMLResponse(html)


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_page(request: Request):
    html = _jinja.get_template("privacy.html").render(**_CTX)
    return HTMLResponse(html)


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
async def terms_page(request: Request):
    html = _jinja.get_template("terms.html").render(**_CTX)
    return HTMLResponse(html)
