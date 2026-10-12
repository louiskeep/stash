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
from starlette.responses import (
    JSONResponse, PlainTextResponse, RedirectResponse, Response)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from stash import websession as ws
from stash.capture import capture_async
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
    # fetch() POSTs (the board) send the token in the X-CSRF-Token header; plain
    # form POSTs (login, logout) send it in a hidden field. Accept either.
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


# --- board -----------------------------------------------------------------

_SEARCH_LIMIT = 200  # ranked ids the board intersects with its other filters

async def board_page(request: Request) -> Response:
    # The only screen. It boots by fetching /board/notes; the server renders only
    # the shell (with the CSRF meta), never note text, so note content reaches
    # the page as JSON data and is placed with textContent, never as HTML.
    return _render(request, "board.html", {})


async def board_notes(request: Request) -> Response:
    storage = request.app.state.storage
    return JSONResponse({"notes": storage.board_notes(),
                         "piles": [dict(p) for p in storage.board_piles()]})


async def board_search(request: Request) -> Response:
    # Hybrid recall (BM25 + vector) for the top-bar search. Returns ranked note
    # ids the board intersects with its tag/category/time filters. An empty query
    # means "search inactive"; the client treats a null/absent id list as "show
    # all", so there is no result list here, only ids. The CPU-bound embed is
    # offloaded off the loop; recall.search's own hardening handles odd input.
    q = request.query_params.get("q", "")
    if not q.strip():
        return JSONResponse({"q": q, "ids": []})
    storage = request.app.state.storage
    embedder = request.app.state.embedder
    qvec = await asyncio.to_thread(embedder.embed, q)
    results = search(storage, embedder, q, limit=_SEARCH_LIMIT, query_embedding=qvec)
    return JSONResponse({"q": q, "ids": [r.note_id for r in results]})


async def _json_body(request: Request):
    # Parse a JSON request body, raising 400 on malformed input rather than 500.
    try:
        return await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json body")


async def board_positions(request: Request) -> Response:
    _require_csrf(request, "")
    body = await _json_body(request)
    positions = body.get("positions") if isinstance(body, dict) else None
    if not isinstance(positions, list):
        raise HTTPException(status_code=400, detail="positions must be a list")
    written = request.app.state.storage.save_positions(positions)
    return JSONResponse({"ok": True, "written": written})


async def board_piles(request: Request) -> Response:
    _require_csrf(request, "")
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid body")
    piles = body.get("piles", [])
    assignments = body.get("assignments", {})
    if not isinstance(piles, list) or not isinstance(assignments, dict):
        raise HTTPException(status_code=400, detail="invalid piles payload")
    mapping = request.app.state.storage.save_piles(piles, assignments)
    # Keys are client ids (strings); JSON object keys are strings already.
    return JSONResponse({"ok": True, "piles": mapping})


async def board_add(request: Request) -> Response:
    _require_csrf(request, "")
    body = await _json_body(request)
    text = body.get("text", "") if isinstance(body, dict) else ""
    if not isinstance(text, str) or not text.strip():
        return JSONResponse({"ok": False, "error": "empty note"}, status_code=400)
    storage = request.app.state.storage
    embedder = request.app.state.embedder
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    result = await capture_async(storage, embedder, text, "web", now)
    return JSONResponse({"ok": True, "note_id": result.note_id,
                         "receipt": result.receipt})


async def board_delete(request: Request) -> Response:
    _require_csrf(request, "")
    body = await _json_body(request)
    try:
        note_id = int(body.get("note_id")) if isinstance(body, dict) else None
    except (TypeError, ValueError):
        note_id = None
    if note_id is None:
        raise HTTPException(status_code=400, detail="note_id required")
    deleted = request.app.state.storage.delete_note(note_id)
    return JSONResponse({"ok": True, "deleted": deleted})


_BOARD_NAME_MAX = 80


async def board_boards_list(request: Request) -> Response:
    rows = request.app.state.storage.list_boards()
    import json
    out = []
    for r in rows:
        try:
            cfg = json.loads(r["config"])
        except Exception:
            cfg = {}
        out.append({"id": r["id"], "name": r["name"], "config": cfg})
    return JSONResponse({"boards": out})


async def board_boards_save(request: Request) -> Response:
    _require_csrf(request, "")
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid body")
    name = body.get("name")
    config = body.get("config")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="board name required")
    if len(name) > _BOARD_NAME_MAX:
        raise HTTPException(status_code=400, detail="board name too long")
    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="config must be an object")
    import json
    board_id = request.app.state.storage.save_board(name.strip(), json.dumps(config))
    return JSONResponse({"ok": True, "id": board_id})


async def board_boards_delete(request: Request) -> Response:
    _require_csrf(request, "")
    body = await _json_body(request)
    try:
        board_id = int(body.get("id")) if isinstance(body, dict) else None
    except (TypeError, ValueError):
        board_id = None
    if board_id is None:
        raise HTTPException(status_code=400, detail="id required")
    deleted = request.app.state.storage.delete_board(board_id)
    return JSONResponse({"ok": True, "deleted": deleted})


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
        # The board is the only screen (W5). Search, topic and time are controls
        # on it; the former Search/Timeline/Topics/Reminders pages are gone.
        Route("/", board_page, methods=["GET"]),
        Route("/board/notes", board_notes, methods=["GET"]),
        Route("/board/search", board_search, methods=["GET"]),
        Route("/board/positions", board_positions, methods=["POST"]),
        Route("/board/piles", board_piles, methods=["POST"]),
        Route("/board/add", board_add, methods=["POST"]),
        Route("/board/delete", board_delete, methods=["POST"]),
        Route("/board/boards", board_boards_list, methods=["GET"]),
        Route("/board/boards", board_boards_save, methods=["POST"]),
        Route("/board/boards/delete", board_boards_delete, methods=["POST"]),
        Mount("/static", StaticFiles(directory=_STATIC_DIR), name="static"),
    ]
    app = Starlette(routes=routes)
    app.state.storage = storage
    app.state.embedder = embedder
    app.state.config = config
    app.add_middleware(AuthGuard, config=config)
    return app
