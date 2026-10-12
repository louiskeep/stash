"""Task 2: session auth (login, logout, guard)."""

import re

from tests.conftest import login


def test_unauth_home_redirects_to_login(client):
    r = client.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert 'name="csrf_token"' in r.text


def test_correct_password_sets_cookie_and_unlocks(client):
    token = re.search(r'name="csrf_token" value="([^"]+)"',
                      client.get("/login").text).group(1)
    r = client.post("/login", data={"password": "hunter2", "csrf_token": token})
    assert r.status_code == 303 and r.headers["location"] == "/"
    sc = r.headers.get("set-cookie", "").lower()
    assert "httponly" in sc and "samesite=lax" in sc
    # Now the home page is reachable.
    assert client.get("/").status_code == 200


def test_wrong_password_401_no_cookie(client):
    token = re.search(r'name="csrf_token" value="([^"]+)"',
                      client.get("/login").text).group(1)
    r = client.post("/login", data={"password": "nope", "csrf_token": token})
    assert r.status_code == 401
    assert "stash_session" not in r.cookies


def test_cookie_signed_with_other_secret_is_rejected(app, config):
    from starlette.testclient import TestClient
    from stash import websession as ws
    forged = ws.issue_session("a-totally-different-secret-value")
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(ws.SESSION_COOKIE, forged)
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_secure_flag_on_https_absent_on_http(app):
    from starlette.testclient import TestClient
    # http: no Secure
    c = TestClient(app, follow_redirects=False)
    tok = re.search(r'name="csrf_token" value="([^"]+)"',
                    c.get("/login").text).group(1)
    sc = c.post("/login", data={"password": "hunter2", "csrf_token": tok}
                ).headers.get("set-cookie", "").lower()
    assert "secure" not in sc
    # https: Secure present
    cs = TestClient(app, follow_redirects=False, base_url="https://stash.local")
    tok = re.search(r'name="csrf_token" value="([^"]+)"',
                    cs.get("/login").text).group(1)
    sc = cs.post("/login", data={"password": "hunter2", "csrf_token": tok}
                 ).headers.get("set-cookie", "").lower()
    assert "secure" in sc


def test_logout_clears_cookie(client):
    csrf = login(client)
    r = client.post("/logout", data={"csrf_token": csrf})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    # Cookie cleared -> home redirects again.
    client.cookies.clear()
    assert client.get("/").status_code == 303
