# Static assets

- `app.css`, `htmx.min.js` - the text UI (Search, Timeline, Topics, Reminders).
- `board.css`, `board.js`, `board-piles.js` - the board (home) surface.
- `muuri.min.js` - the board's drag library (vendored; see below).

## Vendor Muuri (required for the board)

The board loads its drag library only from `/static/muuri.min.js`, never from a
CDN at runtime: stash does all processing and storage locally, and loading
third-party script into the authenticated session from a CDN would be both an
offline break and a security risk. So Muuri is vendored here.

Fetch it once (version 0.9.5).

macOS / Linux:

```bash
curl -L -o stash/static/muuri.min.js \
  https://cdnjs.cloudflare.com/ajax/libs/muuri/0.9.5/muuri.min.js
```

Windows PowerShell (its `curl` is an alias for Invoke-WebRequest and does not
take `-L -o`, so use one of these):

```powershell
Invoke-WebRequest -Uri https://cdnjs.cloudflare.com/ajax/libs/muuri/0.9.5/muuri.min.js -OutFile stash/static/muuri.min.js
# or, to use the real curl that ships with Windows 10+:
curl.exe -L -o stash/static/muuri.min.js https://cdnjs.cloudflare.com/ajax/libs/muuri/0.9.5/muuri.min.js
```

Until this file exists, the board page shows a message asking you to run that
command; every other page works without it.
