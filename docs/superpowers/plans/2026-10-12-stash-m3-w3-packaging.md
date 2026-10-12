# stash Milestone 3 / W3 (Packaging) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make stash installable and runnable as one command and one container,
and give it a safe online backup. After W3: `uv run stash ...` works (not only
`python -m stash`), `docker build` + `docker run` gives a one-container install,
`stash backup <path>` writes a consistent copy of the live database, and the run
docs cover install, run, and backup end to end.

**Architecture:** No new runtime subsystem. W3 turns the flat module into an
installed package with a console-script entry point, adds a `backup` CLI
subcommand that wraps SQLite's online backup (`VACUUM INTO`), and ships a
Dockerfile that runs `stash serve`. The embedding model is downloaded on first
run (spec 9) and cached in a volume so a container restart does not refetch it.

**Tech Stack:** Python >=3.10, `uv` (build + run), a PEP 621 build backend
(hatchling), stdlib `sqlite3` for the backup, Docker. No new application deps.

**Spec:** `docs/superpowers/specs/2026-09-24-stash-design.md` (v2), section 9
(Packaging and run), section 6.11 (App composition).

## Global Constraints

- **One command, one database file, one process** (spec principle 5 / 6.11).
  The entry point must start exactly what `python -m stash` starts today; the
  two run paths stay behaviorally identical.
- **No behavior change to the core.** W3 is packaging and ops only. The capture,
  recall, reminder, Telegram, and web code are not touched except where the
  entry point and the new `backup` subcommand require.
- **The backup is consistent, not a raw copy.** Backup uses `VACUUM INTO` (or
  the sqlite3 backup API) against the live connection, never a filesystem copy
  of a WAL database (spec 9). It is safe to run while `stash serve` is running.
- **Secrets never baked into the image.** The Dockerfile copies no `.env` and no
  database; config comes from the environment / a mounted `.env` at run time.
  `.dockerignore` excludes `.env`, `*.db`, `.venv`, caches.
- **Model cache is a volume, not a layer.** The ~90MB model is fetched on first
  run into a cache directory that is a Docker volume, so it survives restarts and
  is not committed to the image.
- **Localhost by default carries into the container honestly.** The container
  binds `STASH_WEB_BIND` (default 127.0.0.1 inside the container); the run docs
  state that reaching it from the host means binding appropriately and publishing
  the port, and that exposing it needs TLS (carried from W1's web-setup guide).
- **No em-dashes in prose, comments, or docs.**

## Review Focus

- **Entry-point parity:** `uv run stash serve` and `python -m stash serve` start
  the same process (same crash-repair, same three loops). A Task 1 test asserts
  the console script resolves to `stash.cli:main`.
- **Backup correctness:** `stash backup <dest>` produces a file that opens as a
  valid stash database with the same notes and reminders, while the source
  remains usable; it refuses to overwrite an existing file unless forced. Task 2
  tests.
- **Backup does not corrupt live state:** backup holds no long write lock and
  does not touch `reminders`, `kv`, or `derived_at`. Task 2 test runs a backup
  with notes present and asserts source rows are unchanged.
- **Image builds and runs the entry point:** the Dockerfile builds, and the
  default command is `stash serve`. (Build/run is validated by the author in an
  environment with a Docker daemon and network; see the verification note.)
- **No secret or database in the image:** `.dockerignore` excludes `.env` and
  `*.db`; a Task 3 check greps the build context rules.

## New CLI surface

- `stash backup DEST [--force]`: write a consistent copy of the configured
  database to `DEST` using `VACUUM INTO`. Refuses when `DEST` exists unless
  `--force`. Prints the destination path on success.

---

### Task 0: Build backend and console-script entry point

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Produces: an installable `stash` package with
  `[project.scripts] stash = "stash.cli:main"`, built by hatchling; `uv run
  stash` resolves to the CLI.

- [ ] **Step 1:** Add `[build-system]` (hatchling) and `[project.scripts]`
      `stash = "stash.cli:main"`. Remove `package = false`; configure hatch to
      package the `stash/` directory including `templates/` and `static/` as
      package data (so the installed app can find its templates).
- [ ] **Step 2:** Keep `[tool.pytest.ini_options] pythonpath = ["."]` so the
      test suite still imports the source tree in place; confirm tests are not
      broken by the packaging change.
- [ ] **Step 3:** `tests/test_packaging.py`: assert the entry point is declared
      and imports (`from stash.cli import main; assert callable(main)`), and that
      the packaged template/static directories exist on disk relative to
      `stash.web`.

**Acceptance:** `[project.scripts]` names `stash.cli:main`; `uv run stash --help`
exits cleanly (author-run where uv can build); templates and static are declared
as package data so a wheel install can serve them.

---

### Task 1: `stash backup` subcommand

**Files:**
- Create: `stash/backup.py`
- Modify: `stash/cli.py`
- Test: `tests/test_backup.py`

**Interfaces:**
- Produces: `backup(storage, dest, force=False) -> str` that runs
  `VACUUM INTO ?` against the live connection and returns `dest`; a `backup`
  subparser (`DEST`, `--force`).
- Consumes: the open `Storage` connection.

- [ ] **Step 1:** `stash/backup.py`: `backup(storage, dest, force=False)`. If
      `dest` exists and not `force`, raise a clear error. Otherwise
      `storage.conn.execute("VACUUM INTO ?", (dest,))`. `VACUUM INTO` writes a
      fully consistent, defragmented copy with no long lock on the source and no
      WAL side files, which is the spec-9 backup.
- [ ] **Step 2:** Wire `backup` into `cli.py`'s subparsers (`DEST` positional,
      `--force` flag); print the destination on success; map the "exists" error
      to a nonzero exit with a readable message.
- [ ] **Step 3:** `tests/test_backup.py`: seed notes + a reminder; back up to a
      temp path; open the copy as a `Storage` and assert the notes and the
      reminder are present and equal; assert the source rows are unchanged;
      assert a second backup to the same path without `--force` raises and with
      `--force` succeeds.

**Acceptance:** the backup opens as a valid stash DB with identical notes and
reminders; the source is untouched and still usable; overwrite is refused without
`--force`.

---

### Task 2: Dockerfile + .dockerignore

**Files:**
- Create: `Dockerfile`, `.dockerignore`
- Modify: `docs/guides/web-setup.md` or a new `docs/guides/docker.md`

**Interfaces:**
- Produces: an image that installs stash and its deps and runs `stash serve` by
  default, reading config from the environment, caching the model in a volume.

- [ ] **Step 1:** `Dockerfile`: a slim Python base, install `uv`, copy the
      project, `uv sync --no-dev` (or build + install the wheel), set
      `HF_HOME`/`SENTENCE_TRANSFORMERS_HOME` to a cache path declared as a
      `VOLUME`, declare the DB path as a volume, `EXPOSE` the web port, and
      `CMD ["stash", "serve"]`.
- [ ] **Step 2:** `.dockerignore`: exclude `.env`, `*.db`, `*.sqlite`, `.venv`,
      `__pycache__`, `.pytest_cache`, `.git`, so no secret or local database
      enters the build context.
- [ ] **Step 3:** `docs/guides/docker.md`: build, run with an env file and
      volumes for the DB and model cache, publish the port, and the localhost/TLS
      note carried from web-setup. State that first run downloads the model.

**Acceptance (author-run, needs a Docker daemon + network):** `docker build`
succeeds; `docker run` with the web env set starts `stash serve` and serves the
login page; the model cache and DB persist across `docker run` invocations via
their volumes. In an environment without Docker/network, the Dockerfile and
`.dockerignore` are reviewed statically and the exclusion rules are asserted.

---

### Task 3: Run docs consolidation

**Files:**
- Modify: `README.md`, `docs/guides/web-setup.md`

**Interfaces:**
- Produces: a single clear path from clone to running, covering `uv run stash`,
  `stash serve`, `stash backup`, and Docker.

- [ ] **Step 1:** README Run section: show `uv run stash add/search/serve/backup`
      now that the entry point exists (keep `python -m stash` noted as the
      no-install equivalent).
- [ ] **Step 2:** Cross-link the backup command and the Docker guide from the
      web-setup guide so the ops story is in one reading path.

**Acceptance:** a reader can go clone -> `uv sync` -> `uv run stash serve` ->
open the UI -> `uv run stash backup backup.db`, or build and run the container,
from the docs alone.

---

## VERIFY / GATE

- The suite stays green, including the new `test_packaging.py` and
  `test_backup.py`. The backup test uses the real SQLite connection (needs
  sqlite-vec, as the rest of the storage suite does).
- Independent review at the gate (R2), focused on: entry-point parity, backup
  consistency and non-corruption, and no secret/DB in the image.
- Branch: `feat/m3-w3-packaging`. On GO, move the W3 entry to
  `docs/RECENTLY-SHIPPED.md` with gate evidence and tick W3 in `ROADMAP.md`.

**Verification note:** this environment has no PyPI/Docker access, so `uv run`
and `docker build` cannot execute here. The backup logic and the entry-point
declaration are verified in-session against stdlib sqlite3 and the parsed
`pyproject.toml`; the `uv run` and `docker` acceptance steps are run in an
environment with the toolchain before merge, as noted on the W1 slice.

## Explicitly not in W3

- HDBSCAN clustering (that is W2).
- Any change to capture, recall, reminders, Telegram, or the web routes beyond
  the entry point and the `backup` subcommand.
- Publishing the image to a registry, or CI. W3 ships the Dockerfile, not a
  release pipeline.

---
cam
