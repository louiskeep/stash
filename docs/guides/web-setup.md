# Web UI Setup

The web UI is a local page for searching, browsing, and reviewing your notes and
reminders. It runs in the same process as the Telegram poller and the reminder
scheduler, over the one SQLite database, and it binds to localhost by default.

## Prerequisites

- A `.env` file in your stash repo directory (copy from `.env.example`).
- The dependencies installed: `uv sync`.

## Step 1: Set a password and a session secret

The web UI stays off until both of these are set. Edit `.env`:

```bash
STASH_WEB_PASSWORD=choose-a-password
STASH_WEB_SESSION_SECRET=paste-a-long-random-string
```

Generate a session secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The session secret signs your login cookie. Keep it stable (changing it signs
you out) and keep it out of version control. The password is checked in constant
time; there is one password for the one user.

## Step 2: Run it

```bash
python -m stash serve
```

This starts the web server alongside the Telegram poller and reminder scheduler.
If you have not configured Telegram, that part simply does nothing; the web UI
still runs. Open:

```
http://127.0.0.1:8000
```

Log in with your password. You get:

- **Search** - hybrid keyword + meaning search, with a box to stash a new note.
- **Timeline** - everything you have stashed, newest first.
- **Topics** - your `#tags` and `@categories`, each drilling into its notes.
- **Note** - a note's verbatim text, its tags and category, any reminder, and
  related notes found by meaning.
- **Reminders** - what is pending, sent, or failed.

The web UI never edits or deletes a note's text; the raw note is kept exactly as
you sent it.

## Changing the port or address

```bash
STASH_WEB_BIND=127.0.0.1   # this machine only (default)
STASH_WEB_PORT=8000
```

## Exposing it beyond localhost

Binding to `0.0.0.0` makes the UI reachable from your network, so your phone can
submit notes directly. The bind address alone does **not** make this safe:

- Put it behind TLS (a reverse proxy such as Caddy or nginx with a certificate).
  The login cookie is only marked `Secure` over HTTPS. When TLS is terminated at
  a proxy, stash sees plain HTTP and will not set `Secure` unless it is told to
  trust the proxy's `X-Forwarded-Proto`. Run the app so uvicorn honors forwarded
  headers only from your proxy's address, e.g. with
  `--forwarded-allow-ips=127.0.0.1` (never trust forwarded headers from an open
  bind). Until then, assume the cookie is not `Secure` behind a proxy.
- Keep `STASH_WEB_SESSION_SECRET` set and secret.
- The cookie is already `HttpOnly` and `SameSite=Lax`, and every form submission
  carries a CSRF token.

Without TLS, treat `0.0.0.0` as exposing your password and your notes in clear
text over the network. Prefer localhost plus an SSH tunnel or a private VPN if
you are unsure.

## Backups

Back up the SQLite database with a consistent online backup while the process is
running, not a raw file copy of a live WAL database:

```bash
uv run stash backup stash-backup.db
```

This runs `VACUUM INTO` under the hood: a coherent, single-file snapshot that is
safe to take while `stash serve` is running, and refuses to overwrite an existing
file unless you pass `--force`. (The older `sqlite3 stash.db "VACUUM INTO ..."`
does the same thing by hand.)

## Running in a container

For a one-container install, see `docs/guides/docker.md`.
