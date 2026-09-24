from stash.embed import FakeEmbedder


def test_fake_embedder_is_deterministic_and_right_dim():
    e = FakeEmbedder(dim=384)
    v1 = e.embed("hello")
    v2 = e.embed("hello")
    assert v1 == v2
    assert len(v1) == 384
    assert e.embed("hello") != e.embed("world")


def test_fake_embed_batch_matches_single():
    e = FakeEmbedder(dim=384)
    assert e.embed_batch(["a", "b"]) == [e.embed("a"), e.embed("b")]
