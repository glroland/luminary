import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth
from ..config import get_auth_enabled, get_guest_mode_enabled

logger = logging.getLogger(__name__)

router = APIRouter()


class SessionInfo(BaseModel):
    auth_enabled: bool
    guest_mode: bool
    # Whether the caller may use the app at all right now (false: show the
    # login screen), and as what. `role` is null when not authenticated.
    authenticated: bool
    role: Optional[str] = None
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    password: str


def _info(principal: Optional[auth.Principal]) -> SessionInfo:
    return SessionInfo(
        auth_enabled=get_auth_enabled(),
        guest_mode=get_guest_mode_enabled(),
        authenticated=principal is not None,
        role=principal.role if principal else None,
        display_name=(principal.display_name or None) if principal else None,
    )


@router.get("/auth/session", response_model=SessionInfo)
def get_session(request: Request) -> SessionInfo:
    return _info(auth.resolve_principal(request))


@router.post("/auth/login", response_model=SessionInfo)
def login(body: LoginRequest, request: Request, response: Response) -> SessionInfo:
    if not get_auth_enabled():
        return _info(auth.ADMIN_WHEN_DISABLED)
    retry_after = auth.login_retry_after()
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Too many failed sign-in attempts. Try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )
    principal = auth.authenticate({"password": body.password})
    if principal is None:
        auth.record_login_failure()
        logger.warning("Failed sign-in attempt")
        raise HTTPException(status_code=401, detail="Invalid password")
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.create_session(principal),
        max_age=auth.session_max_age(),
        httponly=True,
        samesite="strict",
        secure=auth.cookie_secure(request),
        path="/",
    )
    logger.info("Signed in as %s", principal.role)
    return _info(principal)


@router.post("/auth/logout", response_model=SessionInfo)
def logout(request: Request, response: Response) -> SessionInfo:
    response.delete_cookie(auth.SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    # What the caller is now: nobody, or an anonymous guest if those are allowed.
    if not get_auth_enabled():
        return _info(auth.ADMIN_WHEN_DISABLED)
    return _info(auth.ANONYMOUS_GUEST if get_guest_mode_enabled() else None)
