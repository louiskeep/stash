"""The stash web UI: a server-rendered Starlette app over the existing core.

The app is read-only against stored notes (the raw note is sacred); its only
write path is capturing a new note through `capture()`. Every route except
`/healthz`, `/login`, and `/static` requires a valid signed session. Routes are
thin: they call the synchronous core (`recall.search`, `storage.*`, `capture`)
and render Jinja templates. The one CPU-bound call on a request path, the query
embed, is offloaded with `asyncio.to_thread`, exactly as `commands.py` does, so
it never stalls the shared event loop (the Telegram poller and the reminder
scheduler run in the same process, spec 6.11).

`build_app(storage, embedder, config)` is the factory. It is driven in tests
through Starlette's in-process transport, so no socket is bound and no network
is touched.
"""

import asyncio
import os

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from stash import websession as ws
from stash.capture import capture
from stash.recall import search

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.join(_HERE, "templates")
_STATIC_DIR = os.path.join(_HERE, "static")

# Jinja2Templates enables autoescape for .html/.xml by default, so every value
# interpolated into a template (including a note's raw text, which may contain
# markup) is HTML-escaped unless explicitly marked safe. We never mark note
# content safe.
_templates = Jinja2Templates(directory=_TEMPLATES_DIR)


# --- small helpers ---------------------------------------------------------

def _session_token(request: Request) -> str | None:
    return request.cookies.get(ws.SESSION_COOKIE)


def _is_authed(request: Request) -> bool:
    secret = request.app.state.config.web_session_secret
    return ws.verify_session(_session_token(request), secret)


def _csrf_for(request: Request) -> str:
    """The CSRF token for the current (authenticated) session."""
    secret = request.app.state.config.web_session_secret
    return ws.issue_csrf(secret, _session_token(request))


def _render(request: Request, name: str, ctx: dict, status: int = 200) -> Response:
    base = {
        "request": request,
        "authed": _is_authed(request),
        "csrf_token": _csrf_for(request) if _is_authed(request) else None,
    }
    base.update(ctx)
    return _templates.TemplateResponse(request, name, base, status_code=status)


def _set_session_cookie(resp: Response, token: str, secure: bool) -> None:
    resp.set_cookie(
        ws.SESSION_COOKIE, token,
        max_age=ws.DEFAULT_MAX_AGE_SECONDS,
        httponly=True, samesite="lax", secure=secure, path="/",
    )


async def _form_field(request: Request, name: str) -> str:
    form = await request.form()
    value = form.get(name)
    return value if isinstance(value, str) else ""


def _csrf_header_or_form(request: Request, form_token: str) -> str:
    # htmx POSTs send the token in the X-CSRF-Token header (from the body's
    # hx-headers); plain form POSTs send it in a hidden field. Accept either.
    return request.headers.get("x-csrf-token") or form_token


# --- auth routes -----------------------------------------------------------

async def login_get(request: Request) -> Response:
    if _is_authed(request):
        return RedirectResponse("/", status_code=303)
    secret = request.app.state.config.web_session_secret
    return _render(request, "login.html",
                   {"authed": False, "csrf_token": None,
                    "login_csrf": ws.issue_csrf(secret, None)})


async def login_post(request: Request) -> Response:
    cfg = request.app.state.config
    secret = cfg.web_session_secret
    form = await request.form()
    token = request.headers.get("x-csrf-token") or (form.get("csrf_token") or "")
    if not ws.verify_login_csrf(token if isinstance(token, str) else "", secret):
        raise HTTPException(status_code=403, detail="bad csrf token")
    password = form.get("password")
    password = password if isinstance(password, str) else ""
    if not ws.check_password(password, cfg.web_password):
        # Generic error, 401, no logging of the attempt or the value.
        return _render(request, "login.html",
                       {"authed": False, "csrf_token": None,
                        "login_csrf": ws.issue_csrf(secret, None),
                        "error": "That password did not work."},
                       status=401)
    session = ws.issue_session(secret)
    resp = RedirectResponse("/", status_code=303)
    _set_session_cookie(resp, session, secure=(request.url.scheme == "https"))
    return resp


async def logout_post(request: Request) -> Response:
    # CSRF-checked like any state-changing POST.
    _require_csrf(request, await _form_field(request, "csrf_token"))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(ws.SESSION_COOKIE, path="/")
    return resp


def _require_csrf(request: Request, form_token: str) -> None:
    secret = request.app.state.config.web_session_secret
    token = _csrf_header_or_form(request, form_token)
    if not ws.verify_csrf(token, secret, _session_token(request)):
        raise HTTPException(status_code=403, detail="bad csrf token")


# --- health ----------------------------------------------------------------

async def healthz(request: Request) -> Response:
    return PlainTextResponse("ok")


# --- auth guard middleware -------------------------------------------------

# Exact-match open paths plus one open prefix (/static/...). Everything else
# requires a session. Listing them explicitly (rather than guarding per-route)
# means a newly added route is protected by default: forgetting to guard a
# handler cannot leak note content, which is the property the Task 6 sweep locks
# in.
_OPEN_EXACT = frozenset({"/healthz", "/login"})
_OPEN_PREFIX = ("/static/",)


def _is_open(path: str) -> bool:
    if path in _OPEN_EXACT:
        return True
    return any(path.startswith(p) for p in _OPEN_PREFIX)


class AuthGuard:
    """Redirect any non-open route to /login unless the request carries a valid
    session. Runs before routing, so protection is the default.
    """

    def __init__(self, app, config):
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not _is_open(path):
            request = Request(scope, receive)
            if not ws.verify_session(request.cookies.get(ws.SESSION_COOKIE),
                                     self.config.web_session_secret):
                resp = RedirectResponse("/login", status_code=303)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


# --- page routes -----------------------------------------------------------

_RESULTS_LIMIT = 20
_TIMELINE_PAGE = 50
_RELATED_LIMIT = 5
_REMINDERS_LIMIT = 200
_TOPIC_LIMIT = 200


async def _do_search(request: Request, q: str):
    if not q.strip():
        return []
    storage = request.app.state.storage
    embedder = request.app.state.embedder
    # Offload the CPU-bound embed off the event loop, like commands.py. The
    # SQLite work in recall.search stays on the loop thread.
    qvec = await asyncio.to_thread(embedder.embed, q)
    return search(storage, embedder, q, limit=_RESULTS_LIMIT, query_embedding=qvec)


async def search_page(request: Request) -> Response:
    q = request.query_params.get("q", "")
    results = await _do_search(request, q)
    return _render(request, "search.html",
                   {"active": "search", "q": q, "results": results})


async def search_fragment(request: Request) -> Response:
    q = request.query_params.get("q", "")
    results = await _do_search(request, q)
    # Just the results list, for htmx to swap into #results.
    return _render(request, "_tiles.html", {"q": q, "results": results})


async def add_note(request: Request) -> Response:
    _require_csrf(request, await _form_field(request, "csrf_token"))
    text = await _form_field(request, "text")
    storage = request.app.state.storage
    embedder = request.app.state.embedder
    receipt = None
    if text.strip():
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        # capture() runs the embed inline; offload it so the loop is not stalled.
        result = await asyncio.to_thread(
            capture, storage, embedder, text, "web", now)
        receipt = result.receipt
    return _render(request, "search.html",
                   {"active": "search", "q": "", "results": [], "receipt": receipt})


async def timeline_page(request: Request) -> Response:
    storage = request.app.state.storage
    before_id = request.query_params.get("before_id")
    before = int(before_id) if before_id and before_id.isdigit() else None
    # Fetch one extra to know whether an older page exists.
    rows = storage.recent_notes(_TIMELINE_PAGE + 1, before_id=before)
    next_before_id = None
    if len(rows) > _TIMELINE_PAGE:
        next_before_id = rows[_TIMELINE_PAGE - 1]["id"]
        rows = rows[:_TIMELINE_PAGE]
    return _render(request, "timeline.html",
                   {"active": "timeline", "notes": rows,
                    "next_before_id": next_before_id})


async def topics_page(request: Request) -> Response:
    storage = request.app.state.storage
    return _render(request, "topics.html",
                   {"active": "topics",
                    "tags": storage.tag_counts(),
                    "categories": storage.category_counts()})


async def topic_tag(request: Request) -> Response:
    storage = request.app.state.storage
    tag = request.path_params["tag"]
    notes = storage.notes_by_tag(tag, _TOPIC_LIMIT)
    return _render(request, "topic_list.html",
                   {"active": "topics", "label": "#" + tag, "notes": notes})


async def topic_category(request: Request) -> Response:
    storage = request.app.state.storage
    cat = request.path_params["cat"]
    notes = storage.notes_by_category(cat, _TOPIC_LIMIT)
    return _render(request, "topic_list.html",
                   {"active": "topics", "label": "@" + cat, "notes": notes})


async def note_detail(request: Request) -> Response:
    storage = request.app.state.storage
    note_id = request.path_params["note_id"]
    detail = storage.note_detail(note_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="no such note")
    related = storage.related_by_embedding(note_id, _RELATED_LIMIT)
    return _render(request, "note.html",
                   {"active": "", "note": detail, "related": related})


async def reminders_page(request: Request) -> Response:
    storage = request.app.state.storage
    return _render(request, "reminders.html",
                   {"active": "reminders",
                    "reminders": storage.list_reminders(_REMINDERS_LIMIT)})


# --- factory ---------------------------------------------------------------

def _validate_web_config(config) -> None:
    # Never run the web app open. Mirrors serve._validate_config: a missing
    # password or session secret is a hard refusal, not a silent allow.
    if not config.web_password:
        raise RuntimeError("web UI: STASH_WEB_PASSWORD is not set")
    if not config.web_session_secret:
        raise RuntimeError("web UI: STASH_WEB_SESSION_SECRET is not set")


def build_app(storage, embedder, config) -> Starlette:
    """Build the stash web application.

    `storage` and `embedder` are the already-opened core objects shared with
    the rest of the process; `config` carries the web bind/password/secret.
    """
    _validate_web_config(config)
    routes = [
        Route("/healthz", healthz, methods=["GET"]),
        Route("/login", login_get, methods=["GET"]),
        Route("/login", login_post, methods=["POST"]),
        Route("/logout", logout_post, methods=["POST"]),
        Route("/", search_page, methods=["GET"]),
        Route("/search", search_fragment, methods=["GET"]),
        Route("/add", add_note, methods=["POST"]),
        Route("/timeline", timeline_page, methods=["GET"]),
        Route("/topics", topics_page, methods=["GET"]),
        Route("/topics/tag/{tag}", topic_tag, methods=["GET"]),
        Route("/topics/category/{cat}", topic_category, methods=["GET"]),
        Route("/note/{note_id:int}", note_detail, methods=["GET"]),
        Route("/reminders", reminders_page, methods=["GET"]),
        Mount("/static", StaticFiles(directory=_STATIC_DIR), name="static"),
    ]
    app = Starlette(routes=routes)
    app.state.storage = storage
    app.state.embedder = embedder
    app.state.config = config
    app.add_middleware(AuthGuard, config=config)
    return app
