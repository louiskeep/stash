"""Task 1: saved boards storage + endpoints."""

from tests.conftest import login


async def test_save_list_delete_board(client, storage):
    csrf = await login(client)
    cfg = {"category": "work", "tag": "billing", "from": None, "to": None,
           "search": "", "order": "newest"}
    r = await client.post("/board/boards", headers={"x-csrf-token": csrf},
                          json={"name": "Work invoices", "config": cfg})
    assert r.status_code == 200 and r.json()["ok"]
    bid = r.json()["id"]

    r = await client.get("/board/boards")
    boards = r.json()["boards"]
    row = next(b for b in boards if b["id"] == bid)
    assert row["name"] == "Work invoices"
    assert row["config"]["category"] == "work" and row["config"]["tag"] == "billing"

    r = await client.post("/board/boards/delete", headers={"x-csrf-token": csrf},
                          json={"id": bid})
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert all(b["id"] != bid for b in (await client.get("/board/boards")).json()["boards"])


async def test_boards_require_auth(client):
    assert (await client.get("/board/boards")).status_code == 303


async def test_save_bad_csrf_rejected(client, storage):
    await login(client)
    before = len(storage.list_boards())
    r = await client.post("/board/boards", headers={"x-csrf-token": "bad"},
                          json={"name": "X", "config": {}})
    assert r.status_code == 403
    assert len(storage.list_boards()) == before


async def test_save_missing_name_is_400(client):
    csrf = await login(client)
    r = await client.post("/board/boards", headers={"x-csrf-token": csrf},
                          json={"name": "  ", "config": {}})
    assert r.status_code == 400


async def test_save_config_must_be_object(client):
    csrf = await login(client)
    r = await client.post("/board/boards", headers={"x-csrf-token": csrf},
                          json={"name": "X", "config": "nope"})
    assert r.status_code == 400


async def test_delete_unknown_board_is_noop(client):
    csrf = await login(client)
    r = await client.post("/board/boards/delete", headers={"x-csrf-token": csrf},
                          json={"id": 999999})
    assert r.status_code == 200 and r.json()["deleted"] is False


async def test_reindex_preserves_boards(storage, embedder):
    import json
    from stash.reindex import reindex
    bid = storage.save_board("Keep me", json.dumps({"category": "work"}))
    reindex(storage, embedder)
    assert any(b["id"] == bid for b in storage.list_boards())
