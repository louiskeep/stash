from stash.cli import main
from stash.embed import FakeEmbedder
from stash.storage import Storage


def test_cli_add_then_search(tmp_path, capsys):
    s = Storage.open(str(tmp_path / "t.db"))
    e = FakeEmbedder()
    assert main(["add", "the quick brown fox #animals"], storage=s, embedder=e) == 0
    assert main(["search", "brown fox"], storage=s, embedder=e) == 0
    out = capsys.readouterr().out
    assert "brown fox" in out


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
