# stash

A second brain that isn't a chatbot. Text a thought, get it back later.
(Working name "stash"; the gstack project slug is still "council".)

## What it is
- **Wedge:** recall. The half-remembered thought you can finally find.
- **Edge:** a command surface, not a chat surface. The AI never makes you talk to it.
- **No generative AI in the default product.** council is a vectorized DB + deterministic
  code functions. stash uses a local embedding model to represent your notes for fuzzy search; it never
  rewrites or adds content. The raw note is sacred. LLM-API is an opt-in paid tier only.

## v0 scope (recall-only)
Telegram or web in -> deterministic parse -> local embedding -> SQLite
(sqlite-vec + FTS5) -> hybrid retrieval (RRF) -> candidate tiles with sources.
Plus reminders and a hit-rate@3 recall eval. ML clustering, the quantitative
path, and an LLM router are deferred.

## Architecture
```
TELEGRAM / WEB -> INGEST (raw immutable) -> PARSE (code) -> EMBED (local) -> SQLITE
                                                                               ^
Recall: query -> parse + expand -> FTS5 + sqlite-vec -> RRF -> top-k tiles + source
```
Everything is one process over one SQLite file: the Telegram poller, the reminder
scheduler, and the web server share it.

## Run
```
uv sync
uv run stash add "a thought to stash"      # capture from the CLI
uv run stash search "half-remembered"      # recall from the CLI
uv run stash serve                          # Telegram poller + reminders + web UI
uv run stash backup stash-backup.db         # consistent online backup
uv sync --extra cluster && uv run stash recluster   # discover topics (optional)
```
(`python -m stash ...` is the no-install equivalent of `uv run stash ...`.)

The web UI turns on once `STASH_WEB_PASSWORD` and `STASH_WEB_SESSION_SECRET` are
set; open http://127.0.0.1:8000. Guides: `docs/guides/web-setup.md`,
`docs/guides/telegram-setup.md`, `docs/guides/docker.md` (one-container install),
`docs/guides/clustering.md` (automatic topics).

## Dev
```
uv sync
uv run pytest
```

Plan + reviews: ~/.gstack/projects/council/ (CEO, eng, design reviews + task lists).
