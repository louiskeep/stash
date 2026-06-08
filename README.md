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
Telegram in -> deterministic parse -> local embedding -> Postgres (pgvector + tsvector)
-> hybrid retrieval (RRF) -> candidate tiles with sources. Plus graveyard import and a
precision@3 eval. Quantitative path, LLM router, and reminders are deferred.

## Architecture
```
TELEGRAM -> INGEST (raw immutable) -> PARSE (code) -> EMBED (local) -> POSTGRES
                                                                          ^
Recall: ?? -> parse + expand -> tsvector + pgvector -> RRF -> top-k tiles + source
```

## Dev
```
uv sync
uv run pytest
```

Plan + reviews: ~/.gstack/projects/council/ (CEO, eng, design reviews + task lists).
