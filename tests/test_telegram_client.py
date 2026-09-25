import httpx
from stash.telegram_client import TelegramClient


def _mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://api.telegram.org")


async def test_get_updates_returns_result_list():
    def handler(request):
        assert "getUpdates" in request.url.path
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 5}]})
    tc = TelegramClient("TOKEN", client=_mock(handler))
    assert await tc.get_updates(offset=None) == [{"update_id": 5}]


async def test_send_message_posts_chat_and_text():
    seen = {}
    def handler(request):
        import json
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})
    tc = TelegramClient("TOKEN", client=_mock(handler))
    await tc.send_message("c1", "hello")
    assert seen["chat_id"] == "c1" and seen["text"] == "hello"


async def test_own_client_gets_a_timeout_that_exceeds_the_long_poll_wait():
    # get_updates long-polls for 25s; httpx's 5s default read timeout would
    # fire on every quiet poll and force an immediate retry. A client we
    # create ourselves (no injected client) must use a longer timeout.
    tc = TelegramClient("TOKEN")
    try:
        assert tc._client.timeout.read >= 30
    finally:
        await tc.aclose()  # no network call made


async def test_error_does_not_leak_token_or_text():
    def handler(request):
        return httpx.Response(403, json={"ok": False, "description": "forbidden"})
    tc = TelegramClient("SECRET-TOKEN", client=_mock(handler))
    try:
        await tc.send_message("c1", "secret note body")
    except Exception as e:
        assert "SECRET-TOKEN" not in str(e)
        assert "secret note body" not in str(e)
    else:
        raise AssertionError("expected an error")
