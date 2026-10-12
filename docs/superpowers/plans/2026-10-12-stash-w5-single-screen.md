# stash W5 (Single Screen) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse stash's web UI into one screen: the board. The separate Search,
Timeline, Topics, and Reminders pages go away; each becomes a control on the
board. The top bar holds search; a dropdown picks a category or tag; an
order-by-time control sets a datetime range; and active reminders are pinned down
the left as live countdowns you can click to focus their note. All filters stack
(AND): search text, the selected tag/category, and the time range narrow the same
board together.

**Architecture:** The board already holds every note client-side and filters
tags/categories/timers in the browser. W5 adds a time-range filter and a
category/tag dropdown in the same client-side filter pass, a left reminders rail
built from the notes that carry a pending timer, and a server search endpoint
(`/board/search?q=`) that reuses `recall.search` (hybrid BM25 + vector) to return
ranked note ids; the board intersects those ids with the other filters. The old
page routes and their templates are removed; `recall.search`, the storage reads,
and `capture()` remain and now power the board's controls. One screen, one set of
data, several lenses.

**Tech Stack:** Starlette + Jinja2 (existing), Muuri (vendored), `recall.search`,
stdlib `sqlite3`, `pytest` + httpx (ASGITransport), `uv`. No new deps.

## Global Constraints

- **One screen.** The only authenticated HTML page is the board. Search,
  Timeline, Topics, Reminders pages and routes are removed; nothing links to
  them. Note detail is folded into the board (a note's full text is already on
  its card; W5 does not add a separate detail page).
- **The raw note is sacred.** Unchanged: the board writes only layout, piles, and
  the explicit delete; search and filtering are read-only.
- **Auth + CSRF unchanged.** The board and its endpoints stay behind the
  deny-by-default guard; state-changing POSTs keep CSRF. The new search endpoint
  is a GET behind auth, like `/board/notes`.
- **Filters stack (AND).** Search ids ∩ tag/category match ∩ time range. An empty
  control does not narrow. The board never shows a note that fails any active
  filter.
- **Search stays server-side and offloaded.** The query embed runs via
  `asyncio.to_thread`, as elsewhere; no SQLite transaction spans the await.
- **Escaping.** Note text and labels remain data placed via textContent; search
  returns ids (+ optional ranks), never HTML.
- **No em-dashes in prose, comments, template copy, or commit messages.**

## Review Focus

- **Pages actually gone:** the removed routes 404 (not merely hidden); no template
  or nav references them; the app still builds and every remaining route works.
- **Filter AND-logic:** a note shown satisfies search, dropdown, and time range
  simultaneously; clearing one control widens correctly; the empty state appears
  when nothing matches.
- **Search endpoint:** `/board/search?q=` is behind auth, offloads the embed,
  returns ranked ids, empty/whitespace q returns empty, a malformed query does not
  500 (reuses the hardened recall path).
- **Reminders rail:** shows exactly the notes with a pending timer, counts down
  live, and clicking one focuses/scrolls to that note; it reflects adds/deletes.
- **No regression:** positions, piles, add, delete, and the auth/CSRF sweep still
  pass with the pages removed.

## Removed surface

- Routes: `/search`, `/search/results`, `/timeline`, `/topics`,
  `/topics/tag/{tag}`, `/topics/category/{cat}`, `/topics/cluster/{id}`,
  `/note/{id}`, `/reminders`, and the `/add` form POST (the board uses
  `/board/add`).
- Handlers: `search_page`, `search_fragment`, `add_note`, `timeline_page`,
  `topics_page`, `topic_tag`, `topic_category`, `topic_cluster`, `note_detail`,
  `reminders_page`.
- Templates: `search.html`, `_tiles.html`, `timeline.html`, `topics.html`,
  `topic_list.html`, `note.html`, `reminders.html`. `base.html` is now used by no
  page and is removed too (the board is standalone).
- `app.css` and `htmx.min.js` are no longer needed by any page and are removed.
  (Storage methods that only served those pages, e.g. `recent_notes`,
  `notes_by_tag`, `tag_counts`, are kept if the board uses them, else left in
  place as they are cheap and tested; removal of dead storage is out of scope.)

## New surface

- `GET /board/search?q=` -> `{ids: [...]}`, ranked by `recall.search`, behind
  auth, embed offloaded.
- Board controls: a search input in the top bar; a single "Topic" dropdown
  listing categories then tags; an "Order by time" control with newest/oldest and
  a from/to datetime range; a left reminders rail.

---

### Task 0: Remove the other pages

**Files:**
- Modify: `stash/web.py` (drop handlers + routes), delete templates listed above,
  delete `stash/static/app.css`, `stash/static/htmx.min.js`.
- Modify/delete tests: remove `tests/test_web_search.py`, `test_web_pages.py`,
  `test_web_clusters.py` (page-level), and the page rows from the authz sweep;
  keep storage-level tests (`test_storage_reads.py`) since those methods remain.

**Interfaces:**
- Produces: an app whose only authenticated HTML route is `/` (the board); the
  removed routes return 404.

- [ ] **Step 1:** Delete the handlers and their routes; delete the templates and
      the two unused static files; remove `base.html`.
- [ ] **Step 2:** Update the authz sweep: drop the removed GET/POST rows, keep
      the board rows, add that a removed route (e.g. `/timeline`) now 404s when
      authed (gone, not merely guarded).
- [ ] **Step 3:** App builds; board + board endpoints + auth/login/logout still
      work.

**Acceptance:** `GET /timeline`, `/topics`, `/reminders`, `/search`, `/note/1`
return 404 when authed; `/` is the board; the suite (minus deleted page tests)
passes.

---

### Task 1: Board search in the top bar

**Files:**
- Modify: `stash/web.py` (`/board/search`), `stash/static/board.js`,
  `stash/templates/board.html`, `stash/static/board.css`
- Test: `tests/test_board_search.py`

**Interfaces:**
- Produces: `GET /board/search?q=` returning `{ids}` from `recall.search`; a
  top-bar search input that, on input (debounced), fetches ids and adds "search"
  to the active filter set.

- [ ] **Step 1:** Handler: empty/whitespace q -> `{ids: []}` with a flag meaning
      "no search active" vs "searched, nothing matched" (e.g. return `{q, ids}`
      and let the client treat empty q as inactive). Offload the embed.
- [ ] **Step 2:** board.js keeps a `searchIds` set (null = inactive). On input,
      debounce, fetch, set the set, re-apply filters. Clearing the box sets it
      back to null (inactive).
- [ ] **Step 3:** `passes(n)` also requires `searchIds === null ||
      searchIds.has(n.id)`.

**Acceptance:** typing a query narrows the board to matches; clearing it restores
all; empty q is inactive (shows all), a no-match query shows the empty state; the
endpoint is behind auth and does not 500 on odd input.

---

### Task 2: Topic dropdown + time-range filter

**Files:**
- Modify: `stash/static/board.js`, `stash/templates/board.html`,
  `stash/static/board.css`
- Test: covered by board behavior; a small JS-logic note in the plan (filter
  intersection) plus the endpoint tests from Task 1.

**Interfaces:**
- Produces: a "Topic" `<select>` (categories, then tags, with counts) that sets
  the active tag/category; an "Order by time" control (newest/oldest) with a
  from/to `datetime-local` range that narrows by `created_at`.

- [ ] **Step 1:** Replace the chip row with a single dropdown (keeps the screen
      uncluttered); selecting an option sets `state.cat`/`state.tag`; "All"
      clears. Counts shown per option.
- [ ] **Step 2:** Time control: order newest/oldest (existing) plus two
      `datetime-local` inputs; `passes(n)` also checks `from <= created_at <= to`
      when either bound is set.
- [ ] **Step 3:** All filters AND together in `passes(n)`: search set, tag/cat,
      time range. Each empty control is a no-op.

**Acceptance:** picking a topic narrows the board; setting a from/to range narrows
by date; combined with search they intersect; clearing any one widens correctly.

---

### Task 3: Left reminders rail

**Files:**
- Modify: `stash/static/board.js`, `stash/static/board-piles.js` (layout offset),
  `stash/templates/board.html`, `stash/static/board.css`
- Test: `tests/test_board_search.py` (data) + browser-verified interaction.

**Interfaces:**
- Produces: a left rail listing notes with a pending timer, each a live countdown;
  clicking one scrolls to and briefly highlights that note on the board. The rail
  updates as notes are added/deleted and as timers pass.

- [ ] **Step 1:** Build the rail from `state.notes.filter(n => n.timer)`, sorted
      by soonest fire time, each showing the countdown (reusing `until`) and a
      short label from the note's first words.
- [ ] **Step 2:** Clicking a rail item finds the note's Muuri element, scrolls it
      into view in the board scroll area, and adds a brief highlight class.
- [ ] **Step 3:** The board canvas gets a left margin for the rail; the rail is
      hidden (or collapses to a strip) at phone width so the board stays usable.
- [ ] **Step 4:** The rail re-renders on reload and when the 30s tick updates
      countdowns; a fired/removed timer drops off.

**Acceptance:** the rail shows exactly the pending-timer notes with live
countdowns; clicking one focuses its card; adding a reminder note makes it appear,
deleting it removes it.

---

### Task 4: Docs + GATE

**Files:**
- Modify: `README.md`, `docs/guides/web-setup.md`, `tests/test_web_authz_sweep.py`
  (final route table).

- [ ] **Step 1:** Docs: the web UI is a single board screen; search is in the top
      bar, topic and time are filters, reminders are the left rail. Remove
      mentions of the separate pages.
- [ ] **Step 2:** Final authz sweep over the real route table (board + search +
      the POSTs + open routes); removed routes 404 when authed.

**Acceptance:** docs describe the one screen; the sweep passes; removed routes
404.

---

## VERIFY / GATE

- Suite green with the page tests removed and the board/search tests added; board
  tests use the fake embedder + ASGITransport.
- Independent review at the gate: pages truly gone (404, no refs), AND-filter
  correctness, search endpoint auth + offload + hardening, reminders rail
  correctness, and no regression in positions/piles/add/delete/auth.
- Branch: `feat/w5-single-screen` (off `feat/w4-board`). On GO, record in
  `docs/RECENTLY-SHIPPED.md`.

**Verification note:** no sqlite-vec or browser in the build environment, so
search (which needs the real recall path over vec_notes) and the live rail/filter
interactions are browser/user-verified; the route removal, search endpoint
auth/shape, and filter-intersection logic are verified in-session via
ASGITransport and fake storage, and the board JS passes node --check. Before
merge, run the suite with sqlite-vec installed.

## Explicitly not in W5

- Snooze/cancel of reminders from the rail (display + click-to-focus only).
- A separate note-detail page (the card holds the full note).
- Clustering/Topics as a page (clustering still runs via `stash recluster`; if
  discovered topics should appear in the dropdown, that is a small follow-up).

---
cam
