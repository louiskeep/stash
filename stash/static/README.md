# Static assets

- `board.css`, `board.js` - the board, which is the whole web UI.

The board is the only screen: an ordered grid of your notes as paper cards, with
search, category/tag/date filters, saved boards, reminders, and settings in the
top bar. Drag one card onto another to merge them into a single card; click the
dashed divider in a merged card to snip it back apart.

No third-party JavaScript is loaded. stash does all processing and storage
locally, so the board ships only its own `board.css` and `board.js` and pulls
nothing from a CDN at runtime. There is no vendoring step to run.
