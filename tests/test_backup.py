"""Task 1: the consistent online backup (VACUUM INTO)."""

import os
from datetime import datetime, timezone

import pytest

from stash.backup import backup
from stash.capture import capture
from stash.embed import FakeEmbedder
from stash.storage import Storage


def _now():
    return datetime.now(timezone.utc).isoformat()


def _seed(storage, embedder):
    capture(storage, embedder, "buy milk #groceries", "web", _now())
    capture(storage, embedder, "remind me in 30min to stretch", "web", _now())


def test_backup_is_a_valid_copy_with_same_rows(tmp_path):
    storage = Storage.open(":memory:")
    embedder = FakeEmbedder()
    _seed(storage, embedder)
    dest = str(tmp_path / "backup.db")

    assert backup(storage, dest) == dest
    assert os.path.exists(dest)

    # The copy opens as a stash DB and carries the same notes and reminder.
    copy = Storage.open(dest)
    src_notes = [r["raw"] for r in storage.recent_notes(100)]
    cp_notes = [r["raw"] for r in copy.recent_notes(100)]
    assert cp_notes == src_notes
    assert len(copy.list_reminders(100)) == len(storage.list_reminders(100))


def test_backup_leaves_source_usable_and_unchanged(tmp_path):
    storage = Storage.open(":memory:")
    embedder = FakeEmbedder()
    _seed(storage, embedder)
    before = [dict(r) for r in storage.recent_notes(100)]

    backup(storage, str(tmp_path / "b.db"))

    after = [dict(r) for r in storage.recent_notes(100)]
    assert after == before
    # Source still writable after the backup.
    capture(storage, embedder, "a new note after backup", "web", _now())
    assert len(storage.recent_notes(100)) == len(before) + 1


def test_backup_refuses_overwrite_without_force(tmp_path):
    storage = Storage.open(":memory:")
    _seed(storage, FakeEmbedder())
    dest = str(tmp_path / "b.db")
    backup(storage, dest)
    with pytest.raises(FileExistsError):
        backup(storage, dest)
    # With force it succeeds.
    assert backup(storage, dest, force=True) == dest


def test_cli_backup_command(tmp_path):
    storage = Storage.open(":memory:")
    embedder = FakeEmbedder()
    _seed(storage, embedder)
    dest = str(tmp_path / "cli.db")
    from stash.cli import main
    rc = main(["backup", dest], storage=storage, embedder=embedder)
    assert rc == 0
    assert os.path.exists(dest)
    # Refuses overwrite without --force.
    assert main(["backup", dest], storage=storage, embedder=embedder) == 1
    # Succeeds with --force.
    assert main(["backup", dest, "--force"], storage=storage, embedder=embedder) == 0
