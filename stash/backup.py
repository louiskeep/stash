"""Consistent online backup of the stash database.

`VACUUM INTO` writes a fully consistent, defragmented copy of the database to a
new file. It reads a single snapshot of the source, so the copy is coherent even
while `stash serve` is writing, and it produces one plain file with no WAL side
files to reconcile. This is the spec-9 backup: not a filesystem copy of a live
WAL database, which can capture a torn state.
"""

import os


def backup(storage, dest: str, force: bool = False) -> str:
    """Write a consistent copy of the live database to `dest`.

    Refuses to overwrite an existing file unless `force` is set, so a backup
    never silently clobbers a previous one (or an unrelated file). Returns the
    destination path on success.

    VACUUM INTO itself requires that the destination not already exist, so the
    explicit check here turns that into a clear error and lets `force` remove the
    old copy first.
    """
    if os.path.exists(dest):
        if not force:
            raise FileExistsError(
                f"{dest} already exists; pass --force to overwrite it")
        os.remove(dest)
    # Parameterized: the path is bound, not interpolated into the SQL text.
    storage.conn.execute("VACUUM INTO ?", (dest,))
    return dest
