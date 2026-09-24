import stash.cli as cli_module
from stash.cli import main
from stash.embed import FakeEmbedder
from stash.recall import search as recall_search
from stash.storage import Storage


def test_cli_add_then_search(tmp_path, capsys):
    s = Storage.open(str(tmp_path / "t.db"))
    e = FakeEmbedder()
    assert main(["add", "the quick brown fox #animals"], storage=s, embedder=e) == 0
    assert main(["search", "brown fox"], storage=s, embedder=e) == 0
    out = capsys.readouterr().out
    assert "brown fox" in out


def test_cli_reindexes_when_embed_model_changes(tmp_path, capsys):
    # Gate finding 3 / spec 6.5: changing the embedding model must not
    # silently mix vector spaces. main() records which model produced the
    # stored vectors, and reindexes with the new model when it changes, so
    # a later query is always compared against vectors from the same model.
    s = Storage.open(str(tmp_path / "t.db"))

    class NamedFakeEmbedder(FakeEmbedder):
        def __init__(self, model_name, dim=384):
            super().__init__(dim)
            self._model_name = model_name
            self.embed_calls = 0

        @property
        def name(self):
            return self._model_name

        def embed(self, text):
            self.embed_calls += 1
            return super().embed(text)

    e_a = NamedFakeEmbedder("model-a")
    assert main(["add", "distinct search phrase zephyr"], storage=s, embedder=e_a) == 0
    assert s.kv_get("embed_model") == "model-a"

    e_b = NamedFakeEmbedder("model-b")
    capsys.readouterr()  # clear
    assert main(["search", "distinct search phrase zephyr"], storage=s, embedder=e_b) == 0
    out = capsys.readouterr().out
    assert "zephyr" in out  # still found, re-embedded and searched under model-b
    assert s.kv_get("embed_model") == "model-b"
    assert e_b.embed_calls >= 1  # reindex ran under the new model


def test_cli_wires_stash_embed_model_into_default_embedder(tmp_path, monkeypatch):
    monkeypatch.setenv("STASH_EMBED_MODEL", "custom-test-model")
    seen = {}

    class SpyEmbedder:
        def __init__(self, model_name):
            seen["model_name"] = model_name
            self.name = model_name  # main() reads .name for the kv reconcile

    monkeypatch.setattr(cli_module, "SentenceTransformerEmbedder", SpyEmbedder)
    s = Storage.open(str(tmp_path / "t.db"))
    # "tags" never calls the embedder, so a real model is never loaded; this
    # only checks that cfg.embed_model reaches the constructor.
    assert main(["tags"], storage=s) == 0
    assert seen["model_name"] == "custom-test-model"


def test_cli_tags_lists_counts(tmp_path, capsys):
    s = Storage.open(str(tmp_path / "t.db"))
    e = FakeEmbedder()
    main(["add", "note one #work"], storage=s, embedder=e)
    main(["add", "note two #work #urgent"], storage=s, embedder=e)
    capsys.readouterr()  # clear
    assert main(["tags"], storage=s, embedder=e) == 0
    out = capsys.readouterr().out
    assert "#work (2)" in out
    assert "#urgent (1)" in out


def test_startup_runs_crash_repair_before_dispatch(tmp_path, capsys):
    s = Storage.open(str(tmp_path / "t.db"))
    e = FakeEmbedder()
    # Simulate a crash after the raw write: the note exists but was never
    # derived, so it isn't indexed yet.
    s.add_note("crashed before derive brown fox", "cli", "2026-09-24T00:00:00+00:00")
    assert s.underived_note_ids() != []
    assert recall_search(s, e, "brown fox") == []  # not indexed yet

    assert main(["search", "brown fox"], storage=s, embedder=e) == 0
    out = capsys.readouterr().out
    assert "brown fox" in out  # startup repair healed it before search ran
