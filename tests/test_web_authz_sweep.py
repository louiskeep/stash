"""Task 6: every app route rejects unauthenticated access, leaking no content.

If someone adds a route without the guard covering it, this sweep fails. The
guard is deny-by-default (an allowlist of open paths), so the property holds for
routes not yet imagined here too; this test pins the current table explicitly.
"""

import pytest

GET_ROUTES = [
    "/", "/search", "/search?q=milk", "/timeline", "/topics",
    "/topics/tag/groceries", "/topics/category/work", "/note/1", "/reminders",
]
POST_ROUTES = ["/add", "/logout"]
OPEN_ROUTES = ["/healthz", "/login", "/static/app.css"]


@pytest.mark.parametrize("path", GET_ROUTES)
async def test_unauth_get_redirects_and_leaks_nothing(client, seed, path):
    r = await client.get(path)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert "buy milk" not in r.text


@pytest.mark.parametrize("path", POST_ROUTES)
async def test_unauth_post_has_no_effect(client, storage, seed, path):
    before = len(storage.recent_notes(1000))
    r = await client.post(path, data={"text": "sneaky", "csrf_token": "x"})
    assert r.status_code in (303, 403)
    assert len(storage.recent_notes(1000)) == before


@pytest.mark.parametrize("path", OPEN_ROUTES)
async def test_open_routes_reachable_without_auth(client, path):
    r = await client.get(path)
    assert r.status_code == 200
