from stash.ports import IncomingMessage, MemoryIngest, MemoryDelivery


def test_memory_ingest_polls_then_empties():
    ing = MemoryIngest([
        IncomingMessage(update_id=1, chat_id="c1", chat_type="private",
                         sender_id="s1", msg_id="m1", text="hi")
    ])
    assert [m.text for m in ing.poll()] == ["hi"]
    assert ing.poll() == []


def test_memory_delivery_records():
    d = MemoryDelivery()
    d.send("c1", "hello")
    assert d.sent == [("c1", "hello")]
