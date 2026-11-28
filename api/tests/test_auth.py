"""Authentication and authorization (docs/security.md)."""

import pytest

from app import auth, chat

ADMIN_PW = "correct-horse-admin"
GUEST_PW = "plain-guest-pass"
SECRET = "s" * 32


def _auth_env(monkeypatch, *, guest_mode, guest_password=GUEST_PW):
    monkeypatch.setenv("LUMINARY_AUTH_ENABLED", "true")
    monkeypatch.setenv("LUMINARY_GUEST_MODE_ENABLED", "true" if guest_mode else "false")
    monkeypatch.setenv("LUMINARY_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("LUMINARY_SESSION_SECRET", SECRET)
    if guest_password:
        monkeypatch.setenv("LUMINARY_GUEST_PASSWORD", guest_password)
    else:
        monkeypatch.delenv("LUMINARY_GUEST_PASSWORD", raising=False)
    auth.reset_throttle()


@pytest.fixture
def guest_mode_client(client, monkeypatch):
    _auth_env(monkeypatch, guest_mode=True)
    return client


@pytest.fixture
def login_required_client(client, monkeypatch):
    _auth_env(monkeypatch, guest_mode=False)
    return client


def _login(client, password):
    return client.post("/api/auth/login", json={"password": password})


# --- the policy table covers the app exactly ---------------------------------


def _app_routes():
    from app.main import app

    return {
        (method, route.path)
        for route in app.routes
        if getattr(route, "methods", None) and route.path.startswith("/api")
        for method in route.methods - {"HEAD", "OPTIONS"}
    }


def test_every_route_has_a_policy_and_no_policy_is_stale():
    routes = _app_routes()
    assert routes - set(auth.ROUTE_POLICY) == set(), "route(s) with no auth policy"
    assert set(auth.ROUTE_POLICY) - routes == set(), "policy for a route that doesn't exist"


def test_every_mutation_is_admin_only_except_the_allowlist():
    for (method, path), level in auth.ROUTE_POLICY.items():
        if method != "GET" and (method, path) not in auth.NON_ADMIN_MUTATIONS:
            assert level == auth.ADMIN_ONLY, f"{method} {path} must be admin-only"


def test_a_route_without_a_policy_is_refused_even_for_an_admin(client, monkeypatch):
    from app.main import app

    @app.get("/api/_unlisted_test_route")
    def unlisted():
        return {"ok": True}

    try:
        assert client.get("/api/_unlisted_test_route").status_code == 403
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") != "/api/_unlisted_test_route"]


# --- auth disabled -----------------------------------------------------------------


def test_auth_disabled_everyone_is_admin(client):
    session = client.get("/api/auth/session").json()
    assert session["auth_enabled"] is False
    assert session["role"] == "admin"
    assert client.post("/api/tags", json={"name": "beach"}).status_code == 201
    assert client.get("/api/jobs").status_code == 200


# --- guest mode on ---------------------------------------------------------------


def test_anonymous_visitor_is_a_guest_who_can_read(guest_mode_client):
    session = guest_mode_client.get("/api/auth/session").json()
    assert session == {
        "auth_enabled": True,
        "guest_mode": True,
        "authenticated": True,
        "role": "guest",
        "display_name": "Guest",
    }
    assert guest_mode_client.get("/api/photos").status_code == 200
    assert guest_mode_client.get("/api/tags").status_code == 200
    assert guest_mode_client.get("/api/individuals").status_code == 200
    assert guest_mode_client.get("/api/trips").status_code == 200


def test_guest_cannot_change_anything(guest_mode_client):
    c = guest_mode_client
    for method, path in [
        ("post", "/api/tags"),
        ("post", "/api/individuals"),
        ("post", "/api/individual-groups"),
        ("post", "/api/sync"),
        ("post", "/api/jobs/cancel"),
        ("post", "/api/faces/refresh"),
        ("post", "/api/trips"),
        ("post", "/api/homes"),
        ("post", "/api/trips/merge"),
        ("delete", "/api/purge"),
        ("delete", "/api/photos/x"),
        ("put", "/api/photos/x/rotation"),
        ("post", "/api/photos/x/tags/y"),
        ("delete", "/api/faces/x/tag"),
        ("patch", "/api/tags/x"),
        ("post", "/api/photos"),
    ]:
        response = getattr(c, method)(path, **({"json": {}} if method in ("post", "put", "patch") else {}))
        assert response.status_code == 403, f"{method} {path} -> {response.status_code}"


def test_guest_cannot_read_manage_page_data(guest_mode_client):
    c = guest_mode_client
    for path in [
        "/api/jobs",
        "/api/sync/status",
        "/api/homes",
        "/api/trips?status=dismissed",
        "/api/individuals/x/faces",
        "/api/individuals/x/rejections",
        "/api/faces/x/thumbnail",
        "/api/rejections/x/thumbnail",
        "/api/duplicates/csv",
        "/api/photos/duplicates/csv",
        "/api/tags/x/photos/csv",
        "/api/faces/refresh/status",
        "/api/trips/refresh/status",
    ]:
        assert c.get(path).status_code == 403, path


def test_login_as_admin_unlocks_everything(guest_mode_client):
    response = _login(guest_mode_client, ADMIN_PW)
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert guest_mode_client.post("/api/tags", json={"name": "beach"}).status_code == 201
    assert guest_mode_client.get("/api/jobs").status_code == 200
    assert guest_mode_client.get("/api/trips?status=dismissed").status_code == 200


def test_logout_returns_to_guest(guest_mode_client):
    _login(guest_mode_client, ADMIN_PW)
    result = guest_mode_client.post("/api/auth/logout").json()
    assert result["role"] == "guest"
    assert guest_mode_client.post("/api/tags", json={"name": "x"}).status_code == 403


def test_guest_password_signs_in_as_guest_not_admin(guest_mode_client):
    assert _login(guest_mode_client, GUEST_PW).json()["role"] == "guest"
    assert guest_mode_client.post("/api/tags", json={"name": "x"}).status_code == 403


# --- guest mode off ------------------------------------------------------------------


def test_login_required_blocks_everything_but_the_public_routes(login_required_client):
    c = login_required_client
    assert c.get("/api/health").status_code == 200
    session = c.get("/api/auth/session").json()
    assert session["authenticated"] is False and session["role"] is None
    assert c.get("/api/photos").status_code == 401
    assert c.get("/api/photos/x/thumbnail").status_code == 401
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 401
    assert c.post("/api/tags", json={"name": "x"}).status_code == 401


def test_unknown_password_is_refused_without_a_session(login_required_client):
    response = _login(login_required_client, "nope")
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert login_required_client.get("/api/photos").status_code == 401


def test_admin_password_makes_an_admin(login_required_client):
    assert _login(login_required_client, ADMIN_PW).json()["role"] == "admin"
    assert login_required_client.get("/api/photos").status_code == 200
    assert login_required_client.post("/api/tags", json={"name": "x"}).status_code == 201


def test_guest_password_makes_a_guest(login_required_client):
    assert _login(login_required_client, GUEST_PW).json()["role"] == "guest"
    assert login_required_client.get("/api/photos").status_code == 200
    assert login_required_client.post("/api/tags", json={"name": "x"}).status_code == 403


def test_without_a_guest_password_only_the_admin_gets_in(client, monkeypatch):
    _auth_env(monkeypatch, guest_mode=False, guest_password=None)
    assert _login(client, GUEST_PW).status_code == 401
    assert _login(client, "").status_code == 401
    assert _login(client, ADMIN_PW).status_code == 200


# --- sessions ------------------------------------------------------------------------


def test_forged_and_tampered_cookies_are_ignored(login_required_client, monkeypatch):
    c = login_required_client
    guest_token = auth.create_session(auth.Principal("guest", auth.GUEST, "password"))
    payload, signature = guest_token.split(".")
    admin_payload = auth.create_session(auth.Principal("admin", auth.ADMIN, "password")).split(".")[0]
    for token in [
        f"{admin_payload}.{signature}",  # admin payload, guest signature
        guest_token + "x",
        "garbage",
        "a.b.c",
        "",
    ]:
        c.cookies.set(auth.SESSION_COOKIE, token)
        assert c.get("/api/photos").status_code == 401, token
    # A token signed with a different secret is no good either.
    other = auth.create_session(auth.Principal("admin", auth.ADMIN, "password"))
    monkeypatch.setenv("LUMINARY_SESSION_SECRET", "z" * 32)
    c.cookies.set(auth.SESSION_COOKIE, other)
    assert c.get("/api/photos").status_code == 401


def test_session_expires(monkeypatch):
    monkeypatch.setenv("LUMINARY_SESSION_SECRET", SECRET)
    token = auth.create_session(auth.Principal("admin", auth.ADMIN, "password"), now=1_000)
    assert auth.read_session(token, now=1_000 + 60).role == auth.ADMIN
    assert auth.read_session(token, now=1_000 + 13 * 3600) is None


def test_failed_logins_are_throttled(login_required_client):
    for _ in range(10):
        assert _login(login_required_client, "wrong").status_code == 401
    blocked = _login(login_required_client, ADMIN_PW)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    auth.reset_throttle()
    assert _login(login_required_client, ADMIN_PW).status_code == 200


def test_cookie_is_secure_behind_https(login_required_client):
    response = login_required_client.post(
        "/api/auth/login", json={"password": ADMIN_PW}, headers={"X-Forwarded-Proto": "https"}
    )
    assert "secure" in response.headers["set-cookie"].lower()


# --- startup validation -----------------------------------------------------------------


def test_startup_refuses_a_bad_configuration(monkeypatch):
    monkeypatch.setenv("LUMINARY_AUTH_ENABLED", "true")
    monkeypatch.delenv("LUMINARY_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("LUMINARY_GUEST_PASSWORD", raising=False)
    monkeypatch.setenv("LUMINARY_SESSION_SECRET", SECRET)
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        auth.validate_config()
    monkeypatch.setenv("LUMINARY_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("LUMINARY_SESSION_SECRET", "short")
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        auth.validate_config()
    monkeypatch.setenv("LUMINARY_SESSION_SECRET", SECRET)
    monkeypatch.setenv("LUMINARY_GUEST_PASSWORD", ADMIN_PW)
    with pytest.raises(RuntimeError, match="differ"):
        auth.validate_config()
    monkeypatch.setenv("LUMINARY_GUEST_PASSWORD", GUEST_PW)
    auth.validate_config()


def test_disabled_auth_needs_no_secrets(monkeypatch):
    monkeypatch.delenv("LUMINARY_AUTH_ENABLED", raising=False)
    auth.validate_config()


# --- sidebar data guests need ----------------------------------------------------------------


def test_guest_can_list_an_individuals_photo_ids(guest_mode_client):
    created = None
    _login(guest_mode_client, ADMIN_PW)
    created = guest_mode_client.post("/api/individuals", json={"name": "Ann"}).json()
    guest_mode_client.post("/api/auth/logout")
    response = guest_mode_client.get(f"/api/individuals/{created['id']}/photos")
    assert response.status_code == 200
    assert response.json() == {"photo_ids": []}
    assert guest_mode_client.get("/api/individuals/nope/photos").status_code == 404


# --- chat: the LLM can't be used to get around roles --------------------------------------------


def test_every_chat_tool_declares_a_role():
    assert set(chat._TOOL_ROLES) == set(chat._TOOL_HANDLERS)
    assert all(role in (auth.GUEST, auth.ADMIN) for role in chat._TOOL_ROLES.values())
    offered = {tool["function"]["name"] for tool in chat._SEARCH_TOOLS + [chat._LOCATE_OBJECT_TOOL]}
    assert offered <= set(chat._TOOL_ROLES)


def test_a_chat_tool_needing_admin_is_refused_for_a_guest(monkeypatch):
    calls = []
    monkeypatch.setitem(chat._TOOL_HANDLERS, "change_something", lambda arguments, turn: calls.append(arguments) or "changed")
    monkeypatch.setitem(chat._TOOL_ROLES, "change_something", auth.ADMIN)
    call = {"id": "1", "name": "change_something", "arguments": "{}"}

    guest_turn = chat._Turn(None, role=auth.GUEST)
    assert "Not permitted" in chat._run_tool_call(call, guest_turn)
    assert calls == []
    # No role given is the least privilege, not the most.
    assert "Not permitted" in chat._run_tool_call(call, chat._Turn(None))
    assert chat._run_tool_call(call, chat._Turn(None, role=auth.ADMIN)) == "changed"
    assert calls == [{}]


def test_a_tool_with_no_declared_role_is_refused(monkeypatch):
    monkeypatch.setitem(chat._TOOL_HANDLERS, "undeclared", lambda arguments, turn: "ran")
    call = {"id": "1", "name": "undeclared", "arguments": "{}"}
    assert "Not permitted" in chat._run_tool_call(call, chat._Turn(None, role=auth.ADMIN))


def test_tools_offered_to_the_model_are_filtered_by_role(monkeypatch):
    monkeypatch.setitem(chat._TOOL_ROLES, "search_photos_by_tag", auth.ADMIN)
    guest_names = {t["function"]["name"] for t in chat._chat_tools(0, chat._Turn(None, role=auth.GUEST))["tools"]}
    admin_names = {t["function"]["name"] for t in chat._chat_tools(0, chat._Turn(None, role=auth.ADMIN))["tools"]}
    assert "search_photos_by_tag" not in guest_names
    assert "search_photos_by_tag" in admin_names


def test_chat_route_passes_the_callers_role_not_the_clients(guest_mode_client, monkeypatch):
    seen = []

    def fake_ask(photo_bytes, content_type, message, history, search_list=None, role=None):
        seen.append(role)
        return iter([{"text": "ok"}])

    monkeypatch.setattr(chat, "ask_events", fake_ask)
    body = {"message": "hi", "history": [], "role": "admin"}
    assert guest_mode_client.post("/api/chat", json=body).status_code == 200
    _login(guest_mode_client, ADMIN_PW)
    assert guest_mode_client.post("/api/chat", json=body).status_code == 200
    assert seen == ["guest", "admin"]


def test_about_deployment_details_are_admin_only(guest_mode_client):
    guest = guest_mode_client.get("/api/about").json()
    assert guest["photos_source"] is None and guest["database_host"] is None
    assert guest["year"]
    _login(guest_mode_client, ADMIN_PW)
    admin = guest_mode_client.get("/api/about").json()
    assert admin["photos_source"] and admin["database_host"]
