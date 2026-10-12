"""Signed sessions and CSRF tokens for the web UI.

One password grants a session: a signed, timestamped token in an HttpOnly cookie.
There is no server-side session store (single user, one process); the cookie is
the session, and its signature is what makes it unforgeable. The CSRF token is
derived from the session token and signed too, so a valid CSRF token both proves
"same session" and cannot be minted cross-site.

Signing uses `itsdangerous` with the configured persistent secret
(`STASH_WEB_SESSION_SECRET`), so sessions survive a restart (the secret is
stable) but are invalidated if the operator rotates it. All equality checks on
secrets and tokens use `hmac.compare_digest`.
"""

import hmac
import secrets

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from itsdangerous.exc import BadData

SESSION_COOKIE = "stash_session"
# Each session carries a random nonce as its signed payload. Single user, so the
# nonce is not an identity to look up anywhere; it exists so that distinct logins
# produce distinct tokens, which is what lets a CSRF token be bound to one
# specific session rather than to a shared constant.
_NONCE_BYTES = 16
# A CSRF token for the pre-session (login) form carries this marker instead of a
# session nonce, so a login CSRF token can never be replayed as a session and a
# session-bound CSRF token can never satisfy the login check.
_LOGIN_CSRF_VALUE = "login"
_CSRF_SALT = "stash-csrf"
# Sessions are long-lived for a personal tool; max_age is enforced at verify.
DEFAULT_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days


def _signer(secret: str) -> TimestampSigner:
    return TimestampSigner(secret)


def _csrf_signer(secret: str) -> TimestampSigner:
    # Separate salt so a session token can never be accepted where a CSRF token
    # is expected, or vice versa, even though both are signed with one secret.
    return TimestampSigner(secret, salt=_CSRF_SALT)


def issue_session(secret: str) -> str:
    """Return a fresh signed session token to put in the session cookie.

    Each call embeds a new random nonce, so two logins yield two distinct
    tokens; this is what makes a session-bound CSRF token specific to one
    session.
    """
    nonce = secrets.token_urlsafe(_NONCE_BYTES)
    return _signer(secret).sign(nonce.encode()).decode()


def verify_session(token: str | None, secret: str,
                   max_age: int = DEFAULT_MAX_AGE_SECONDS) -> bool:
    """True iff `token` is a valid, unexpired session signed with `secret`.

    The nonce payload is opaque; a good signature over an unexpired token is
    the whole of the check, since there is no server-side session list to
    consult for a single-user tool.
    """
    if not token:
        return False
    try:
        _signer(secret).unsign(token, max_age=max_age)
    except (BadSignature, SignatureExpired, BadData, ValueError):
        return False
    return True


def check_password(submitted: str | None, expected: str | None) -> bool:
    """Timing-safe password check. False if either side is missing."""
    if not submitted or not expected:
        return False
    return hmac.compare_digest(submitted.encode(), expected.encode())


def issue_csrf(secret: str, session_token: str | None) -> str:
    """Issue a CSRF token bound to this session (or to the login bootstrap).

    When `session_token` is present, the CSRF token is bound to it, so it is
    only valid for that exact session. For the login form there is no session
    yet, so the token is bound to a fixed login marker; `POST /login` verifies
    it with `verify_login_csrf`, which closes the cross-site-login hole.
    """
    payload = session_token if session_token else _LOGIN_CSRF_VALUE
    return _csrf_signer(secret).sign(payload.encode()).decode()


def _verify_csrf(token: str | None, secret: str, expected_payload: str,
                 max_age: int = DEFAULT_MAX_AGE_SECONDS) -> bool:
    if not token:
        return False
    try:
        value = _csrf_signer(secret).unsign(token, max_age=max_age)
    except (BadSignature, SignatureExpired, BadData, ValueError):
        return False
    return hmac.compare_digest(value, expected_payload.encode())


def verify_csrf(token: str | None, secret: str, session_token: str | None,
                max_age: int = DEFAULT_MAX_AGE_SECONDS) -> bool:
    """Verify a CSRF token for an authenticated request.

    The token must have been bound to this exact session token, so a token
    minted for a different session (or for the login form) does not pass.
    """
    if not session_token:
        return False
    return _verify_csrf(token, secret, session_token, max_age)


def verify_login_csrf(token: str | None, secret: str,
                      max_age: int = DEFAULT_MAX_AGE_SECONDS) -> bool:
    """Verify the CSRF token on the pre-session login POST."""
    return _verify_csrf(token, secret, _LOGIN_CSRF_VALUE, max_age)
