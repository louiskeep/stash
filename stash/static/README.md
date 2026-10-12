# Static assets

- `app.css`, `htmx.min.js` - the text UI (Search, Timeline, Topics, Reminders).
- `board.css`, `board.js`, `board-piles.js` - the board (home) surface.

## Muuri (board drag library)

The board loads Muuri 0.9.5 from cdnjs, so it works out of the box when the
machine has internet on first load. stash otherwise does all processing and
storage locally; to make the board fully offline too, vendor Muuri here:

1. Download `muuri.min.js` (version 0.9.5) from
   https://cdnjs.cloudflare.com/ajax/libs/muuri/0.9.5/muuri.min.js
2. Save it as `stash/static/muuri.min.js`.
3. In `stash/templates/board.html`, change the Muuri `<script src>` from the
   cdnjs URL to `/static/muuri.min.js`.

After that the board needs no network.
