# stash W7 (Clickable tags + Settings & colors) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Clicking a `#tag` or `@category` on a note card re-filters the board
to it. (2) A Settings pane, opened by a gear icon top-right, where the user can
assign colors to tags and categories (applied to cards and the filter controls),
choose a theme (Light / Dark / System), and set the board's default sort. Settings
persist server-side.

**Architecture:** #1 is a client change: the tag/category spans on each card become
buttons that set the Category/Tag dropdown and re-filter, reusing the existing
filter path. #2 adds a single settings document stored in the existing `kv` table
(JSON under one key), read and written through two endpoints; the board loads it on
start and applies colors when rendering cards and filter chips, sets the theme via
`data-theme` on the root, and uses the default sort as the initial order. Prefer
existing packages over hand-rolled code: the color picker uses the native
`<input type="color">`; the theme uses the existing CSS token system
(`data-theme`); no new library is needed.

**Tech Stack:** Starlette + Jinja2, stdlib `sqlite3` (kv), native `<input
type="color">`, `pytest` + httpx (ASGITransport), `uv`. No new deps.

## Global Constraints

- **The raw note is sacred.** W7 reads notes and writes only the settings
  document; no note text is changed.
- **Settings are durable, derived from nothing.** Stored as one JSON value in
  `kv` (which a reindex already preserves). No migration needed.
- **Auth + CSRF unchanged.** Settings read is a GET behind the guard; save is a
  CSRF-protected POST.
- **Reuse, do not hand-roll.** Native color input; the existing `data-theme`
  token system for theming; the existing filter path for clickable tags. No new
  color-picker or theming library.
- **Colors are presentation only.** A tag/category with no assigned color renders
  exactly as today. Colors never change filtering or storage of notes.
- **Escaping.** Settings values (color hex strings, theme name, sort) are
  validated/normalized server-side; colors applied in the DOM are set as style
  properties from a validated hex, never by injecting markup.
- **No em-dashes in prose, comments, template copy, or commit messages.**

## Review Focus

- **Clickable tags:** clicking `#tag`/`@category` on a card sets the matching
  dropdown and filters; it does not trigger a drag; it works for a card inside a
  pile; clicking the card body still opens nothing destructive.
- **Settings endpoints:** GET behind auth; POST CSRF-checked before write; a
  malformed body is 400; colors are validated as `#rrggbb` (bad values rejected
  or dropped, never stored raw); theme is one of light/dark/system; sort is one
  of the known values.
- **Settings durability + round-trip:** saved settings survive a reload and a
  reindex (kv-backed); applying them colors the right tags/categories and sets
  the theme and default sort.
- **Colors are safe:** a color value is only ever used as a CSS color after
  passing a `#rrggbb` check; no value flows into HTML.
- **No regression:** board, filters, saved boards, reminders, auth/CSRF intact.

## New surface

- `kv` key `settings` holding JSON:
  `{ theme: "system"|"light"|"dark", defaultSort: "newest"|"oldest",
     colors: { "tag:billing": "#rrggbb", "cat:work": "#rrggbb", ... } }`.
- storage: `get_settings() -> dict`, `save_settings(dict) -> None` (validates and
  normalizes before storing).
- web: `GET /board/settings`, `POST /board/settings` (CSRF).
- board.html: a gear button top-right opening a Settings panel; card tag/category
  spans become buttons.
- board.js: clickable tags; load+apply settings (colors on cards/controls, theme
  on root, default sort); the settings panel UI (color pickers per tag/category,
  theme radios, default-sort select) saving on change.

---

### Task 0: Clickable tags/categories on cards

**Files:**
- Modify: `stash/static/board.js`, `stash/static/board.css`
- Test: board page + JS (node check); interaction browser-verified.

**Interfaces:**
- Produces: the `.t`/`.c` spans in `noteEl` become buttons that, on click, set
  `state.tag`/`state.cat` and the matching dropdown, then `applyFilter()`; the
  click does not start a drag (handled like the delete button in the drag
  predicate) and stops propagation.

- [ ] **Step 1:** In `noteEl`, render each tag and the category as a `<button>`
      (or a span with role/tabindex) carrying its value; on click set the filter
      and re-apply, matching the dropdown value so the control reflects it.
- [ ] **Step 2:** Exclude these from the drag start predicate (like `.del`), so a
      click filters rather than dragging the card.
- [ ] **Step 3:** Style them as subtle inline chips; keyboard focusable.

**Acceptance:** clicking a card's `#tag` filters the board to that tag and sets
the Tag dropdown; clicking its `@category` filters and sets the Category dropdown;
the card is still draggable from its body; no horizontal scroll introduced.

---

### Task 1: Settings storage + endpoints

**Files:**
- Modify: `stash/storage.py`, `stash/web.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `get_settings()`/`save_settings()` over the `kv` `settings` key;
  `GET /board/settings`, `POST /board/settings`.

- [ ] **Step 1:** `get_settings()` returns the parsed JSON (or defaults:
      `{theme:"system", defaultSort:"newest", colors:{}}`) and never raises on a
      corrupt value (falls back to defaults). `save_settings(d)` validates: theme
      in {system,light,dark}; defaultSort in {newest,oldest}; colors is a map of
      string keys to `#rrggbb` values (drop any entry whose value fails the hex
      check), then stores JSON.
- [ ] **Step 2:** `GET /board/settings` returns the settings behind auth;
      `POST /board/settings` CSRF-checks, requires a dict body (400 otherwise),
      validates via `save_settings`, returns the normalized settings.
- [ ] **Step 3:** A bad color (`red`, `#xyz`, injection attempt) is dropped, not
      stored; the endpoint still returns 200 with the cleaned settings.

**Acceptance:** save settings, GET returns them; a corrupt stored value yields
defaults, not a 500; a non-hex color is dropped; bad CSRF 403, malformed body 400;
theme/sort out of range are rejected or normalized.

---

### Task 2: Settings pane + applying settings (gear, colors, theme, sort)

**Files:**
- Modify: `stash/static/board.js`, `stash/static/board.css`,
  `stash/templates/board.html`
- Test: board page assertions + `tests/test_settings.py` round-trip.

**Interfaces:**
- Produces: a gear button top-right opening a settings panel with: a theme control
  (Light / Dark / System), a default-sort select, and a list of the user's tags
  and categories each with a native color input; changes save to
  `POST /board/settings` and apply immediately.

- [ ] **Step 1:** On load, `GET /board/settings`; apply theme by setting
      `document.documentElement.dataset.theme` (or removing it for system), set
      the initial `state.order` from defaultSort, and apply colors.
- [ ] **Step 2:** Apply colors when building cards: a tag/category with a color
      gets that color on its chip (text or left-border), validated `#rrggbb` only.
      Re-apply on settings change without a full reload where possible.
- [ ] **Step 3:** The panel: gear button toggles it (closes on Escape/outside
      click, like the reminders panel); theme radios; default-sort select; a row
      per tag and per category (from the current notes) with an `<input
      type="color">`; changing any control debounces a save and applies live.
- [ ] **Step 4:** Reuse, do not hand-roll: native color input, existing
      `data-theme` tokens, the existing panel open/close pattern.

**Acceptance:** the gear opens settings; choosing Dark sets the theme immediately
and persists; setting a default sort changes the initial order on next load;
assigning a color to a tag tints that tag on cards and the control, persists, and
a bad value never reaches the DOM; the panel closes on Escape/outside click.

---

### Task 3: Docs + sweep + GATE

**Files:**
- Modify: `tests/test_web_authz_sweep.py` (settings routes), `README.md`,
  `docs/guides/web-setup.md`.

- [ ] **Step 1:** Add `/board/settings` (GET + POST) to the sweep.
- [ ] **Step 2:** Docs: clickable tags, the gear Settings pane (colors, theme,
      default sort).

**Acceptance:** sweep passes; docs describe clickable tags and settings.

---

## VERIFY / GATE

- Suite green; settings tests use fake storage + ASGITransport. The live
  clickable-tags, color application, theme switch, and panel are user-verified.
- Independent review at the gate: settings auth/CSRF/validation (especially the
  color hex check and no-HTML-injection), durability (kv-backed, reindex-safe),
  clickable-tags not breaking drag, and no regression.
- Branch: `feat/w7-tags-settings` (off `feat/w6-saved-boards`). On GO, record in
  `docs/RECENTLY-SHIPPED.md`.

**Verification note:** no sqlite-vec or browser here, so settings endpoints,
validation, and the kv round-trip are verified in-session via ASGITransport and
fake storage (board JS passes node --check); the live tag-click, colors, theme,
and panel are user-verified. Run the suite with sqlite-vec before merge.

## Explicitly not in W7

- Per-note color overrides (colors are per tag/category only).
- The wider settings backlog (density, default filters, reminder lead time,
  parsing rules, export/backup from the UI, password change) - a later slice; this
  slice ships colors + theme + default sort.

---
cam
