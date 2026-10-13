# stash: recently shipped

Dated log of merged/merge-ready slices with gate evidence. The roadmap points
here instead of restating history.

## 2026-10-12: W8: Grid + merge board, warm light/dark refresh

Branch `feat/w8-design-refresh` (off `feat/w7-tags-settings`). A deliberate shift
from the austere free-drag canvas toward a friendlier tool, approved by the user
against an interactive light/dark preview before any app code changed.

What shipped:
- **Interaction model.** The free-drag Muuri canvas and offset "piles" are gone.
  Notes lay out in an ordered CSS grid (Newest/Oldest). Dragging one card onto
  another's top/bottom half **merges** them into one card (dragged text above or
  below the target); clicking the dashed divider **snips** a merged card apart.
  A merge is a reversible grouping -- each raw note stays its own row, never
  rewritten or deleted (the sacred-raw invariant, upheld end to end).
- **Look.** Warm paper light/dark palette (warm charcoal `#1b1a18`, not
  blue-black), friendlier blue-violet accent, amber reminders. Note text is now a
  readable sans, not monospace. Whole-card color by category (user colors from
  Settings, else a stable auto palette) with a colored left edge, so the board
  reads by zone. Login page reskinned to match. No third-party JS at all (Muuri
  and `board-piles.js` removed).
- **Model.** Migration 4 adds `board_layout.seq` (order within a merged group)
  and retires old piles (cleared on upgrade -- a loose stack has no faithful
  ordered-merge mapping). `save_groups(groups)` replaces `save_positions` and
  `save_piles`; it rebuilds `board_piles` + `board_layout.pile_id/seq` in one
  transaction, groups are disjoint, `<2`-member groups dropped. `POST /board/groups`
  replaces `/board/positions` and `/board/piles`; `board_notes` carries
  `pile_id` + `seq`. Merge grouping is durable (reindex-safe); delete still cascades.

Gate evidence:
- 5 tasks tracked; plan carried in this entry and the W8 preview artifact.
- Independent cold review: no HIGH/Critical. Confirmed merge/snip never touch the
  `notes` table and `delete_note` is the only destructive path (CSRF-guarded;
  two-step confirm for merged cards). One MEDIUM fixed: migration 4's `ALTER` was
  not idempotent, breaking the repo's crash-recovery contract -- the runner now
  tolerates a duplicate-column re-run, and migration 4 orders its idempotent
  resets before the `ALTER`. Two LOWs fixed: old piles are now explicitly cleared
  on upgrade (no silent arbitrary-order conversion), and `save_groups` enforces
  cross-group disjointness. XSS clean: note text via `textContent` only.
- Auth/CSRF on `/board/groups`; malformed/non-list body -> 400; merge order and
  snip round-trip through storage and the endpoint; reindex preserves groups;
  raw notes byte-for-byte unchanged by a merge (test).

Verification note: no sqlite-vec or browser in the build environment. The route
table, AuthGuard, CSRF, the `/board/notes` shape, and the full merge/snip contract
are verified in-session by driving the real app through a minimal ASGI harness
with fake storage; migration 4 (incl. old-pile clearing and re-run tolerance) and
the client card/merge/snip algorithms are verified with standalone SQLite and
`node` harnesses (board JS also passes `node --check`). The live drag-to-merge,
snip, theming, and card colors are user-verified on Windows. Run the suite with
sqlite-vec before merge.

Known limitation: drag-to-merge uses HTML5 drag-and-drop, which does not fire on
touch screens; merging is a desktop gesture for now.

Not in W8 (by design): touch-drag merging; per-note color overrides; live
re-fetch of notes added elsewhere while the board is open (still reloads on
add/delete only, to avoid clobbering an unsaved local merge).

## 2026-10-12: W7: Clickable tags + Settings & colors

Branch `feat/w7-tags-settings` (off `feat/w6-saved-boards`). (1) Clicking a `#tag`
or `@category` on a note card filters the board to it (reusing the filter path;
the chips are excluded from dragging). (2) A gear Settings pane (top right) with a
theme control (System / Light / Dark, applied via the existing `data-theme`
tokens), a default sort, and a native color picker per tag and category that tints
that tag on cards. Settings persist server-side.

What shipped: settings stored as one JSON document in the existing `kv` table
(reindex-safe, no migration), validated by `_normalize_settings` (theme/sort
allowlists; colors kept only if they match `#rrggbb` and a `tag:`/`cat:` key);
`get_settings`/`save_settings`; GET /board/settings (auth) and POST (CSRF). Client:
clickable chips, the settings module (load/apply theme+colors+sort, the panel,
save on change), and the chip color hook.

Gate evidence:
- 4 tasks, plan on disk at
  `docs/superpowers/plans/2026-10-12-stash-w7-clickable-tags-settings.md`,
  acceptance tests per task (R2).
- Independent cold review: GO, 0 Critical. The color-to-DOM path (the main XSS
  risk) was traced end to end and cannot inject: every color passes a #rrggbb
  gate server-side (and again client-side) and only ever reaches el.style, never
  innerHTML; the server re-validates on POST so a tampered client payload can't
  persist a bad color either. One HIGH fixed: clicking a tag chip on a pile's top
  card also toggled the pile (the piles capture-phase handler missed the .chipbtn
  exclusion). One MEDIUM fixed: a settings-load failure could blank the board
  (settings now never gate the notes load).
- Auth/CSRF on both routes; settings durable (kv, reindex-safe) and
  corruption-tolerant (defaults, no 500); theme/sort/colors round-trip.

Verification note: no sqlite-vec or browser in the build environment, so the
settings endpoints, normalization/validation, and kv round-trip are verified
in-session via ASGITransport and fake storage (board JS passes node --check); the
live tag-click, color application, theme switch, and settings panel are
user-verified. Run the suite with sqlite-vec before merge.

Not in W7 (by design): per-note color overrides; the wider settings backlog
(density, default filters, reminder lead time, parsing rules, export/backup,
password change).

## 2026-10-12: W6: Saved Boards + UI refinements

Branch `feat/w6-saved-boards` (off `feat/w5-single-screen`). (1) Split the Topic
dropdown into independent Category and Tag selects. (2) The board scrolls
vertically only, with a thin subtle scrollbar; saved card positions clamp to the
visible width. (3) Saved boards: a named filter set, saved from the current
filters, listed down the left and re-applied on click (filters only, one shared
canvas). The active-timers rail is replaced by a top-bar Reminders button that
opens a countdown dropdown with click-to-focus. (4) Date-only range filtering
(the To date includes its whole day).

What shipped: migration 3 (`boards` table, durable, reindex-safe); storage
list/save/delete_board; GET /board/boards + CSRF-protected save/delete; the
Category/Tag selects, date-only parse, x-clamped vertical-only layout, the
saved-boards column, and the reminders dropdown.

Gate evidence:
- 4 tasks, plan on disk at
  `docs/superpowers/plans/2026-10-12-stash-w6-saved-boards.md`, acceptance tests
  per task before implementation (R2).
- Independent cold review: 0 Critical (no XSS - board names and reminders render
  via textContent; auth/CSRF on all three new routes with the check before any
  write; config validated as an object before storing and parsed defensively on
  read; reindex leaves boards untouched). One HIGH fixed: a stale
  test_board_page.py asserted the old topic/rail markup (repointed). Two MEDIUM
  fixed: the save-board input could double-submit (reentrancy guard added) and
  the active-board highlight detached on input not change (now both).
- Category + Tag AND independently and self-heal their selection after a delete;
  the date range is inclusive of the To day; no horizontal scroll at any width.

Verification note: no sqlite-vec or browser in the build environment, so the
boards endpoints, migration, reindex-safety, and the filter/date logic are
verified in-session via ASGITransport and fake storage (board JS passes
node --check); the live filtering, saved-board save/apply/delete, scroll, and
reminders dropdown are user-verified in a browser. Run the suite with sqlite-vec
before merge.

Not in W6 (by design): per-board canvas layouts (filters only); drag-reorder of
saved boards; snooze/cancel reminders.

## 2026-10-12: W5: Single Screen

Branch `feat/w5-single-screen` (off `feat/w4-board`). The whole web UI is now one
screen, the board. The separate Search, Timeline, Topics, and Reminders pages are
removed; each is a control on the board: search in the top bar, a Topic dropdown
(categories and tags), an Order select with a From/To datetime range, and active
reminders as a left rail with live countdowns you click to jump to a note. All
filters stack (AND): search text, the selected topic, and the time range narrow
the same board together.

What shipped: removal of the page routes, handlers, and templates (and base.html,
app.css, htmx.min.js, now unused), with login.html made standalone; a new
GET /board/search?q= over hybrid recall (embed offloaded, returns ranked ids the
board intersects with its filters); board.js filter state and AND-logic, the
Topic dropdown, the From/To time filter, and the reminders rail with
click-to-focus; board.css control and rail styles.

Gate evidence:
- 5 tasks, plan on disk at
  `docs/superpowers/plans/2026-10-12-stash-w5-single-screen.md`, acceptance tests
  per task before implementation (R2).
- Independent cold review: 0 Critical (no auth bypass, no XSS, no content leak;
  the removed routes verifiably 404 when authed, not merely guarded; search
  returns only ids; rail and dropdown use textContent). Two HIGH fixed, both
  stale tests left red by the page removal: test_web_csrf.py posted to the gone
  /add route (repointed to /board/add) and test_packaging.py asserted deleted
  assets (repointed to the surviving ones). One MEDIUM fixed: the Topic dropdown
  could desync from the filter state after a delete (buildTopics now clears
  state.tag/cat when the selected option disappears). Two LOW cleanups.
- The board reads the same storage and recall the old pages did, so the controls
  cannot drift from the real notes.

Verification note: no sqlite-vec or browser in the build environment, so search
(over vec_notes) and the live filter/rail interactions are user-verified in a
browser; the route removal, search endpoint auth/shape, filter AND-logic,
packaging-asset, and login-standalone changes were verified in-session via
ASGITransport and fake storage, and both board JS files pass node --check. Before
merge, run the suite with sqlite-vec installed.

Not in W5 (by design): snooze/cancel of reminders from the rail (display +
click-to-focus only); a separate note-detail page (the card holds the full note);
discovered topics are not yet a dropdown entry of their own (a small follow-up).

## 2026-10-12: W4: The Board (home surface)

Branch `feat/w4-board` (off `feat/m3-w2-clustering`). The web UI's home is now a
canvas of the user's notes as draggable paper cards: arrange them freely (the
layout persists server-side, so the board is the same on any device), drop one
card onto another to stack them into a labeled pile, filter by time order / a tag
or category / active timers, and add a note with + or a double-click. Search,
Timeline, Topics, and Reminders remain in the nav. The raw note stays sacred: the
board writes only layout and pile state, plus the one explicit delete the user
initiates.

What shipped: migration 2 (board_layout, board_piles) as durable state (reindex
leaves it; a deleted note cascades its layout, a deleted pile detaches members);
storage board_notes / save_positions / save_piles / delete_note; a JSON notes
endpoint and CSRF-protected positions/piles/add/delete routes; the canvas
(board.html/css/js) with a custom Muuri layout that restores saved positions, and
a stacking-piles module; /board as the home and post-login landing, with Search
moved to /search and its fragment to /search/results.

Gate evidence:
- 6 tasks, plan on disk at
  `docs/superpowers/plans/2026-10-12-stash-m3-w4-board.md`, acceptance tests per
  task before implementation (R2).
- Independent cold review: 0 Critical (no auth bypass, no CSRF gap, no XSS sink,
  SQL sound; CSRF genuinely session-bound; delete behind auth+CSRF with a correct
  fts/vec cleanup; save_piles atomic). Two HIGH root-fixed: saved positions were
  never re-applied on load (the arrangement did not actually persist) - fixed
  with a custom Muuri layout that restores x/y/z; and a position save nulled
  pile_id on every drag - fixed by making save_positions touch only x/y/z and
  giving the piles module the single release handler. Two MEDIUM fixed: Muuri was
  loaded from a CDN at runtime (offline break + third-party-script-in-session
  risk) - now vendored at /static/muuri.min.js only; and added bad-CSRF 403 tests
  for the destructive /board/delete and the other POSTs.
- The board reads the same storage the text pages do, so it cannot drift from the
  real notes.

Verification note: no sqlite-vec or browser in the build environment, so
storage-backed reads use a fake storage in tests and the live drag / drop-to-stack
/ persistence round-trip is confirmed by the user in a browser. The route / auth /
CSRF / JSON-shape / escaping / pile-preservation logic was verified in-session via
ASGITransport and fake storages; both board JS files pass node --check. Before
merge, run the pytest suite with sqlite-vec installed and vendor Muuri (one curl,
see static/README.md).

Not in W4 (by design): named/multiple boards, real-time multi-viewer sync, and
any change to capture, recall, reminders, Telegram, or clustering beyond the
board's read/layout endpoints.

## 2026-10-12: Milestone 3 / W2: Clustering (completes the MVP)

Branch `feat/m3-w2-clustering` (off `feat/m3-w3-packaging`). Topic discovery:
`recluster()` runs HDBSCAN over the note embeddings, groups semantically similar
notes, labels each from its own TF-IDF terms or a shared tag, and leaves notes
that do not fit a dense group as outliers. The Topics page shows a "Discovered
topics" section when clusters exist. With W2 merged, all three Milestone 3 slices
are done and the v1 MVP is complete.

What shipped: an opt-in `cluster` extra (scikit-learn + numpy) so the base
install stays light while the feature is fully built; `stash/cluster.py` with the
import probe, `recluster()`, and deterministic non-generative labelling;
derived-only storage writes (`replace_clusters` clears + inserts + assigns in one
transaction, touching only `clusters` and `note_meta.cluster_id`); a `stash
recluster` CLI with summary / small-corpus skip / install-hint paths; reindex
clearing stale clusters; and the Topics "Discovered topics" section +
`/topics/cluster/{id}` drill-down. Docs: a clustering guide plus README and
web-setup links.

Gate evidence:
- 5 tasks, plan on disk at
  `docs/superpowers/plans/2026-10-12-stash-m3-w2-clustering.md`, acceptance tests
  per task before implementation (R2).
- Independent cold review at the gate: GO, 0 Critical / 0 High. The reviewer ran
  the real HDBSCAN path (scikit-learn was available in the review environment)
  and confirmed derived-only isolation, transactional atomicity, idempotence of
  the grouping and the cluster ids, outlier handling (label -1 -> cluster_id
  NULL), the optional-dependency boundary, deterministic non-generative labels,
  and autoescaped output. Two LOW items fixed (a wrong docstring; a clearer
  zero-topics CLI message); `requires-python` kept at >=3.10 by choice.
- Spec section 10 clustering criteria met: stability on unchanged input,
  non-empty labels, outliers allowed.

Verification note: unlike the earlier slices, scikit-learn WAS present in the
build/review environment, so the real `recluster()` algorithm path was exercised
in-session (two-theme-plus-outlier corpora: correct clusters, labels, outliers,
idempotence, small-corpus skip, derived-only). sqlite-vec was still absent, so
the storage reads of `vec_notes` (via a plain-sqlite fake storage in tests) and
the full pytest suite should be run with sqlite-vec + the `cluster` extra
installed before merge.

Not in W2 (by design): auto-reclustering in the serve loop (manual `stash
recluster` only); per-cluster rename/merge; CLI overrides for the clustering
thresholds (possible follow-up); any change to capture, recall, reminders, or
Telegram.

## 2026-10-12: Milestone 3 / W3: Packaging

Branch `feat/m3-w3-packaging` (off `feat/m3-w1-web-ui`). Makes stash installable
and runnable as one command and one container, with a safe online backup.

What shipped: a hatchling build backend + `[project.scripts] stash =
"stash.cli:main"` so `uv run stash ...` works (not only `python -m stash`), with
the web templates/static force-included into the wheel; a `stash backup DEST
[--force]` subcommand using `VACUUM INTO` for a consistent single-file online
backup (safe while `stash serve` runs; dispatched before the startup
repair/reindex so it is a pure read); a Dockerfile (runs `stash serve` off the
installed venv, model cache and DB on volumes, no secret baked in), a
`.dockerignore` excluding `.env`/`*.db`/caches, and a docker guide; and
consolidated run docs.

As part of the review (H1 below), `serve()` was generalized to run with the web
UI alone, Telegram alone, or both (previously it hard-required Telegram); a
half-configured Telegram is still rejected, and a web-only start opens no
Telegram connection.

Gate evidence:
- 4 tasks, plan on disk at
  `docs/superpowers/plans/2026-10-12-stash-m3-w3-packaging.md`, acceptance tests
  per task before implementation (R2).
- Independent cold review at the gate. 0 Critical. One HIGH root-fixed: the
  documented one-container web install crashed on startup because `serve()`
  required Telegram config; fixed so at least one channel suffices and the
  Telegram client/poller/scheduler are built only when Telegram is configured
  (verified end-to-end: web-only serve runs, builds no Telegram client, stops on
  SIGINT). Three MEDIUM fixed: tomllib import guarded for the >=3.10 floor; the
  wheel-asset invariant pinned with an additive force-include and asserted; the
  container CMD switched from `uv run` (which re-resolves the lock, can need the
  network) to the installed console script on PATH.
- Reviewer confirmed correct: entry-point parity (`stash.cli:main` works as a
  console script and matches `python -m stash`), backup consistency and
  non-corruption (VACUUM INTO, source untouched, no repair/reindex side effect),
  and no secret/DB in the image.

Verification note: as with W1, this build environment had no PyPI/Docker access,
so `uv run`, `uv build`, and `docker build` could not run here. The backup logic
(against stdlib sqlite3), the entry-point and packaging declarations (against the
parsed pyproject), the serve validation and web-only startup, and the Docker
context rules were verified in-session. `uv run stash ...`, a wheel build, and
`docker build`/`run` should be exercised in an environment with the toolchain
before merge.

Not in W3 (by design): HDBSCAN clustering (W2); publishing the image to a
registry or CI; any core behavior change beyond the entry point, the `backup`
subcommand, and the web-only `serve()` generalization.

## 2026-10-12: Milestone 3 / W1: Web UI

Branch `feat/m3-w1-web-ui`. A local, server-rendered web UI over the existing
core, mounted into the `stash serve` asyncio loop (spec 6.11) so one process runs
the web server, the Telegram poller, and the reminder scheduler over one SQLite
DB. Read-only against stored notes; the only write path is capturing a new note.

What shipped: a Starlette + Jinja2 app with a minimal htmx-compatible shim for
live search (progressive enhancement; the search form degrades to a plain GET
with JS off); pages for Search (+ live `/search` fragment + `/add`), Timeline
(keyset paginated), Topics (deterministic tag/category + drilldown), Note detail
(raw + derived + related-by-embedding + reminder), and Reminders (read-only);
single-password signed sessions and session-bound CSRF via itsdangerous
(timing-safe compares, separate salts, a pre-session login CSRF token); a
deny-by-default auth guard; HttpOnly/SameSite/Secure-on-https cookies; eight
read-only storage queries; and the uvicorn server composed into the serve loop
with one stop signal shutting down all three loops (web skipped, logged, if its
config is unset). Docs: a web-setup runbook, the README's stale Postgres line
corrected to SQLite, `.env.example` web comments.

Gate evidence:
- 9 tasks, acceptance tests defined per task before implementation (R2); plan on
  disk at `docs/superpowers/plans/2026-10-12-stash-m3-w1-web-ui.md`.
- Independent cold review at the gate (a reviewer that had not seen the code
  being written) against the slice's Review Focus checklist. 0 Critical. Two
  HIGH findings, both root-fixed: `add_note` ran SQLite on a worker thread via
  `asyncio.to_thread(capture, ...)` (switched to `capture_async`, keeping the DB
  on the loop thread per the item-7 invariant); and the web tests drove the app
  with the sync TestClient's portal thread while the connection was opened on the
  test thread (rewritten to httpx `AsyncClient` over `ASGITransport`, the
  harness the plan specified). LOW items (login-CSRF as a shared constant, no
  login throttling, `Secure` behind a TLS proxy) acknowledged as single-user
  tradeoffs; the proxy case is documented in the web-setup guide.
- Reviewer confirmed correct by reading: deny-by-default guard (no prefix-match
  leak), session-bound CSRF, autoescaping on every surface, FTS/vec hardening +
  empty-query safety, timing-safe compares, serve composition, SQL of the new
  storage methods, and no transaction held across an await.

Verification note: this slice was built in an environment without PyPI access,
so `sqlite-vec`, the real encoder, and `pytest` could not be installed to run the
suite here. The security and routing logic that does not require the vec
extension was verified in-session through Starlette/httpx in-process transports
(auth guard, login flow, wrong-password 401, cookie flags, CSRF on all POSTs,
autoescaping, add reaching `capture_async` with source "web", static serving,
serve stop-event shutdown on an ephemeral port, all templates rendering). The
sqlite-vec-backed result assertions (search results, timeline/topics/detail
content, the new storage reads) run in the pytest suite and should be executed in
an environment with the deps installed before merge.

Not in W1 (by design): HDBSCAN clustering and ML auto-Topics (W2); the
`[project.scripts]` entry point, Dockerfile, and backup tooling (W3); editing or
deleting a note's raw text, or reminder actions, from the web.

## 2026-09-25: Milestone 2: Telegram adapter

Branch `feat/m2-telegram` (commits `f02747b..a14671f`). A `stash serve` asyncio
daemon that long-polls Telegram, captures plain-text messages, answers `/find`,
`/recent`, `/help`, and delivers reminders. Includes the async redesign of the
reminder scheduler (one-at-a-time claim, attempt-at-send, retry backoff,
lease-owned CAS completion) that was deferred from M1.

What shipped: async ports; the reminder scheduler redesign; an httpx Bot API
client (token/text never logged); ingest/delivery adapters with private-chat +
allow-list auth; command dispatch; the `stash serve` daemon (single-instance
flock, fail-fast config validation, `.env` loading, per-update ack, best-effort
receipts); and a setup runbook.

Gate evidence:
- 8 tasks, each spec+quality reviewed; fix rounds on the scheduler and the
  daemon; a whole-branch review that drove a 6-item fix wave.
- dennis GO (0 Critical/High/Medium, 4 Low). Codex final gate GO after three
  rounds that caught and root-fixed: a reminder-authorization leak (delivery to
  a since-removed sender), a raw-first durability regression (a failed encode
  must not lose the note), token-in-logs hardening, a long-poll timeout bug, and
  a spec/code ack reconciliation.
- Full suite: 100 tests pass, including the real-encoder recall gate.

Carry-forward follow-ups (both gates below the merge bar): graceful shutdown can
exceed the documented `max(25s, tick)` during a Telegram outage (systemd
`Restart` mitigates); reminders created via the CLI (no chat) are simply never
claimed by the daemon.

Not in M2 (by design): web UI + clustering (M3), SMS (future hosted tier),
multi-user. A live smoke test against a real bot is a manual step (needs your
bot token + user id).

## 2026-09-24: Milestone 1: core, usable via CLI

Branch `feat/m1-core` (commits `f55cea2..7965bb5`), merge-ready pending Cam's
merge call. Built with subagent-driven development: a fresh implementer and an
independent spec+quality reviewer per task, then a whole-branch review.

What shipped:
- Config loader with validation (`stash/config.py`).
- SQLite schema + numbered migrations, WAL + busy_timeout, sqlite-vec + FTS5
  (`stash/db.py`, `stash/migrations.py`).
- Storage layer: scoped dedupe, FTS/vec sync, kv, BM25 + vector search
  (`stash/storage.py`).
- Local embeddings: `all-MiniLM-L6-v2` + a deterministic fake for tests
  (`stash/embed.py`).
- Crash-safe capture pipeline with `repair()` (`stash/capture.py`).
- RRF hybrid recall (`stash/recall.py`).
- Real-encoder hit-rate@3 / MRR eval with a recorded, fail-closed regression
  gate (`stash/eval.py`, `stash/eval_baseline.json`).
- Lease-based at-least-once reminder scheduler (`stash/reminders.py`).
- Channel ports (IngestSource / Delivery), reindex that preserves durable state,
  and the `stash` CLI (`stash/ports.py`, `stash/reindex.py`, `stash/cli.py`).

Gate evidence:
- 8 tasks, each spec+quality reviewed by a fresh reviewer; fix loops on Tasks 1,
  2, 4 (migration idempotency, reindex regression test + narrowed dedupe catch,
  UTC `derived_at`).
- Whole-branch final review (most capable model): ready to merge with fixes, no
  Critical. Four should-fix items resolved before merge: FTS query crash on
  punctuation, missing spec §10 txn-across-send test, `STASH_EMBED_MODEL`
  ignored by the CLI, and these docs.
- Two-gate merge check: dennis GO (0 Critical/0 High); Codex gate ran three
  rounds. Round 1 (reindex crash-recovery, reminder lease ownership, CLI startup
  repair) and round 2 (ownership recheck before send, attempt-cap on claim,
  reindex on embed-model change) were remediated at the root with reproduce-first
  tests. Round 3 flagged a reminder-scheduler batch-claim/attempt-accounting bug
  that is only reachable under a concurrent `run_due` (a live scheduler loop),
  which M1 does not have. Per Cam's call, M1 ships now and that item is tracked
  as must-fix-before-M2 (see ROADMAP). The cheap config-validation fix
  (reject `max_attempts = 0`) landed here.
- Full suite: 58 tests pass, including the real-encoder eval.

Not in M1 (by design): live Telegram adapter, running poller/scheduler loop,
web UI, HDBSCAN clustering. Run command is `python -m stash` (a `uv run stash`
entry point is an M3 packaging follow-up).

Known limitation carried to M2: the reminder scheduler's concurrency (batch
claim + attempt accounting under a running loop) needs a redesign before the
live loop lands. It is safe in M1 because `run_due` is never invoked
concurrently here.

---
cam
