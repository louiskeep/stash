"""Board JSON endpoint and the CSRF-protected groups/add/delete routes."""

from datetime import datetime, timezone

from stash.capture import capture
from tests.conftest import login


def _now():
    return datetime.now(timezone.utc).isoformat()


async def test_board_notes_returns_notes(client, storage, embedder):
    capture(storage, embedder, "invoice @work #billing", "web", _now())
    await login(client)
    r = await client.get("/board/notes")
    assert r.status_code == 200
    data = r.json()
    assert "notes" in data
    assert any("invoice" in n["raw"] for n in data["notes"])
    # Each note carries its merge-group membership for the client to reconstruct.
    n = data["notes"][0]
    assert "pile_id" in n and "seq" in n


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


async def test_groups_save_with_csrf(client, storage, embedder):
    a = capture(storage, embedder, "note a", "web", _now()).note_id
    b = capture(storage, embedder, "note b", "web", _now()).note_id
    csrf = await login(client)
    r = await client.post("/board/groups", headers={"x-csrf-token": csrf},
                          json={"groups": [[a, b]]})
    assert r.status_code == 200 and r.json()["groups"] == 1
    notes = {n["id"]: n for n in (await client.get("/board/notes")).json()["notes"]}
    # Both notes land in the same group, in order (seq 0 then 1).
    assert notes[a]["pile_id"] is not None
    assert notes[a]["pile_id"] == notes[b]["pile_id"]
    assert notes[a]["seq"] == 0 and notes[b]["seq"] == 1


async def test_groups_snip_round_trips(client, storage, embedder):
    a = capture(storage, embedder, "a", "web", _now()).note_id
    b = capture(storage, embedder, "b", "web", _now()).note_id
    csrf = await login(client)
    # Merge, then snip apart: the client re-sends the full layout (now empty).
    await client.post("/board/groups", headers={"x-csrf-token": csrf},
                      json={"groups": [[a, b]]})
    await client.post("/board/groups", headers={"x-csrf-token": csrf},
                      json={"groups": []})
    notes = {n["id"]: n for n in (await client.get("/board/notes")).json()["notes"]}
    assert notes[a]["pile_id"] is None and notes[b]["pile_id"] is None


async def test_groups_bad_csrf_rejected(client, storage, embedder):
    a = capture(storage, embedder, "a", "web", _now()).note_id
    b = capture(storage, embedder, "b", "web", _now()).note_id
    await login(client)
    r = await client.post("/board/groups", headers={"x-csrf-token": "bogus"},
                          json={"groups": [[a, b]]})
    assert r.status_code == 403
    notes = {n["id"]: n for n in (await client.get("/board/notes")).json()["notes"]}
    assert notes[a]["pile_id"] is None  # not written


async def test_groups_malformed_body_is_400(client):
    csrf = await login(client)
    r = await client.post("/board/groups",
                          headers={"x-csrf-token": csrf, "content-type": "application/json"},
                          content="not json")
    assert r.status_code == 400


async def test_groups_non_list_is_400(client):
    csrf = await login(client)
    r = await client.post("/board/groups", headers={"x-csrf-token": csrf},
                          json={"groups": "nope"})
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


async def test_delete_bad_csrf_does_not_delete(client, storage, embedder):
    # The destructive board action must reject a forged CSRF token before acting.
    nid = capture(storage, embedder, "keep me", "web", _now()).note_id
    await login(client)
    r = await client.post("/board/delete", headers={"x-csrf-token": "bogus"},
                          json={"note_id": nid})
    assert r.status_code == 403
    assert storage.get_note(nid) is not None  # still there


async def test_groups_and_add_bad_csrf(client, storage, embedder):
    a = capture(storage, embedder, "a", "web", _now()).note_id
    await login(client)
    for path, body in [("/board/groups", {"groups": [[a]]}),
                       ("/board/add", {"text": "x"})]:
        r = await client.post(path, headers={"x-csrf-token": "bad"}, json=body)
        assert r.status_code == 403
