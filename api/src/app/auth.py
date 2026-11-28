"""Authentication and authorization. The design is in docs/security.md.

Summary:

- ``Principal`` is the only thing the rest of the app knows about the caller:
  who (``subject``), what they may do (``role``: ``guest`` or ``admin``) and how
  they proved it (``provider``).
- An ``AuthProvider`` turns credentials into a ``Principal``. v1 has one,
  ``PasswordProvider`` (a shared admin password and an optional shared guest
  password, both from the environment). OAuth/OIDC would be a second
  implementation that ends in the same signed session cookie.
- ``enforce_policy`` is an app-level FastAPI dependency. Every route must be
  listed in ``ROUTE_POLICY``; a route that isn't is refused (fail closed).
- With ``LUMINARY_AUTH_ENABLED`` off, every caller is an admin.
"""

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Protocol

from fastapi import HTTPException, Request

from .config import (
    get_admin_password,
    get_auth_enabled,
    get_guest_mode_enabled,
    get_guest_password,
    get_session_cookie_secure,
    get_session_secret,
    get_session_ttl_hours,
)

logger = logging.getLogger(__name__)

GUEST = "guest"
ADMIN = "admin"
_RANK = {GUEST: 1, ADMIN: 2}

SESSION_COOKIE = "luminary_session"
MIN_SESSION_SECRET_LENGTH = 16

# Policy levels for ROUTE_POLICY.
PUBLIC = "public"  # no session needed
USER = "user"  # any principal, guest included
ADMIN_ONLY = "admin"


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    provider: str
    display_name: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN


def role_allows(role: Optional[str], required: str) -> bool:
    """Whether `role` may do something that needs `required`. An unknown or
    missing role may do nothing."""
    return _RANK.get(role or "", 0) >= _RANK.get(required, 99)


ADMIN_WHEN_DISABLED = Principal(subject="local", role=ADMIN, provider="disabled", display_name="Local")
ANONYMOUS_GUEST = Principal(subject="anonymous", role=GUEST, provider="anonymous", display_name="Guest")


# --- configuration check ----------------------------------------------------


def validate_config() -> None:
    """Called at startup. With auth on, refuse to start rather than run with
    an empty password or an unusable session secret."""
    if not get_auth_enabled():
        logger.warning("Authentication is DISABLED (LUMINARY_AUTH_ENABLED): every request is an admin")
        return
    admin_password = get_admin_password()
    guest_password = get_guest_password()
    secret = get_session_secret()
    if not admin_password:
        raise RuntimeError("LUMINARY_AUTH_ENABLED is on but LUMINARY_ADMIN_PASSWORD is not set")
    if not secret or len(secret) < MIN_SESSION_SECRET_LENGTH:
        raise RuntimeError(
            f"LUMINARY_AUTH_ENABLED is on but LUMINARY_SESSION_SECRET is not set "
            f"(at least {MIN_SESSION_SECRET_LENGTH} characters required)"
        )
    if guest_password and hmac.compare_digest(_digest(admin_password), _digest(guest_password)):
        raise RuntimeError("LUMINARY_GUEST_PASSWORD must differ from LUMINARY_ADMIN_PASSWORD")
    logger.info(
        "Authentication enabled; guest mode %s; guest password %s",
        "on" if get_guest_mode_enabled() else "off",
        "set" if guest_password else "not set",
    )


# --- providers ---------------------------------------------------------------


class AuthProvider(Protocol):
    """Turns credentials into a Principal, or None if they aren't valid."""

    name: str

    def authenticate(self, credentials: dict) -> Optional[Principal]: ...


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


class PasswordProvider:
    """v1: one shared admin password and an optional shared guest password."""

    name = "password"

    def authenticate(self, credentials: dict) -> Optional[Principal]:
        supplied = credentials.get("password")
        if not isinstance(supplied, str) or not supplied:
            return None
        supplied_digest = _digest(supplied)
        # Both comparisons always run, so timing doesn't say which one was closer.
        admin_password = get_admin_password()
        guest_password = get_guest_password()
        is_admin = bool(admin_password) and hmac.compare_digest(supplied_digest, _digest(admin_password))
        is_guest = bool(guest_password) and hmac.compare_digest(supplied_digest, _digest(guest_password))
        if is_admin:
            return Principal(subject="admin", role=ADMIN, provider=self.name, display_name="Admin")
        if is_guest:
            return Principal(subject="guest", role=GUEST, provider=self.name, display_name="Guest")
        return None


PROVIDERS: list[AuthProvider] = [PasswordProvider()]


def authenticate(credentials: dict) -> Optional[Principal]:
    for provider in PROVIDERS:
        principal = provider.authenticate(credentials)
        if principal is not None:
            return principal
    return None


# --- login throttle ----------------------------------------------------------

_THROTTLE_WINDOW_SECONDS = 60.0
_THROTTLE_MAX_FAILURES = 10
_failures: deque = deque()
_failures_lock = threading.Lock()


def _prune(now: float) -> None:
    while _failures and now - _failures[0] > _THROTTLE_WINDOW_SECONDS:
        _failures.popleft()


def login_retry_after() -> int:
    """Seconds to wait before another login attempt, 0 if allowed."""
    now = time.monotonic()
    with _failures_lock:
        _prune(now)
        if len(_failures) < _THROTTLE_MAX_FAILURES:
            return 0
        return max(1, int(_THROTTLE_WINDOW_SECONDS - (now - _failures[0])) + 1)


def record_login_failure() -> None:
    with _failures_lock:
        _failures.append(time.monotonic())


def reset_throttle() -> None:
    with _failures_lock:
        _failures.clear()


# --- sessions ------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())


def session_max_age() -> int:
    return int(get_session_ttl_hours() * 3600)


def create_session(principal: Principal, now: Optional[float] = None) -> str:
    secret = get_session_secret()
    if not secret:
        raise RuntimeError("LUMINARY_SESSION_SECRET is not set")
    issued = int(now if now is not None else time.time())
    body = {
        "sub": principal.subject,
        "role": principal.role,
        "prv": principal.provider,
        "name": principal.display_name,
        "iat": issued,
        "exp": issued + session_max_age(),
    }
    payload = _b64(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_sign(payload, secret)}"


def read_session(token: Optional[str], now: Optional[float] = None) -> Optional[Principal]:
    """The Principal in a session cookie, or None if it is missing, forged,
    malformed or expired."""
    secret = get_session_secret()
    if not token or not secret or token.count(".") != 1:
        return None
    payload, signature = token.split(".")
    try:
        if not hmac.compare_digest(signature, _sign(payload, secret)):
            return None
        body = json.loads(_unb64(payload))
        if not isinstance(body, dict) or body.get("exp", 0) <= (now if now is not None else time.time()):
            return None
        role = body.get("role")
        if role not in _RANK:
            return None
        return Principal(
            subject=str(body.get("sub", "")),
            role=role,
            provider=str(body.get("prv", "")),
            display_name=str(body.get("name", "")),
        )
    except (ValueError, TypeError):
        return None


def cookie_secure(request: Request) -> bool:
    setting = get_session_cookie_secure()
    if setting != "auto":
        return setting == "true"
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return forwarded == "https" or request.url.scheme == "https"


# --- who is calling ----------------------------------------------------------------


def resolve_principal(request: Request) -> Optional[Principal]:
    """The caller, or None when they have no session and guests aren't
    allowed in without one."""
    if not get_auth_enabled():
        return ADMIN_WHEN_DISABLED
    principal = read_session(request.cookies.get(SESSION_COOKIE))
    if principal is not None:
        return principal
    if get_guest_mode_enabled():
        return ANONYMOUS_GUEST
    return None


def current_principal(request: Request) -> Principal:
    """The Principal enforce_policy resolved for this request (route handlers
    use this to narrow a route's policy further)."""
    principal = getattr(request.state, "principal", None)
    if principal is None:
        principal = resolve_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    return principal


def require_admin(request: Request) -> Principal:
    principal = current_principal(request)
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return principal


# --- route policy ------------------------------------------------------------------

# Every route, by (method, path as registered). Anything not here is refused.
# Admin-only includes everything the Manage page reads, not just what it
# changes. Keep tests/test_auth.py's coverage test passing when you add a route.
ROUTE_POLICY: dict[tuple[str, str], str] = {
    # public
    ("GET", "/api/health"): PUBLIC,
    ("GET", "/api/auth/session"): PUBLIC,
    ("POST", "/api/auth/login"): PUBLIC,
    ("POST", "/api/auth/logout"): PUBLIC,
    # guest-readable
    ("GET", "/api/about"): USER,
    ("GET", "/api/backend/status"): USER,
    ("GET", "/api/photos"): USER,
    ("GET", "/api/photos/search"): USER,
    ("GET", "/api/photos/{photo_id}"): USER,
    ("GET", "/api/photos/{photo_id}/original"): USER,
    ("GET", "/api/photos/{photo_id}/thumbnail"): USER,
    ("GET", "/api/photos/{photo_id}/faces"): USER,
    ("GET", "/api/photos/{photo_id}/tags"): USER,
    ("GET", "/api/tags"): USER,
    ("GET", "/api/tags/{tag_id}/photos"): USER,
    ("GET", "/api/individuals"): USER,
    ("GET", "/api/individuals/unmatched/photos"): USER,
    ("GET", "/api/individuals/{person_id}/photos"): USER,
    ("GET", "/api/individuals/{person_id}/thumbnail"): USER,
    ("GET", "/api/individual-groups"): USER,
    ("GET", "/api/trips"): USER,  # status=dismissed is narrowed to admin in the handler
    ("GET", "/api/trips/{trip_id}"): USER,
    ("GET", "/api/trips/{trip_id}/photos"): USER,
    ("GET", "/api/trips/{trip_id}/locations"): USER,
    # chat: a POST, but read-only; its tools are role-checked in chat.py
    ("POST", "/api/chat"): USER,
    ("POST", "/api/photos/{photo_id}/chat"): USER,
    # admin: changes
    ("POST", "/api/photos"): ADMIN_ONLY,
    ("DELETE", "/api/photos/{photo_id}"): ADMIN_ONLY,
    ("PUT", "/api/photos/{photo_id}/rotation"): ADMIN_ONLY,
    ("POST", "/api/photos/{photo_id}/tags/{tag_id}"): ADMIN_ONLY,
    ("DELETE", "/api/photos/{photo_id}/tags/{tag_id}"): ADMIN_ONLY,
    ("DELETE", "/api/purge"): ADMIN_ONLY,
    ("POST", "/api/tags"): ADMIN_ONLY,
    ("PATCH", "/api/tags/{tag_id}"): ADMIN_ONLY,
    ("DELETE", "/api/tags/{tag_id}"): ADMIN_ONLY,
    ("POST", "/api/individuals"): ADMIN_ONLY,
    ("PATCH", "/api/individuals/{person_id}"): ADMIN_ONLY,
    ("DELETE", "/api/individuals/{person_id}"): ADMIN_ONLY,
    ("POST", "/api/individual-groups"): ADMIN_ONLY,
    ("PATCH", "/api/individual-groups/{group_id}"): ADMIN_ONLY,
    ("DELETE", "/api/individual-groups/{group_id}"): ADMIN_ONLY,
    ("POST", "/api/faces/{face_id}/tag"): ADMIN_ONLY,
    ("DELETE", "/api/faces/{face_id}/tag"): ADMIN_ONLY,
    ("DELETE", "/api/rejections/{rejection_id}"): ADMIN_ONLY,
    ("POST", "/api/homes"): ADMIN_ONLY,
    ("PATCH", "/api/homes/{home_id}"): ADMIN_ONLY,
    ("POST", "/api/homes/{home_id}/confirm"): ADMIN_ONLY,
    ("DELETE", "/api/homes/{home_id}"): ADMIN_ONLY,
    ("POST", "/api/trips"): ADMIN_ONLY,
    ("POST", "/api/trips/merge"): ADMIN_ONLY,
    ("PATCH", "/api/trips/{trip_id}"): ADMIN_ONLY,
    ("POST", "/api/trips/{trip_id}/photos/add"): ADMIN_ONLY,
    ("POST", "/api/trips/{trip_id}/photos/remove"): ADMIN_ONLY,
    ("POST", "/api/trips/{trip_id}/split"): ADMIN_ONLY,
    ("POST", "/api/trips/{trip_id}/dismiss"): ADMIN_ONLY,
    ("POST", "/api/trips/{trip_id}/restore"): ADMIN_ONLY,
    # admin: background jobs (start, cancel, status)
    ("POST", "/api/sync"): ADMIN_ONLY,
    ("GET", "/api/sync/status"): ADMIN_ONLY,
    ("GET", "/api/jobs"): ADMIN_ONLY,
    ("POST", "/api/jobs/cancel"): ADMIN_ONLY,
    ("POST", "/api/faces/refresh"): ADMIN_ONLY,
    ("GET", "/api/faces/refresh/status"): ADMIN_ONLY,
    ("POST", "/api/descriptions/refresh"): ADMIN_ONLY,
    ("GET", "/api/descriptions/refresh/status"): ADMIN_ONLY,
    ("POST", "/api/locations/refresh"): ADMIN_ONLY,
    ("GET", "/api/locations/refresh/status"): ADMIN_ONLY,
    ("POST", "/api/pets/refresh"): ADMIN_ONLY,
    ("GET", "/api/pets/refresh/status"): ADMIN_ONLY,
    ("POST", "/api/trips/refresh"): ADMIN_ONLY,
    ("GET", "/api/trips/refresh/status"): ADMIN_ONLY,
    ("POST", "/api/duplicates/refresh"): ADMIN_ONLY,
    ("GET", "/api/duplicates/refresh/status"): ADMIN_ONLY,
    # admin: data only the Manage page reads
    ("GET", "/api/individuals/{person_id}/faces"): ADMIN_ONLY,
    ("GET", "/api/individuals/{person_id}/rejections"): ADMIN_ONLY,
    ("GET", "/api/faces/{face_id}/thumbnail"): ADMIN_ONLY,
    ("GET", "/api/rejections/{rejection_id}/thumbnail"): ADMIN_ONLY,
    ("GET", "/api/homes"): ADMIN_ONLY,
    ("GET", "/api/duplicates/csv"): ADMIN_ONLY,
    ("GET", "/api/photos/duplicates/csv"): ADMIN_ONLY,
    ("GET", "/api/photos/{photo_id}/similar"): ADMIN_ONLY,
    ("GET", "/api/tags/{tag_id}/photos/csv"): ADMIN_ONLY,
}

# Non-GET routes that are not admin-only, and why (checked by the tests).
NON_ADMIN_MUTATIONS = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/chat"),
    ("POST", "/api/photos/{photo_id}/chat"),
}


def enforce_policy(request: Request) -> None:
    """App-level dependency: look the matched route up in ROUTE_POLICY and
    check the caller against it, before the handler runs."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    policy = ROUTE_POLICY.get((request.method, path))
    if policy is None:
        logger.error("No auth policy for %s %s; refusing", request.method, path)
        raise HTTPException(status_code=403, detail="Forbidden")
    if policy == PUBLIC:
        request.state.principal = resolve_principal(request)
        return
    principal = resolve_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    request.state.principal = principal
    if policy == ADMIN_ONLY and not principal.is_admin:
        logger.warning("Refused %s %s for %s (%s)", request.method, path, principal.subject, principal.role)
        raise HTTPException(status_code=403, detail="Administrator access required")
