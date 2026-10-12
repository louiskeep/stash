"""Task 2: session auth (login, logout, guard)."""

import re

import httpx
from httpx import ASGITransport

from tests.conftest import login


async def test_unauth_home_redirects_to_login(client):
    r = await client.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


async def test_login_page_renders(client):
    r = await client.get("/login")
    assert r.status_code == 200
    assert 'name="csrf_token"' in r.text


async def test_correct_password_sets_cookie_and_unlocks(client):
    token = re.search(r'name="csrf_token" value="([^"]+)"',
                      (await client.get("/login")).text).group(1)
    r = await client.post("/login", data={"password": "hunter2", "csrf_token": token})
    assert r.status_code == 303 and r.headers["location"] == "/"
    sc = r.headers.get("set-cookie", "").lower()
    assert "httponly" in sc and "samesite=lax" in sc
    assert (await client.get("/")).status_code == 200


async def test_wrong_password_401_no_cookie(client):
    token = re.search(r'name="csrf_token" value="([^"]+)"',
                      (await client.get("/login")).text).group(1)
    r = await client.post("/login", data={"password": "nope", "csrf_token": token})
    assert r.status_code == 401
    assert "stash_session" not in r.cookies


async def test_cookie_signed_with_other_secret_is_rejected(app):
    from stash import websession as ws
    forged = ws.issue_session("a-totally-different-secret-value")
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://stash.test",
                                 follow_redirects=False,
                                 cookies={ws.SESSION_COOKIE: forged}) as c:
        r = await c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"


async def test_secure_flag_on_https_absent_on_http(app):
    transport = ASGITransport(app=app)
    # http: no Secure
    async with httpx.AsyncClient(transport=transport, base_url="http://stash.test",
                                 follow_redirects=False) as c:
        tok = re.search(r'name="csrf_token" value="([^"]+)"',
                        (await c.get("/login")).text).group(1)
        sc = (await c.post("/login", data={"password": "hunter2", "csrf_token": tok})
              ).headers.get("set-cookie", "").lower()
        assert "secure" not in sc
    # https: Secure present
    async with httpx.AsyncClient(transport=transport, base_url="https://stash.test",
                                 follow_redirects=False) as c:
        tok = re.search(r'name="csrf_token" value="([^"]+)"',
                        (await c.get("/login")).text).group(1)
        sc = (await c.post("/login", data={"password": "hunter2", "csrf_token": tok})
              ).headers.get("set-cookie", "").lower()
        assert "secure" in sc


async def test_logout_clears_cookie(client):
    csrf = await login(client)
    r = await client.post("/logout", data={"csrf_token": csrf})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    client.cookies.clear()
    assert (await client.get("/")).status_code == 303
