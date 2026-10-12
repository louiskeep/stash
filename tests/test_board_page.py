"""Task 3: the board page is the home surface, behind auth, with CSRF + Muuri."""

from tests.conftest import login


async def test_home_is_board_behind_auth(client):
    r = await client.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"


async def test_board_page_renders_when_authed(client):
    await login(client)
    r = await client.get("/")
    assert r.status_code == 200
    assert 'name="csrf-token"' in r.text
    assert "board.js" in r.text
    # Muuri is vendored locally, never loaded from a CDN at runtime.
    assert "/static/muuri.min.js" in r.text
    assert "cdnjs" not in r.text and "unpkg" not in r.text
    # Nav still reaches the text pages.
    assert "/search" in r.text and "/timeline" in r.text and "/topics" in r.text


async def test_search_relocated_to_search_path(client):
    await login(client)
    assert (await client.get("/search")).status_code == 200
    assert (await client.get("/search/results?q=")).status_code == 200


async def test_board_static_assets_served(client):
    await login(client)
    assert (await client.get("/static/board.css")).status_code == 200
    assert (await client.get("/static/board.js")).status_code == 200
