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

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route


async def _healthz(request):
    # Unauthenticated liveness check. Says nothing about notes or config; it
    # exists so the serve loop and tests can confirm the app is mounted and
    # answering without logging in.
    return PlainTextResponse("ok")


def build_app(storage, embedder, config) -> Starlette:
    """Build the stash web application.

    `storage` and `embedder` are the already-opened core objects shared with
    the rest of the process; `config` carries the web bind/password/secret.
    Later tasks add the login guard, CSRF, templates, and the real pages; this
    task establishes the factory and the one unauthenticated route.
    """
    routes = [
        Route("/healthz", _healthz, methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    app.state.storage = storage
    app.state.embedder = embedder
    app.state.config = config
    return app
