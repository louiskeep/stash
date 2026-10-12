# stash: one-container install. Runs `stash serve` (web UI + Telegram poller +
# reminder scheduler) over one SQLite database.
#
# Config comes from the environment at run time, never baked in. The embedding
# model (~90MB) downloads on first run into the cache volume and is reused after.
FROM python:3.12-slim

# uv for dependency install and the console-script entry point.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Model and data live on volumes, not in image layers.
ENV HF_HOME=/cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/cache/sentence-transformers \
    STASH_DB_PATH=/data/stash.db \
    STASH_WEB_BIND=0.0.0.0 \
    STASH_WEB_PORT=8000 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (cached layer), then the project. The lock is not
# pinned with --frozen here so the build still succeeds if uv.lock is slightly
# behind pyproject.toml; commit a fresh `uv lock` and add --frozen for fully
# reproducible builds.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project
COPY . .
RUN uv sync --no-dev

# The database and the model cache persist across runs.
VOLUME ["/data", "/cache"]

EXPOSE 8000

# STASH_WEB_BIND defaults to 0.0.0.0 here so the published port is reachable from
# the host. That is NOT safe on an open network without TLS: front it with a TLS
# reverse proxy, or publish only to 127.0.0.1 on the host (-p 127.0.0.1:8000:8000).
# See docs/guides/docker.md and docs/guides/web-setup.md.
CMD ["uv", "run", "--no-dev", "stash", "serve"]
