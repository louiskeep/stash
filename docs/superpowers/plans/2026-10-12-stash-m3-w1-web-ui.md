# stash Milestone 3 / W1 (Web UI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local, server-rendered web UI over the existing core so the user
can search, browse a timeline, see deterministic Topics, open a note's detail
(raw + derived + related-by-embedding), and view reminders, behind a single
password with a signed session. The web server runs in the same asyncio process
as the Telegram poller and the reminder scheduler (spec 6.11), over one SQLite DB.

**Architecture:** A Starlette ASGI app, server-rendered with Jinja2 and a sprinkle
of htmx for live search, mounted into the existing `stash serve` asyncio loop
(alongside `poll_loop` and `sched_loop`) via `uvicorn.Server.serve()` as a third
coroutine. Routes are thin: they call the existing synchronous core
(`recall.search`, `storage.*`, `capture`) and render templates. The CPU-bound
embed call on the search path is offloaded with `asyncio.to_thread`, exactly as
`commands.py` does. No SQLite transaction is held across an `await`; SQLite is
touched only on the event-loop thread.

**Tech Stack:** Python >=3.10, `starlette`, `jinja2`, `uvicorn` (ASGI server),
`itsdangerous` (signed cookies; a Starlette dependency already), stdlib `hmac`/
`secrets` for password + CSRF comparison, stdlib `sqlite3`, `pytest` +
`pytest-asyncio` + `httpx` (already present; `httpx.ASGITransport` drives the app
in tests with no socket). `uv`.

**Spec:** `docs/superpowers/specs/2026-09-24-stash-design.md` (v2), section 6.10
(Web UI), 6.11 (App composition), 8 (Security).

## Global Constraints

- **No generative AI.** The web UI only reads and displays stored/derived data
  and runs the existing embedding-based search. It never generates prose.
- **The raw note is sacred.** The web UI has exactly one write path to notes:
  capturing a new note from the Search/Timeline "add" box, which goes through the
  existing `capture()`. It never edits or deletes an existing note's raw text in
  W1. (No note-mutation routes.)
- **Auth before anything.** Every route except the login page and static assets
  requires a valid signed session. An unauthenticated request to any app route
  returns a redirect to login, never note content.
- **Secrets never logged.** The password and session secret are never written to
  logs or rendered into any page.
- **Localhost by default.** The server binds to `config.web_bind` (default
  `127.0.0.1`). Binding beyond localhost is the operator's explicit choice and is
  documented, not defaulted.
- **CSRF on every state-changing route.** All POSTs (login, logout, add-note)
  carry and verify a CSRF token bound to the session. A POST with a missing or
  bad token is rejected (403) before any effect.
- **Cookies are hardened.** The session cookie is `HttpOnly`, `SameSite=Lax`, and
  `Secure` is set when the request is HTTPS (so localhost HTTP dev still works but
  a real deployment behind TLS gets Secure). Path-scoped to `/`.
- **No transaction across an `await`.** The embed offload is the only `await` on
  the search path and it happens outside any DB transaction.
- **No em-dashes in prose, comments, template copy, or commit messages.**
- **No new network egress.** The web UI adds no outbound calls. uvicorn binds a
  local socket; that is the only new surface.

## Review Focus

- **Auth bypass:** no app route (search, timeline, topics, note detail,
  reminders, add-note, the htmx search fragment) returns any note content without
  a valid session. Covered by Task 6 tests that hit every route unauthenticated.
- **CSRF:** a forged or missing CSRF token on login, logout, and add-note is
  rejected before the action runs. Task 3 / Task 6 tests.
- **XSS:** raw note text (which can contain `<`, `>`, `&`, quotes, markup) is
  autoescaped everywhere it renders (search tiles, timeline, note detail). Jinja2
  autoescape is on for `.html`; a Task 5 test asserts a note containing a script
  tag renders escaped.
- **FTS/vec injection and empty query:** the search route reuses the hardened
  `storage.search_bm25` / `recall.search` path (already quotes FTS tokens); an
  empty or whitespace query returns an empty result set, not an error. Task 4 test.
- **Timing-safe comparison:** password and CSRF checks use `hmac.compare_digest`,
  not `==`. Task 2 / Task 3 review.
- **Single process (6.11):** the web server is a coroutine in the same loop as
  the poller and scheduler; a clean shutdown signal stops all three. Task 7 test.
- **No transaction across await; SQLite on the loop thread only.** Task 4 review.

## New core read methods (storage), used by the pages

The pages need read-only queries the core does not expose yet (the CLI inlines
them ad hoc). These are added to `storage.py` with tests so the web routes stay
thin and the queries are covered independently of HTTP:

- `recent_notes(limit, before_id=None) -> list[Row]`: timeline, newest first,
  keyset-paginated by `id` (stable under inserts; `before_id` gives the next page).
- `notes_by_tag(tag, limit) -> list[Row]`: Topics drill-down.
- `tag_counts() -> list[Row]` (`tag`, `n`): Topics index. (Promotes the CLI
  `tags` query into storage so the page and CLI share one source.)
- `category_counts() -> list[Row]` (`category`, `n`): Topics by `@category`.
- `related_by_embedding(note_id, limit) -> list[Row]`: note detail "related",
  a `vec_notes` nearest-neighbor on the note's own stored embedding, excluding
  itself.
- `note_detail(note_id) -> dict | None`: the note row + its tags + its meta
  (category, intent) + its reminder (status, fire_at, sent_at) if any, in one
  place, for the detail page.
- `list_reminders(limit) -> list[Row]`: reminders joined to their note's raw,
  ordered `fire_at`, for the Reminders page (status, fire_at, sent_at, attempts).

All are plain `SELECT`s, no schema change, no migration. `recent_notes` and
`related_by_embedding` are the only ones with pagination/NN subtlety and get
dedicated tests.

---

### Task 0: Dependencies and the ASGI test harness

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_web_smoke.py`

**Interfaces:**
- Produces: `starlette`, `jinja2`, `uvicorn` installed; an app factory
  `stash.web:build_app(storage, embedder, config) -> Starlette` importable; a test
  helper that drives it with `httpx.AsyncClient(transport=ASGITransport(app))`.

- [ ] **Step 1:** Add `starlette`, `jinja2`, `uvicorn` to `[project].dependencies`;
      keep `httpx` where it is (used by tests and the Telegram client).
- [ ] **Step 2:** Create `stash/web.py` with a stub `build_app` that returns a
      Starlette app exposing one unauthenticated `GET /healthz` returning `200 ok`.
- [ ] **Step 3:** `tests/test_web_smoke.py`: assert `GET /healthz` is 200 over
      `ASGITransport` (no network). This proves the harness before any real route.

**Acceptance:** `uv run pytest tests/test_web_smoke.py` passes; the app builds and
answers `/healthz` in-process with no socket bound.

---

### Task 1: Templates, base layout, autoescape, static

**Files:**
- Create: `stash/templates/base.html`, `login.html`, `search.html`,
  `timeline.html`, `topics.html`, `note.html`, `reminders.html`,
  `_tiles.html` (htmx search-results fragment)
- Create: `stash/static/app.css`, vendored `htmx.min.js`
- Modify: `stash/web.py` (Jinja2 environment with autoescape on; static mount)

**Interfaces:**
- Produces: a `Jinja2Templates` env rooted at `stash/templates` with autoescape
  enabled for `.html`; a `/static` mount; a shared `base.html` the pages extend.

- [ ] **Step 1:** Build `base.html` with the nav (Search, Timeline, Topics,
      Reminders), a content block, and a CSRF `<meta>` for htmx POSTs. Plain,
      legible CSS; no build step; htmx loaded from `/static`.
- [ ] **Step 2:** Wire `Jinja2Templates(directory=...)`; confirm autoescape is on.
- [ ] **Step 3:** Render a trivial page through a temporary route in a test to
      assert a value containing `<script>` comes back escaped.

**Acceptance:** a template rendering `{{ "<script>alert(1)</script>" }}` outputs
`&lt;script&gt;...`; `/static/app.css` serves 200.

---

### Task 2: Session auth (login, logout, guard middleware)

**Files:**
- Modify: `stash/web.py`
- Create: `stash/websession.py` (sign/verify a session token with the configured
  secret via `itsdangerous`; set/clear the cookie with the hardening flags)
- Test: `tests/test_web_auth.py`

**Interfaces:**
- Produces: `GET /login` (form), `POST /login` (verify password -> set signed
  session cookie -> redirect to `/`), `POST /logout` (clear cookie), and an auth
  guard that redirects any unauthenticated app route to `/login`.
- Consumes: `config.web_password`, `config.web_session_secret`.

- [ ] **Step 1:** Fail-fast config check for the web: if `web_password` or
      `web_session_secret` is unset, `build_app` raises a clear `RuntimeError`
      (mirrors `serve._validate_config`), so the server never runs open.
- [ ] **Step 2:** `POST /login` compares the submitted password with
      `hmac.compare_digest`; on success issues a signed, HttpOnly, SameSite=Lax
      cookie (Secure when `request.url.scheme == "https"`); on failure re-renders
      login with a generic error and a 401, no lockout, no logging of the attempt.
- [ ] **Step 3:** Auth guard: unauthenticated request to any app route ->
      303 redirect to `/login`. A tampered/expired cookie is treated as
      unauthenticated.
- [ ] **Step 4:** `POST /logout` clears the cookie and redirects to `/login`.

**Acceptance (tests):**
- Unauthenticated `GET /` redirects to `/login`.
- Correct password sets a cookie and unlocks `GET /`.
- Wrong password: 401, no cookie set.
- A cookie signed with a different secret is rejected (treated as logged out).
- The cookie carries HttpOnly and SameSite; Secure is present when the request
  scheme is https and absent on plain http.

---

### Task 3: CSRF protection for state-changing routes

**Files:**
- Modify: `stash/web.py`, `stash/websession.py`
- Test: `tests/test_web_csrf.py`

**Interfaces:**
- Produces: a per-session CSRF token embedded in every rendered form and in a
  `<meta>` for htmx; a check on every POST that compares the submitted token
  (`hmac.compare_digest`) to the session's token before the handler runs.

- [ ] **Step 1:** Derive a CSRF token bound to the session (signed, stable per
      session). Expose it to templates and to htmx (`hx-headers` from the meta).
- [ ] **Step 2:** A POST without a valid token returns 403 before any effect
      (no login, no logout, no capture).
- [ ] **Step 3:** Login is the one bootstrap case: the login form issues a
      pre-session CSRF token (signed with the secret, not bound to a session yet)
      and `POST /login` verifies it, so even login cannot be cross-site forged.

**Acceptance (tests):**
- `POST /add` with a good session but no/forged CSRF token -> 403, no note written.
- `POST /add` with a valid token -> note is captured.
- `POST /login` with a missing CSRF token -> 403.

---

### Task 4: Search page + live htmx fragment

**Files:**
- Modify: `stash/web.py`
- Test: `tests/test_web_search.py`

**Interfaces:**
- Produces: `GET /` (Search page: query box + an "add a note" box + results),
  `GET /search?q=...` (htmx fragment `_tiles.html` for live results), `POST /add`
  (capture a note, CSRF-checked, redirect back).
- Consumes: `recall.search`, `capture.capture`, `embedder` (embed offloaded via
  `asyncio.to_thread`).

- [ ] **Step 1:** `GET /search?q=` offloads the query embed with
      `asyncio.to_thread`, calls `recall.search`, renders `_tiles.html`. Each tile
      links to the note detail and shows date, tags, source.
- [ ] **Step 2:** Empty/whitespace `q` renders an empty result list, not an error
      (reuses `recall.search`'s empty-query guard).
- [ ] **Step 3:** `POST /add` captures via `capture()` (source `"web"`), returns
      the receipt and the refreshed list.

**Acceptance (tests, fake embedder):**
- A seeded note is found by a query matching its text; the tile links to
  `/note/{id}`.
- Empty query -> 200 with zero tiles, no exception.
- `POST /add` with CSRF -> `storage.get_note` shows the new note with source
  `"web"`; a malformed query string never 500s.

---

### Task 5: Timeline, Topics, Note detail, Reminders pages

**Files:**
- Modify: `stash/web.py`, `stash/storage.py` (the new read methods above)
- Test: `tests/test_web_pages.py`, additions to `tests/test_storage.py`

**Interfaces:**
- Produces: `GET /timeline` (keyset-paginated, newest first), `GET /topics`
  (tag + category counts), `GET /topics/tag/{tag}` and `/topics/category/{cat}`
  (drill-down), `GET /note/{id}` (raw + derived + related-by-embedding + reminder),
  `GET /reminders` (status, fire_at, sent_at).
- Consumes: the new `storage` read methods.

- [ ] **Step 1:** Add and unit-test the storage read methods (`recent_notes`,
      `notes_by_tag`, `tag_counts`, `category_counts`, `related_by_embedding`,
      `note_detail`, `list_reminders`). `recent_notes` keyset pagination and
      `related_by_embedding` self-exclusion get explicit cases.
- [ ] **Step 2:** Timeline page renders newest-first with a "more" link carrying
      `before_id`.
- [ ] **Step 3:** Topics index lists `#tag (n)` and `@category (n)`; each links to
      its drill-down list.
- [ ] **Step 4:** Note detail shows the verbatim raw text (escaped), its tags,
      category, intent, any reminder (status + fire_at + sent_at), and up to N
      related notes by embedding (each linking onward).
- [ ] **Step 5:** Reminders page lists reminders with status, fire time, and sent
      time; no action buttons in W1 (read-only view).
- [ ] **Step 6:** A note whose raw text contains `<script>` renders escaped on
      detail, timeline, and search tiles.

**Acceptance (tests):**
- Timeline returns notes newest-first and the second page (via `before_id`)
  continues without overlap or gap.
- A note with `#x` appears under `/topics/tag/x` and contributes to its count.
- Note detail for a reminder note shows the reminder's status and fire time.
- `related_by_embedding` never returns the note itself.
- Escaping test passes on all three surfaces.

---

### Task 6: Auth coverage sweep across every route

**Files:**
- Test: `tests/test_web_authz_sweep.py`

**Interfaces:**
- Produces: a parametrized test that walks every app route (GET and POST) with no
  session and asserts a redirect to `/login` (or 403 for a CSRF-guarded POST),
  and never leaks note content.

- [ ] **Step 1:** Enumerate routes; assert each unauthenticated GET redirects to
      `/login` and the body contains no seeded note text.
- [ ] **Step 2:** Assert each POST unauthenticated is a redirect or 403, never an
      effect (no note written, no cookie issued).

**Acceptance:** the sweep passes for the full route table; adding a route without
auth fails this test.

---

### Task 7: App composition - web server in the serve loop (6.11)

**Files:**
- Modify: `stash/serve.py`, `stash/cli.py` (so `serve` also starts the web app)
- Test: `tests/test_serve_web.py`

**Interfaces:**
- Produces: `stash serve` runs `poll_loop`, `sched_loop`, and the uvicorn web
  server as three coroutines in one loop over one DB; one stop signal shuts all
  three down cleanly. The web app is skipped (with a logged notice) if the web
  password/secret are unset, so the Telegram-only daemon still runs.

- [ ] **Step 1:** Build the app with `build_app(storage, embedder, config)` and
      run it via `uvicorn.Server(Config(app, host=config.web_bind,
      port=config.web_port, log_level=...))` as a gathered coroutine; the existing
      `stop` event triggers `server.should_exit = True`.
- [ ] **Step 2:** If web config is incomplete, log a single notice and run only
      the poller + scheduler (no crash), so M2's daemon behavior is preserved.
- [ ] **Step 3:** Shutdown: SIGINT/SIGTERM stops all three loops; the single-
      instance flock and client close still happen in `finally`.

**Acceptance (tests):**
- With web config set, the gathered serve starts the app (asserted via a bound-app
  health check through `ASGITransport`, not a real socket) and a stop event ends
  all three coroutines.
- With web config unset, serve runs poller + scheduler and logs the skip, no
  exception.

---

### Task 8: Docs - run guide and security note

**Files:**
- Create: `docs/guides/web-setup.md`
- Modify: `README.md` (correct the stale Postgres line to SQLite; add a Web UI
  line), `.env.example` (web vars with safe defaults and a comment that exposing
  beyond localhost needs TLS)

**Interfaces:**
- Produces: a short runbook: set `STASH_WEB_PASSWORD` + `STASH_WEB_SESSION_SECRET`,
  run `stash serve`, open `http://127.0.0.1:8000`, log in. The localhost/TLS
  boundary from spec section 8 is stated plainly.

- [ ] **Step 1:** Write `docs/guides/web-setup.md` (env, run, the localhost vs
      `0.0.0.0`+TLS boundary, backup pointer).
- [ ] **Step 2:** Fix the README's architecture line (it says Postgres/pgvector;
      the product is SQLite + sqlite-vec + FTS5) and add the web UI to the run
      section.
- [ ] **Step 3:** Add `STASH_WEB_PASSWORD`, `STASH_WEB_SESSION_SECRET`,
      `STASH_WEB_BIND`, `STASH_WEB_PORT` to `.env.example` with comments.

**Acceptance:** a reader following the guide can start the server, log in, search,
and browse; the README no longer claims Postgres.

---

## VERIFY / GATE

- Full suite green, including the existing real-encoder recall gate (unchanged by
  this slice). New web tests run with the fake embedder and `ASGITransport` (no
  model load, no socket), so they are fast.
- Test-strength measured on the changed units (auth, CSRF, escaping, the new
  storage reads, serve composition) at VERIFY.
- Independent review at the gate (R2): one reviewer for spec+quality, a
  whole-slice review before merge, as with M1/M2. Review focus list above is the
  checklist.
- Branch: `feat/m3-w1-web-ui`. On GO, move the W1 entry to
  `docs/RECENTLY-SHIPPED.md` with gate evidence and tick W1 in `ROADMAP.md`.

## Explicitly not in W1

- HDBSCAN clustering and ML auto-Topics (that is W2; Topics here are the
  deterministic tag/category grouping M1 already computes).
- Packaging: the `[project.scripts]` entry point, Dockerfile, backup tooling
  (that is W3). W1 still starts via `python -m stash serve`.
- Editing or deleting a note's raw text from the web (the raw note is sacred; no
  mutation routes in W1).
- Reminder actions from the web (snooze/cancel). W1's Reminders page is read-only.

---
cam
