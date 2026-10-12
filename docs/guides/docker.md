# Running stash in Docker

A one-container install: the image runs `stash serve`, which is the web UI, the
Telegram poller, and the reminder scheduler over one SQLite database. Config
comes from an env file at run time; the database and the embedding-model cache
live on volumes so they survive restarts.

## Build

```bash
docker build -t stash .
```

The build installs stash and its dependencies. The embedding model is not baked
into the image; it downloads on first run (about 90MB) into the cache volume.

## Configure

Create an `.env` (see `.env.example`). At minimum, to use the web UI:

```bash
STASH_WEB_PASSWORD=choose-a-password
STASH_WEB_SESSION_SECRET=paste-a-long-random-string
```

Generate a secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

The image does not read your `.env` at build time, and `.dockerignore` keeps it
(and any local `*.db`) out of the build context. Pass it at run time instead.

## Run

```bash
docker run --rm \
  --env-file .env \
  -v stash-data:/data \
  -v stash-cache:/cache \
  -p 127.0.0.1:8000:8000 \
  stash
```

- `-v stash-data:/data` keeps the database (`/data/stash.db`) across runs.
- `-v stash-cache:/cache` keeps the downloaded model across runs, so only the
  first start pays the download.
- `-p 127.0.0.1:8000:8000` publishes the UI to your machine only. Open
  http://127.0.0.1:8000 and log in.

First start downloads the model, so it takes longer; later starts are quick.

## Exposing it beyond your machine

Inside the container stash binds `0.0.0.0` so the published port is reachable,
but the container boundary is not security. Reaching the UI from other machines
means publishing to a non-loopback address, and that is only safe behind TLS:

- Front it with a TLS reverse proxy (Caddy, nginx) and run with
  `--forwarded-allow-ips` so the `Secure` cookie flag is set (see
  `docs/guides/web-setup.md`).
- Otherwise publish only to `127.0.0.1` on the host, as above, and reach it over
  an SSH tunnel or a private VPN.

## Backups

Run a consistent backup against the running container's database:

```bash
docker exec <container> stash backup /data/stash-backup.db
```

The backup lands on the `/data` volume; copy it off with `docker cp` if you want
it on the host. See `docs/guides/web-setup.md` for the backup rationale.

## Telegram

To also capture from Telegram, set `STASH_TELEGRAM_BOT_TOKEN` and
`STASH_ALLOWED_SENDER_IDS` in the env file (see `docs/guides/telegram-setup.md`).
Leaving them unset runs the web UI alone.
