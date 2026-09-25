import pytest
from stash.ports import IncomingMessage, FakeAsyncDelivery, FakeAsyncIngest


def _msg(uid=1, text="hi"):
    return IncomingMessage(update_id=uid, chat_id="c1", chat_type="private",
                           sender_id="s1", msg_id=str(uid), text=text)


async def test_fake_async_delivery_records_and_can_fail():
    d = FakeAsyncDelivery(fail_times=1)
    with pytest.raises(RuntimeError):
        await d.send("c1", "first")
    await d.send("c1", "second")
    assert d.sent == [("c1", "second")]


async def test_fake_async_ingest_polls_then_empties():
    ing = FakeAsyncIngest([_msg(1), _msg(2)])
    first = await ing.poll()
    assert [m.update_id for m in first] == [1, 2]
    assert await ing.poll() == []
    ing.ack("3")
    assert ing.offset == "3"
