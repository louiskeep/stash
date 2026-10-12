"""The board page is the only screen, behind auth, with CSRF + local Muuri."""

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
    # The board carries its own controls: search, category + tag, order, dates,
    # the reminders dropdown, and the saved-boards column.
    assert 'id="search"' in r.text and 'id="category"' in r.text and 'id="tag"' in r.text
    assert 'id="order"' in r.text and 'id="from"' in r.text and 'id="to"' in r.text
    assert 'id="remindersBtn"' in r.text and 'id="boardsList"' in r.text
    # No links to the removed pages, and no old combined topic/rail markup.
    assert "/timeline" not in r.text and 'id="topic"' not in r.text and 'id="rail"' not in r.text


async def test_board_static_assets_served(client):
    await login(client)
    assert (await client.get("/static/board.css")).status_code == 200
    assert (await client.get("/static/board.js")).status_code == 200
