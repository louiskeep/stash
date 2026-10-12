"""CSRF protection on state-changing routes.

The board's add path is /board/add (JSON, CSRF via the X-CSRF-Token header); the
old /add form route was removed in W5.
"""

from tests.conftest import login


async def test_board_add_without_csrf_rejected(client, seed):
    await login(client)
    r = await client.post("/board/add", json={"text": "no token here"})
    assert r.status_code == 403


async def test_board_add_with_forged_csrf_rejected(client, seed):
    await login(client)
    r = await client.post("/board/add", headers={"x-csrf-token": "bogus"},
                          json={"text": "forged"})
    assert r.status_code == 403


async def test_board_add_with_valid_csrf_captures(client, storage, seed):
    csrf = await login(client)
    before = storage.recent_notes(100)
    r = await client.post("/board/add", headers={"x-csrf-token": csrf},
                          json={"text": "fresh web note"})
    assert r.status_code == 200
    after = storage.recent_notes(100)
    assert len(after) == len(before) + 1
    assert after[0]["raw"] == "fresh web note"
    assert after[0]["source"] == "web"


async def test_login_without_csrf_rejected(client):
    r = await client.post("/login", data={"password": "hunter2"})
    assert r.status_code == 403


async def test_logout_without_csrf_rejected(client):
    await login(client)
    r = await client.post("/logout", data={})
    assert r.status_code == 403
