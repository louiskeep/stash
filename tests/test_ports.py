from stash.ports import IncomingMessage, MemoryIngest, MemoryDelivery


def test_memory_ingest_polls_then_empties():
    ing = MemoryIngest([IncomingMessage("c1", "m1", "hi", "s1")])
    assert [m.text for m in ing.poll()] == ["hi"]
    assert ing.poll() == []


def test_memory_delivery_records():
    d = MemoryDelivery()
    d.send("c1", "hello")
    assert d.sent == [("c1", "hello")]
