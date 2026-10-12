# stash W4 (Board) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the board prototype into the app's primary surface: a single-page
canvas of draggable paper notes backed by the user's real notes, with a filter
bar (time order, topic, active timers), add-a-note by `+` or double-click,
drop-to-stack piles, and a layout that persists server-side so the board is the
same on any device and survives a restart. `/board` becomes the home page.

**Architecture:** A new `/board` route serves a canvas page (Muuri + light
inertia, carried from the prototype) that boots from a JSON endpoint of the
user's notes and their saved board positions, then saves position and stacking
changes back through small CSRF-protected POST endpoints. Board layout is new
durable state in SQLite (a `board_layout` table keyed by note id), added by a
numbered migration; it is NOT derived (a reindex must not clear it), it is the
user's own arrangement. Notes, tags, categories, and timers come from the
existing core; adding a note from the canvas goes through the existing
`capture()`. The board reads and writes only through the core and the new layout
store; it never mutates a note's raw text.

**Tech Stack:** Python >=3.10, Starlette + Jinja2 (existing web stack), Muuri
0.9.5 from cdnjs (vendored into static for the app, matching the no-external-fetch
posture of the htmx shim), stdlib `sqlite3`, `pytest` + `pytest-asyncio` + httpx
(ASGITransport), `uv`.

**Spec:** `docs/superpowers/specs/2026-09-24-stash-design.md` (section 6.10 Web UI
is extended by this slice; the board is the new default of the five surfaces).

## Global Constraints

- **The raw note is sacred.** The board reads notes and writes only board layout
  and stacking. Its one note-write path is capturing a new note via `capture()`.
  No route edits or deletes a note's raw text. (Deleting a note from the board,
  if included, removes the note row and cascades; that is a real delete the user
  asked for, handled by a dedicated CSRF-protected endpoint, not a raw-text edit.)
- **Layout is durable state, not derived.** `board_layout` is written by the user
  dragging notes; a `reindex` must leave it untouched (like `reminders` and
  `kv`). A note deleted cascades its layout row away via FK.
- **Auth + CSRF unchanged.** Every board route sits behind the existing
  deny-by-default auth guard. Every state-changing POST (save positions, save
  stacks, add, delete) carries and verifies the session CSRF token before acting.
- **No external fetch at runtime.** Muuri is vendored into `stash/static` and
  served locally, like the htmx shim, so the app needs no CDN at run time and
  works offline (consistent with "processing and storage are local").
- **Degrade without JS.** The board needs JS, but the other pages (Search,
  Timeline, Topics, Reminders) remain and are reachable from the nav, so a
  JS-less or scripted client still has the full text UI.
- **Escaping.** All note text and labels rendered into the page or returned as
  JSON are escaped/encoded; the board builds DOM from text via safe APIs, never
  by interpolating raw note text into HTML.
- **No em-dashes in prose, comments, template copy, or commit messages.**

## Review Focus

- **Auth/CSRF on every new route:** the notes JSON, position-save, stack-save,
  add, and delete routes reject unauthenticated access and (for POSTs) a missing/
  bad CSRF token before any read of note content or any write. Covered by the
  authz sweep + dedicated CSRF tests.
- **Layout durability:** `reindex` does not touch `board_layout`; a deleted note
  cascades its layout and stack rows. Storage tests.
- **XSS:** a note whose raw text contains markup renders escaped on the board and
  in the JSON (as data, not HTML). Test with a script-tag note.
- **Position/stack save integrity:** saving positions for notes that exist
  updates them; a payload referencing an unknown note id is ignored, not an
  error; a malformed payload is a 400, not a 500.
- **Board is additive to the core:** notes/tags/categories/timers shown on the
  board match what the text pages show (same storage reads), so the board cannot
  drift from the real data.

## New state and surface

- Migration 2: `board_layout(note_id PK FK -> notes ON DELETE CASCADE, x REAL,
  y REAL, z INTEGER, pile_id INTEGER NULL, updated_at TEXT)` and
  `board_piles(id PK, label TEXT, collapsed INTEGER DEFAULT 0, updated_at TEXT)`.
  Durable state; reindex leaves it alone.
- storage: `board_notes()` (each note with raw, created_at, source, tags,
  category, reminder fire_at/status, and its layout x/y/z/pile if any);
  `save_positions(list of {note_id,x,y,z})`; `save_piles(piles, assignments)`;
  `delete_note(note_id)` (real delete, cascades).
- web: `GET /board` (page), `GET /board/notes` (JSON), `POST /board/positions`,
  `POST /board/piles`, `POST /board/add`, `POST /board/delete`. `/board` becomes
  the home; `/` redirects to `/board` (Search moves to `/search-page` or stays at
  `/` as a list; decision: keep Search reachable, make `/board` the nav home and
  the post-login redirect).
- static: vendored `muuri.min.js`; board CSS + JS (the prototype, adapted to load
  from the endpoint and save back).
- templates: `board.html`; nav gains a Board item and marks it active/home.

---

### Task 0: Migration + layout storage

**Files:**
- Modify: `stash/migrations.py`, `stash/storage.py`, `stash/reindex.py`
- Test: `tests/test_board_storage.py`, additions to `tests/test_reindex.py`

**Interfaces:**
- Produces: migration 2 (board_layout, board_piles); storage `board_notes`,
  `save_positions`, `save_piles`, `delete_note`.

- [ ] **Step 1:** Append migration 2 (never edit migration 1). Both tables as
      above; FK `ON DELETE CASCADE` from `board_layout.note_id` and
      `board_layout.pile_id -> board_piles(id) ON DELETE SET NULL`.
- [ ] **Step 2:** `board_notes()` left-joins notes to their layout, tags, meta,
      and reminder, returning one record per note with everything the canvas
      needs. `save_positions`, `save_piles` upsert; `delete_note` deletes the
      note (cascades layout + tags + meta + reminders).
- [ ] **Step 3:** `reindex` does NOT touch `board_layout`/`board_piles` (assert
      in a test). A deleted note cascades its layout row (assert).

**Acceptance:** migration applies idempotently; `board_notes` returns a note's
position after `save_positions`; `reindex` preserves layout; deleting a note
removes its layout row.

---

### Task 1: Board notes JSON endpoint

**Files:**
- Modify: `stash/web.py`
- Test: `tests/test_board_api.py`

**Interfaces:**
- Produces: `GET /board/notes` returning JSON `{notes: [...]}` with id, raw,
  created_at, source, tags, category, timer (reminder fire_at when pending), and
  x/y/z/pile when positioned.

- [ ] **Step 1:** Handler reads `storage.board_notes()`, shapes the JSON, returns
      `JSONResponse`. Note text travels as a JSON string value (escaped by the
      encoder), never interpolated into HTML.
- [ ] **Step 2:** Behind auth: unauthenticated `GET /board/notes` redirects to
      login and returns no note content.

**Acceptance:** authenticated returns the user's notes with positions; a
script-tag note comes back as a JSON string (not executable); unauth is a
redirect with no note text.

---

### Task 2: Save positions + piles + delete (CSRF)

**Files:**
- Modify: `stash/web.py`
- Test: `tests/test_board_save.py`

**Interfaces:**
- Produces: `POST /board/positions` ({positions:[{note_id,x,y,z}]}),
  `POST /board/piles` ({piles:[...], assignments:{note_id:pile_id}}),
  `POST /board/delete` ({note_id}). All CSRF-checked (accept token via
  X-CSRF-Token header for fetch, matching the htmx header convention).

- [ ] **Step 1:** Each POST verifies the session CSRF token before acting; a
      missing/bad token is 403. Bodies are JSON; a malformed body is 400.
- [ ] **Step 2:** `save_positions` ignores unknown note ids rather than erroring.
      `delete` removes the note and returns ok; it is a real delete the user
      initiated, the one destructive board action.
- [ ] **Step 3:** No SQLite transaction spans an await; saves are short
      transactions on the loop thread.

**Acceptance:** valid CSRF + payload persists positions (visible via
`/board/notes`); bad CSRF is 403 with no write; unknown note id is ignored;
malformed JSON is 400; delete removes the note and its layout.

---

### Task 3: The board page (canvas wired to real data)

**Files:**
- Create: `stash/templates/board.html`, `stash/static/board.css`,
  `stash/static/board.js`, vendored `stash/static/muuri.min.js`
- Modify: `stash/web.py` (route + make `/board` home), `stash/templates/base.html`
  (nav)
- Test: `tests/test_board_page.py`

**Interfaces:**
- Produces: `GET /board` serving the canvas; it fetches `/board/notes`, renders
  paper notes with Muuri, saves positions on drag release and on add/delete.
- The prototype's filter bar (Newest/Oldest, tag/category chips, Active timers),
  `+` and double-click composer, carried over but driven by real data.

- [ ] **Step 1:** Vendor Muuri 0.9.5 into static. board.js boots from
      `/board/notes`, builds the board, and on drag release POSTs positions with
      the CSRF token from the `<meta>`.
- [ ] **Step 2:** Add composer posts to `/board/add` (capture, source "web"),
      then re-fetches notes; delete posts to `/board/delete`. Chips and timers
      filter client-side over the fetched set.
- [ ] **Step 3:** Make `/board` the nav home and the post-login redirect; keep
      Search at `/search` (or `/` list retained) reachable from the nav. Board is
      marked active.
- [ ] **Step 4:** All note text set via `textContent`, never `innerHTML`, so
      markup in a note cannot execute. Theme tokens match the app (ink on paper,
      mono raw text, indigo accent); dark mode via prefers-color-scheme.

**Acceptance:** `GET /board` returns the page with the CSRF meta and the Muuri
script; the page is behind auth; a note with a script tag renders as text.

---

### Task 4: Stacking piles

**Files:**
- Modify: `stash/static/board.js`, `stash/static/board.css`, `stash/web.py`,
  `stash/storage.py`
- Test: `tests/test_board_piles.py`

**Interfaces:**
- Produces: dropping a note onto another forms a pile (a stacked, labeled group);
  a pile can be collapsed/expanded; un-stacking drags a note out. Pile membership
  and labels persist via `/board/piles`.

- [ ] **Step 1:** Drop-to-stack: when a dragged note is released over another,
      assign both to a pile (new or existing), save via `/board/piles`. A pile
      renders as an offset stack with a count and an editable label (default from
      the notes' shared tag or first words).
- [ ] **Step 2:** Collapse/expand a pile; dragging a note out of a pile removes
      it from the pile (and deletes the pile when it drops below two members).
- [ ] **Step 3:** Pile state round-trips: reload shows the same piles, labels, and
      collapsed state.

**Acceptance:** stacking two notes creates a persisted pile with a non-empty
label; un-stacking below two members removes the pile; reload reproduces piles
and collapsed state.

---

### Task 5: Docs + authz sweep

**Files:**
- Modify: `tests/test_web_authz_sweep.py`, `README.md`, `docs/guides/web-setup.md`
- Test: the sweep covers `/board`, `/board/notes`, and the POST routes.

- [ ] **Step 1:** Add the board routes to the authz sweep (GET redirects unauth,
      POST is 403/redirect with no effect).
- [ ] **Step 2:** README + web-setup: the board is the home page; what it does;
      that layout persists server-side.

**Acceptance:** the sweep passes for the full board route table; docs describe the
board.

---

## VERIFY / GATE

- Suite green; new board tests run with the fake embedder + ASGITransport (no
  model, no socket). sqlite-vec-backed reads run where the extension is installed.
- Independent review at the gate (R2): auth/CSRF on every new route, layout
  durability (reindex-safe, cascade), XSS (textContent + JSON encoding), save
  integrity (unknown ids ignored, malformed = 400), and that the board reads the
  same data the text pages do.
- Branch: `feat/w4-board` (off `feat/m3-w2-clustering` so it has the full app).
  On GO, record in `docs/RECENTLY-SHIPPED.md` and note the board in `ROADMAP.md`.

**Verification note:** this build environment has no PyPI (no sqlite-vec) and no
browser, so storage-backed reads use a fake storage in tests and the live
drag/stack interactions are verified by the user in a browser. The route/auth/
CSRF/JSON-shape/escaping logic is verified in-session via ASGITransport; the
canvas behavior (drag momentum, drop-to-stack, persistence round-trip) is
confirmed by the user running the app, as with the earlier web slices.

## Explicitly not in W4

- Multi-board / named boards (one board per install, matching one-user scope).
- Real-time multi-viewer sync (single user, one process).
- Changing capture, recall, reminders, Telegram, or clustering beyond adding the
  board's read/layout endpoints.

---
cam
