# Security design: authentication and authorization

This describes how Luminary decides who may do what. It covers v1 (shared
passwords) and the seam where OAuth/OIDC plugs in later. Code: `api/src/app/auth.py`,
`api/src/app/routers/auth.py`, `web/src/auth.jsx`.

## Goals and non-goals

- Anyone who can reach the app may **browse** (gallery, viewer, search chat) as a
  *guest*, or must sign in first, depending on deployment.
- Only an **admin** can change anything (tags, individuals, groups, trips, homes,
  rotation, delete/purge) or reach the Manage page, including its read-only data and
  every background job (sync, refreshes, cancel).
- Enforced **twice**: the GUI hides what a role may not use, and the API refuses it.
  The API is the authority; the GUI is convenience only.
- Not in v1: per-user accounts, per-photo permissions, audit logging of who did what,
  OAuth. The design leaves room for them (below).
- Unchanged and independent: the photos backend is never written to
  (see CLAUDE.md "Hard invariant"). Authorization does not relax that for anyone.

## Roles

| Role | Can |
|---|---|
| `guest` | Read photos, thumbnails, originals, sidebar trees (individuals, tags, trips, dates, locations), photo details, **chat/search** |
| `admin` | Everything a guest can, plus every mutation and the Manage page |

## Deployment configuration

All of it is environment, read by the API at call time. The browser cannot set or
override any of it.

| Variable | Default | Helm default | Meaning |
|---|---|---|---|
| `LUMINARY_AUTH_ENABLED` | `false` | `true` | Off: no checks, every request is admin (local development). |
| `LUMINARY_GUEST_MODE_ENABLED` | `false` | `true` | On: a visitor with no session is an anonymous guest, no login needed. Off: a session is required for anything. |
| `LUMINARY_ADMIN_PASSWORD` | none | Secret | The v1 admin credential. Required when auth is on. |
| `LUMINARY_GUEST_PASSWORD` | none | Secret (optional key) | A password that signs in as guest. Optional. |
| `LUMINARY_SESSION_SECRET` | none | Secret | HMAC key for session cookies. Required when auth is on. |
| `LUMINARY_SESSION_TTL_HOURS` | `12` | | Session lifetime. |
| `LUMINARY_SESSION_COOKIE_SECURE` | `auto` | | `true`/`false`/`auto` (Secure when the request arrived over HTTPS, per `X-Forwarded-Proto`). |

Startup **fails** (the pod crash-loops visibly rather than running open) when auth is
on and the admin password or session secret is empty, the secret is shorter than 16
characters, or the guest and admin passwords are equal.

### Who gets in

| auth | guest mode | Result |
|---|---|---|
| off | n/a | Everyone is admin. |
| on | on | No session: anonymous guest. Signing in with the admin password: admin. The guest password also works (stays guest). |
| on | off | No session: `401` on everything but the session/login endpoints, and the GUI shows only a login screen. Admin password: admin. Guest password: guest. Anything else: `401`, no session issued. |

## Sessions

- `POST /api/auth/login {password}` checks the admin password, then the guest password,
  and issues a cookie. `POST /api/auth/logout` clears it. `GET /api/auth/session`
  reports `{auth_enabled, guest_mode, authenticated, role}` and is always public.
- The cookie `luminary_session` is `base64url(json).hmac-sha256` over
  `{sub, role, provider, iat, exp}`, signed with `LUMINARY_SESSION_SECRET`. The role
  is inside the signed payload, so it cannot be changed without the secret. It is
  stateless (no server store), which fits the single-replica deployment.
- Flags: `HttpOnly` (script can't read it), `SameSite=Strict`, `Secure` over HTTPS,
  `Path=/`, `Max-Age` = TTL.
- A cookie is used rather than an `Authorization` header because photos, thumbnails
  and video are loaded by `<img>`/`<video>` elements, which can't add headers.
- Revocation: rotate `LUMINARY_SESSION_SECRET` (signs everyone out). Changing a
  password alone does not end existing sessions until they expire.
- Password checks use `hmac.compare_digest` on SHA-256 digests (constant time, no
  length leak) and always evaluate both passwords. Failed logins are throttled
  (10 failures per rolling minute, then `429`), process-wide. This also slows guessing
  but means an attacker can briefly block logins, which is accepted; guests browsing
  anonymously are unaffected.
- Passwords are never logged, never returned, and live only in Secrets/env.

## Enforcement (API)

One table in `auth.py`, `ROUTE_POLICY`, maps every `(method, path)` to a level:

- `public`: `/health`, `/auth/*`
- `user`: any principal, guest included
- `admin`: admin only

A single app-level dependency (`enforce_policy`) looks the matched route up in the
table before the handler runs:

- It **fails closed**: a route absent from the table is refused (`403`) even for an
  admin. Adding an endpoint without deciding its policy cannot silently expose it.
- A test enumerates `app.routes` and asserts the table and the app agree exactly, and
  that every non-GET route is `admin` except an explicit allowlist (login, logout,
  chat).
- `401` means "no valid session"; `403` means "signed in, not allowed".
- A few handlers narrow further inside the route (e.g. `GET /trips?status=dismissed` is
  Manage data, so it needs admin even though the active list is guest-readable).

Read data that only Manage uses (per-individual faces and rejections, face/rejection
thumbnails, homes, dismissed trips, jobs and every job's status, duplicate/tag CSV
exports) is `admin`. The sidebar needs a few reads that used to share endpoints with
Manage; those got guest-safe equivalents (e.g. `GET /individuals/{id}/photos` returns
photo ids only).

### Chat and the LLM

Chat is the one POST guests may use, and it runs tools on the server, so the LLM is
**not** trusted to decide who may do what:

- The principal comes from the session cookie, resolved by the route, and is carried
  on the chat turn. It never comes from the message, history, `search_list` or tool
  arguments, so a prompt cannot raise it.
- Every tool declares a required role in `chat._TOOL_ROLES`. A tool missing from that
  table is refused. The tools offered to the model are filtered to the role, and the
  role is checked again when a call is executed, because a model can name a tool that
  was never offered. A refused call returns "not permitted" text to the model, runs
  nothing, and is logged.
- Today every tool is read-only (searches, list names, list stats, truncate the
  in-memory list, locate an object in the open photo) so all are `guest`. Tools call
  `storage` directly, not the HTTP routes, which is why the check lives in the chat
  layer. A future tool that mutates must be declared `admin`; a test asserts the two
  tables cover the same names.
- With the chat model unconfigured or tools off nothing changes.

## Enforcement (GUI)

`web/src/auth.jsx` loads `GET /api/auth/session` on startup and exposes
`{role, isAdmin, canSignIn, login, logout}`.

- Auth off: behaves as admin, no sign-in control shown.
- Guest (and auth on): no Manage link, no sync/refresh control, no tag/face/trip/
  rotate/delete controls, no jobs polling. The view cannot be set to "manage" for a
  non-admin. A header control offers "Sign in" (admins see "Sign out").
- Auth on, guest mode off, not signed in: only the login screen renders.
- A `401` from any call returns the app to the session check/login; a `403` shows
  "admin only".
- These are usability guards only. Nothing relies on them for safety.

## Transport and browser considerations

- `SameSite=Strict` plus same-origin access (nginx proxies `/api/` on the one origin)
  means a cross-site page can neither send the cookie nor read responses. CSRF tokens
  are therefore not used. CORS credentials are allowed only when `LUMINARY_CORS_ORIGINS`
  names explicit origins, never with `*`.
- TLS terminates at the OpenShift route. Over HTTP (local dev) the cookie is not
  `Secure`.
- Responses to authentication failures are uniform (`Invalid password`), without
  saying which password was close.

## Threats considered

| Threat | Mitigation |
|---|---|
| Guest calls a mutating endpoint directly | Route policy, fail closed |
| New endpoint shipped unprotected | Fail-closed table plus coverage test |
| Cookie forged or role edited | HMAC with a secret the client never sees |
| Prompt injection makes chat "edit" something | Tools are read-only and role-checked server-side; role not model-controlled |
| Password guessing | Throttle, constant-time compare, long deployer-chosen secret |
| Credentials leak via logs/images | Only in Secrets/env, never logged; `HttpOnly` cookie |
| CSRF | `SameSite=Strict`, no wildcard credentialed CORS |
| Misconfigured open deployment | Startup refuses empty password or secret when auth is on |

Known v1 limits: shared passwords (no individual identity), stateless sessions can't
be revoked individually, and the login throttle is per process (the app is single
replica anyway).

## Moving to OAuth/OIDC

The rest of the system only knows `Principal(subject, display_name, role, provider)`.

- `auth.AuthProvider` is the interface that turns credentials into a `Principal`.
  v1's `PasswordProvider` is one implementation; an `OidcProvider` would add
  `GET /api/auth/oidc/login` (redirect to the IdP) and `/callback` (code exchange,
  ID-token validation, claim/group to role mapping, e.g. an admin group), then issue the
  **same** signed session cookie with `provider: "oidc"` and the user's `sub`.
- Route policy, chat tool roles, the session endpoint and the GUI don't change. The
  GUI's login screen can show a provider button next to the password box
  (`GET /api/auth/session` will list the available methods).
- Guest mode and the roles keep their meaning. Per-user identity then allows audit
  logging and finer roles without reworking enforcement.
