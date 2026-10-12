"""Task 6: every app route rejects unauthenticated access, leaking no content.

If someone adds a route without the guard covering it, this sweep fails. The
guard is deny-by-default (an allowlist of open paths), so the property holds for
routes not yet imagined here too; this test pins the current table explicitly.
"""

import pytest

GET_ROUTES = ["/", "/board/notes", "/board/search?q=milk"]
POST_ROUTES = ["/logout", "/board/positions", "/board/piles",
               "/board/add", "/board/delete"]
OPEN_ROUTES = ["/healthz", "/login", "/static/board.css"]
# W5 removed these pages entirely; authed, they 404 (gone, not merely guarded).
REMOVED_ROUTES = ["/search", "/search/results", "/timeline", "/topics",
                  "/topics/tag/x", "/topics/category/w", "/topics/cluster/1",
                  "/note/1", "/reminders"]


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


@pytest.mark.parametrize("path", REMOVED_ROUTES)
async def test_removed_pages_404_when_authed(client, path):
    from tests.conftest import login
    await login(client)
    assert (await client.get(path)).status_code == 404
