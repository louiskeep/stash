"""Tasks 1-2: board JSON endpoint and the CSRF-protected save/add/delete routes."""

import json
from datetime import datetime, timezone

from stash.capture import capture
from tests.conftest import login


def _now():
    return datetime.now(timezone.utc).isoformat()


async def test_board_notes_returns_notes_and_piles(client, storage, embedder):
    capture(storage, embedder, "invoice @work #billing", "web", _now())
    await login(client)
    r = await client.get("/board/notes")
    assert r.status_code == 200
    data = r.json()
    assert "notes" in data and "piles" in data
    assert any("invoice" in n["raw"] for n in data["notes"])


async def test_board_notes_script_is_data_not_html(client, storage, embedder):
    capture(storage, embedder, "<script>alert(1)</script>", "web", _now())
    await login(client)
    r = await client.get("/board/notes")
    # The raw text is a JSON string value; it is not HTML and the endpoint does
    # not render it.
    note = next(n for n in r.json()["notes"] if "script" in n["raw"])
    assert note["raw"] == "<script>alert(1)</script>"
    assert r.headers["content-type"].startswith("application/json")


async def test_board_notes_requires_auth(client, storage, embedder):
    capture(storage, embedder, "secret note", "web", _now())
    r = await client.get("/board/notes")
    assert r.status_code == 303
    assert "secret note" not in r.text


async def test_positions_save_with_csrf(client, storage, embedder):
    nid = capture(storage, embedder, "a note", "web", _now()).note_id
    csrf = await login(client)
    r = await client.post("/board/positions",
                          headers={"x-csrf-token": csrf},
                          json={"positions": [{"note_id": nid, "x": 12, "y": 34, "z": 1}]})
    assert r.status_code == 200 and r.json()["written"] == 1
    # Position is readable back.
    notes = (await client.get("/board/notes")).json()["notes"]
    saved = next(n for n in notes if n["id"] == nid)
    assert saved["x"] == 12 and saved["y"] == 34


async def test_positions_bad_csrf_rejected(client, storage, embedder):
    nid = capture(storage, embedder, "a note", "web", _now()).note_id
    await login(client)
    r = await client.post("/board/positions",
                          headers={"x-csrf-token": "bogus"},
                          json={"positions": [{"note_id": nid, "x": 1, "y": 2}]})
    assert r.status_code == 403
    notes = (await client.get("/board/notes")).json()["notes"]
    assert next(n for n in notes if n["id"] == nid)["x"] is None  # not written


async def test_positions_malformed_body_is_400(client):
    csrf = await login(client)
    r = await client.post("/board/positions",
                          headers={"x-csrf-token": csrf, "content-type": "application/json"},
                          content="not json")
    assert r.status_code == 400


async def test_add_from_board(client, storage):
    csrf = await login(client)
    r = await client.post("/board/add", headers={"x-csrf-token": csrf},
                          json={"text": "stashed from the board #canvas"})
    assert r.status_code == 200 and r.json()["ok"]
    newest = storage.recent_notes(1)[0]
    assert newest["raw"] == "stashed from the board #canvas"
    assert newest["source"] == "web"


async def test_add_empty_rejected(client):
    csrf = await login(client)
    r = await client.post("/board/add", headers={"x-csrf-token": csrf},
                          json={"text": "   "})
    assert r.status_code == 400


async def test_delete_from_board(client, storage, embedder):
    nid = capture(storage, embedder, "delete me", "web", _now()).note_id
    csrf = await login(client)
    r = await client.post("/board/delete", headers={"x-csrf-token": csrf},
                          json={"note_id": nid})
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert storage.get_note(nid) is None


async def test_piles_save_with_csrf(client, storage, embedder):
    a = capture(storage, embedder, "note a", "web", _now()).note_id
    b = capture(storage, embedder, "note b", "web", _now()).note_id
    csrf = await login(client)
    r = await client.post("/board/piles", headers={"x-csrf-token": csrf},
                          json={"piles": [{"client_id": "p1", "label": "pile one"}],
                                "assignments": {str(a): "p1", str(b): "p1"}})
    assert r.status_code == 200
    notes = {n["id"]: n for n in (await client.get("/board/notes")).json()["notes"]}
    assert notes[a]["pile_id"] is not None and notes[a]["pile_id"] == notes[b]["pile_id"]
