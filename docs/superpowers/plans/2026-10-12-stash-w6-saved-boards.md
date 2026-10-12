# stash W6 (Saved Boards + UI refinements) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine the single-screen board and add saved boards. (1) Split the one
Topic dropdown into separate Category and Tag dropdowns. (2) Vertical scroll only,
with a small, subtle scrollbar; no horizontal scroll. (3) Saved boards: configure
the filters, save the set under a name, and your saved boards live down the left
side; clicking one re-applies its filters. The active-timers rail is replaced by a
Reminders button in the top bar (left of Sign out) that opens a dropdown of active
countdowns. (4) Date-only range filtering (no time of day).

**Architecture:** Saved boards are a small new durable table (`boards`), one row
per named filter set, written/read/removed through CSRF-protected endpoints; the
left side lists them and clicking one applies its stored filter config
client-side. The canvas layout stays shared across boards (a saved board is
filters only, not positions). The Category/Tag split, the date-only inputs, the
scroll change, and the reminders dropdown are client + CSS changes over the W5
board. Note data, search, and capture are unchanged.

**Tech Stack:** Starlette + Jinja2, Muuri (vendored), stdlib `sqlite3`, `pytest` +
httpx (ASGITransport), `uv`. No new deps.

## Global Constraints

- **The raw note is sacred.** Boards store only a filter config; nothing here
  reads or writes note text beyond the existing search/add/delete.
- **Saved boards are durable state, not derived.** A `boards` table like
  `reminders`/`board_layout`: a reindex never touches it.
- **Auth + CSRF unchanged.** Board save/delete are CSRF-protected POSTs; list is a
  GET behind the guard.
- **One shared canvas.** A saved board re-applies filters only; note positions are
  the same whichever board is active.
- **No horizontal scroll.** The board scrolls vertically only; saved x-positions
  are clamped to the visible width so no card sits off the right edge. The
  scrollbar is thin and subtle (styled, reduced-motion safe).
- **Date-only filtering.** The From/To controls are `type="date"`; the range is
  inclusive of the whole To day (compare against end-of-day).
- **No em-dashes in prose, comments, template copy, or commit messages.**

## Review Focus

- **Boards endpoints:** list/save/delete are behind auth; save/delete are
  CSRF-checked (403 on bad token before write); a malformed body is 400; a
  board name is required and bounded; delete of an unknown id is a no-op 200.
- **Boards durability:** a reindex leaves `boards` untouched; boards are per the
  single user (no cross-user concern in v1).
- **Filter config round-trips:** saving the current category/tag/date/search and
  reloading reproduces exactly those filters when the board is applied.
- **Category + Tag are independent AND filters:** selecting a category and a tag
  narrows to notes matching both; each empty is a no-op.
- **Date-only correctness:** a To date includes that whole day; From after To
  shows the empty state, not an error.
- **No horizontal scroll at any width; the reminders dropdown is keyboard
  reachable and closes on outside click/Escape.**
- **No regression:** positions, piles, add, delete, search, auth/CSRF intact.

## New state and surface

- Migration 3: `boards(id PK, name TEXT NOT NULL, config TEXT NOT NULL,
  position INTEGER, created_at TEXT)`. `config` is a JSON string of the filter set
  (category, tag, from, to, search, order). Durable.
- storage: `list_boards()`, `save_board(name, config) -> id`,
  `delete_board(id) -> bool`.
- web: `GET /board/boards` (list), `POST /board/boards` (save {name, config}),
  `POST /board/boards/delete` ({id}). CSRF on the POSTs.
- board.js: a Category select and a Tag select; date-only From/To; a Boards list
  on the left (save-current + saved rows that apply on click, with a small delete
  control); a Reminders button that opens a dropdown panel of countdowns.
- board.css: thin subtle scrollbar; vertical-only board; left Boards column;
  reminders dropdown; remove the old rail styles.

---

### Task 0: Scroll, date-only, category/tag split (client + CSS)

**Files:**
- Modify: `stash/static/board.js`, `stash/static/board.css`,
  `stash/templates/board.html`
- Test: board page assertions in `tests/test_board_page.py`.

**Interfaces:**
- Produces: two dropdowns (`#category`, `#tag`); `#from`/`#to` are `type="date"`;
  board scrolls vertically only with a thin scrollbar.

- [ ] **Step 1:** Replace the single `#topic` select with `#category` and `#tag`
      selects, each built from its own counts; `state.cat` and `state.tag` set
      independently; both AND in `passes(n)` (already do). Remove the combined
      topic builder.
- [ ] **Step 2:** `#from`/`#to` become `type="date"`; `parseDT` parses a date,
      and the To bound compares against end-of-day (23:59:59.999) so the To day is
      included. From-after-To yields the empty state.
- [ ] **Step 3:** CSS: `.board-wrap { overflow-y: auto; overflow-x: hidden }`;
      clamp the custom layout's x so no card exceeds the content width; style a
      thin, subtle scrollbar (webkit + firefox `scrollbar-width: thin`).

**Acceptance:** category and tag filter independently and together; the To date
includes its whole day; the board never scrolls horizontally; the scrollbar is
thin. Board page exposes `#category`, `#tag`, date inputs.

---

### Task 1: Boards storage + endpoints

**Files:**
- Modify: `stash/migrations.py`, `stash/storage.py`, `stash/web.py`,
  `stash/reindex.py` (assert untouched)
- Test: `tests/test_boards.py`

**Interfaces:**
- Produces: migration 3 (`boards`); `list_boards`, `save_board`, `delete_board`;
  `GET /board/boards`, `POST /board/boards`, `POST /board/boards/delete`.

- [ ] **Step 1:** Migration 3 (append-only). `save_board(name, config)` stores
      the JSON config string; `list_boards()` returns id/name/config ordered;
      `delete_board(id)` returns whether a row went.
- [ ] **Step 2:** Endpoints: list (GET, auth); save (POST, CSRF, name required
      and length-bounded, config must be a dict -> stored as JSON, 400 otherwise);
      delete (POST, CSRF, {id}, unknown id -> ok:true deleted:false).
- [ ] **Step 3:** `reindex` does not touch `boards` (assert).

**Acceptance:** save a board, list returns it with the config; delete removes it;
reindex preserves boards; bad CSRF -> 403 no write; missing name -> 400.

---

### Task 2: Saved boards UI (left column) + reminders dropdown

**Files:**
- Modify: `stash/static/board.js`, `stash/static/board.css`,
  `stash/templates/board.html`
- Test: board page assertions + `tests/test_boards.py` (round-trip via endpoints).

**Interfaces:**
- Produces: a left Boards column (a "Save current" action + the saved rows, each
  applying its filters on click, with a delete control); a Reminders button in the
  top bar (left of Sign out) opening a dropdown of active countdowns with
  click-to-focus.

- [ ] **Step 1:** On load, `GET /board/boards`; render the left column. "Save
      current" prompts for a name (inline input, not window.prompt, which is
      inert in some contexts) and POSTs the current filter config; the list
      refreshes. Each row on click sets `state` from its config and re-applies
      filters; a small x deletes it (CSRF).
- [ ] **Step 2:** The Reminders button toggles a dropdown panel listing notes
      with a pending timer (soonest first) and their countdowns; clicking one
      focuses its note (reveal if filtered, scroll, flash). The panel closes on
      outside click and Escape and is keyboard reachable.
- [ ] **Step 3:** Remove the old left rail markup/JS/CSS (`#rail`, `buildRail` is
      repurposed to feed the dropdown instead of the rail).

**Acceptance:** saving the current filters creates a named board that reappears on
reload and re-applies its filters on click; deleting removes it; the Reminders
button opens a countdown panel that jumps to a note and closes on Escape/outside
click.

---

### Task 3: Docs + sweep + GATE

**Files:**
- Modify: `tests/test_web_authz_sweep.py` (board boards routes), `README.md`,
  `docs/guides/web-setup.md`.

- [ ] **Step 1:** Add `/board/boards` (GET) and the two POSTs to the sweep.
- [ ] **Step 2:** Docs: saved boards (configure filters, save, click to apply),
      separate Category/Tag filters, the Reminders button, date-only range.

**Acceptance:** sweep passes for the full route table; docs describe saved boards
and the refined controls.

---

## VERIFY / GATE

- Suite green; boards tests use the fake storage + ASGITransport. The live
  category/tag/date filtering, saved-board apply, scroll behavior, and reminders
  dropdown are user-verified in a browser.
- Independent review at the gate: boards auth/CSRF/validation, durability
  (reindex-safe), filter round-trip, date-inclusive logic, no horizontal scroll,
  and no regression in the W4/W5 board behavior.
- Branch: `feat/w6-saved-boards` (off `feat/w5-single-screen`). On GO, record in
  `docs/RECENTLY-SHIPPED.md`.

**Verification note:** no sqlite-vec or browser here, so storage-backed reads use
a fake storage and the live UI is user-verified; the boards endpoints
(auth/CSRF/shape), migration, reindex-safety, and the filter/date logic are
verified in-session via ASGITransport and fake storage, with board JS passing
node --check. Run the suite with sqlite-vec before merge.

## Explicitly not in W6

- Per-board canvas layouts (a board is filters only; one shared layout).
- Reordering saved boards by drag (simple ordered list; reorder is a follow-up).
- Snooze/cancel reminders (the dropdown is display + click-to-focus).

---
cam
