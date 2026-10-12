"""Task 4: pile state round-trips through the server.

The drag/drop-to-stack interaction is browser-only; these cover the persistence
contract the client relies on: saving piles assigns members and labels, reload
reflects them, un-stacking below two members leaves no pile, and collapsed state
persists.
"""

from datetime import datetime, timezone

from stash.capture import capture
from tests.conftest import login


def _now():
    return datetime.now(timezone.utc).isoformat()


async def test_pile_persists_and_round_trips(client, storage, embedder):
    a = capture(storage, embedder, "note a #proj", "web", _now()).note_id
    b = capture(storage, embedder, "note b #proj", "web", _now()).note_id
    csrf = await login(client)
    await client.post("/board/piles", headers={"x-csrf-token": csrf},
                      json={"piles": [{"client_id": "1", "label": "#proj", "collapsed": True}],
                            "assignments": {str(a): "1", str(b): "1"}})
    data = (await client.get("/board/notes")).json()
    notes = {n["id"]: n for n in data["notes"]}
    assert notes[a]["pile_id"] == notes[b]["pile_id"] is not None
    pile = next(p for p in data["piles"] if p["id"] == notes[a]["pile_id"])
    assert pile["label"] == "#proj" and pile["collapsed"] == 1


async def test_unstack_leaves_no_pile(client, storage, embedder):
    a = capture(storage, embedder, "a", "web", _now()).note_id
    b = capture(storage, embedder, "b", "web", _now()).note_id
    csrf = await login(client)
    # Stack, then rebuild with the pile dissolved (client does this when a pile
    # drops below two members).
    await client.post("/board/piles", headers={"x-csrf-token": csrf},
                      json={"piles": [{"client_id": "1", "label": "x"}],
                            "assignments": {str(a): "1", str(b): "1"}})
    await client.post("/board/piles", headers={"x-csrf-token": csrf},
                      json={"piles": [], "assignments": {}})
    data = (await client.get("/board/notes")).json()
    notes = {n["id"]: n for n in data["notes"]}
    assert notes[a]["pile_id"] is None and notes[b]["pile_id"] is None
    assert data["piles"] == []
