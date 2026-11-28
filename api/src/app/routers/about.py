from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .. import auth
from ..config import (
    get_database_host,
    get_database_name,
    get_database_port,
    get_openai_base_url,
    get_openai_model,
    get_photos_uri,
    get_version,
    redact_uri,
)

router = APIRouter()


class AboutResponse(BaseModel):
    # The Jenkins build number baked into the image; None when running from
    # source, which the web UI reports as development mode.
    version: Optional[str]
    # Current year by the server's clock (not the browser's).
    year: int
    # Deployment details (where the photos and database are, which model):
    # null for a non-admin -- a guest only gets the version and year.
    photos_source: Optional[str] = None
    chat_model: Optional[str] = None
    chat_endpoint: Optional[str] = None
    database_name: Optional[str] = None
    database_host: Optional[str] = None


@router.get("/about", response_model=AboutResponse)
def about(request: Request) -> AboutResponse:
    if not auth.current_principal(request).is_admin:
        return AboutResponse(version=get_version(), year=datetime.now().year)
    # Never include credentials: URIs go through redact_uri, and the
    # database/API passwords are not part of this response at all.
    base_url = get_openai_base_url()
    return AboutResponse(
        version=get_version(),
        year=datetime.now().year,
        photos_source=redact_uri(get_photos_uri()),
        chat_model=get_openai_model(),
        chat_endpoint=redact_uri(base_url) if base_url else None,
        database_name=get_database_name(),
        database_host=f"{get_database_host()}:{get_database_port()}",
    )
