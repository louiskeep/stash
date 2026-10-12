"""Task 1: recluster() core. Skipped when scikit-learn is not installed.

These build embeddings with distinct directions (two dense themes plus a few
isolated points) so HDBSCAN reliably produces clusters and outliers, and assert
the invariants that always hold for a valid partition rather than exact counts,
which are sensitive to the synthetic geometry.
"""

import sqlite3
import warnings

import pytest

from stash.cluster import CLUSTER_AVAILABLE, recluster, ClusterUnavailable

pytestmark = pytest.mark.skipif(
    not CLUSTER_AVAILABLE, reason="scikit-learn (cluster extra) not installed")


class _FakeStorage:
    """A storage stand-in backed by a plain sqlite3 DB (no vec0), so these tests
    do not need sqlite-vec. It implements the exact methods recluster() uses and
    borrows the real Storage write/read methods for cluster persistence."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            "CREATE TABLE notes(id INTEGER PRIMARY KEY, raw TEXT);"
            "CREATE TABLE note_tags(note_id INT, tag TEXT);"
            "CREATE TABLE note_meta(note_id INTEGER PRIMARY KEY, category TEXT,"
            " intent TEXT, cluster_id INTEGER);"
            "CREATE TABLE reminders(id INTEGER PRIMARY KEY, note_id INT, status TEXT);"
            "CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE clusters(id INTEGER PRIMARY KEY, label TEXT, size INT,"
            " updated_at TEXT);")
        self._emb = {}

    def add(self, nid, raw, vec, tags=()):
        self.conn.execute("INSERT INTO notes(id, raw) VALUES(?,?)", (nid, raw))
        self.conn.execute("INSERT INTO note_meta(note_id) VALUES(?)", (nid,))
        for t in tags:
            self.conn.execute("INSERT INTO note_tags VALUES(?,?)", (nid, t))
        self._emb[nid] = vec
        self.conn.commit()

    def all_embeddings(self):
        return sorted(self._emb.items())

    def get_note(self, nid):
        return self.conn.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()


# Borrow the real persistence/read methods.
from stash.storage import Storage  # noqa: E402

_FakeStorage.replace_clusters = Storage.replace_clusters
_FakeStorage.clear_clusters = Storage.clear_clusters
_FakeStorage.cluster_topics = Storage.cluster_topics
_FakeStorage.notes_by_cluster = Storage.notes_by_cluster


def _two_themes_with_outliers(seed=7):
    import numpy as np
    rng = np.random.default_rng(seed)
    s = _FakeStorage()
    dA = np.zeros(384); dA[:10] = 1.0; dA /= np.linalg.norm(dA)
    dB = np.zeros(384); dB[200:210] = 1.0; dB /= np.linalg.norm(dB)
    nid = 1
    for _ in range(20):
        s.add(nid, "invoice vendor payment", (dA + rng.normal(0, 0.03, 384)).tolist(),
              tags=["work"]); nid += 1
    for _ in range(20):
        s.add(nid, "gym run workout", (dB + rng.normal(0, 0.03, 384)).tolist(),
              tags=["fit"]); nid += 1
    for k in range(4):
        v = rng.normal(0, 1, 384); v[100 + k] = 12
        s.add(nid, f"lone thought {k}", v.tolist()); nid += 1
    return s


def test_recluster_partitions_and_keeps_invariants():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s = _two_themes_with_outliers()
        res = recluster(s, min_notes=10, min_cluster_size=4)
    assert not res.skipped
    assert res.n_clusters >= 2
    # Every note is either clustered or an outlier; the counts partition cleanly.
    assert res.n_clustered + res.n_outliers == res.n_notes
    null_ids = [r["note_id"] for r in s.conn.execute(
        "SELECT note_id FROM note_meta WHERE cluster_id IS NULL")]
    assert len(null_ids) == res.n_outliers
    # Outliers are allowed and present with this geometry.
    assert res.n_outliers >= 1
    # Every written cluster has a non-empty label.
    assert all(t["label"] and t["label"].strip() for t in s.cluster_topics())


def test_recluster_is_idempotent_on_unchanged_input():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s = _two_themes_with_outliers()
        recluster(s, min_notes=10, min_cluster_size=4)
        sizes1 = sorted(t["size"] for t in s.cluster_topics())
        recluster(s, min_notes=10, min_cluster_size=4)
        sizes2 = sorted(t["size"] for t in s.cluster_topics())
    assert sizes1 == sizes2


def test_recluster_derived_only_leaves_notes_reminders_kv():
    import numpy as np
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s = _two_themes_with_outliers()
        s.conn.execute("INSERT INTO reminders(note_id, status) VALUES(1,'pending')")
        s.conn.execute("INSERT INTO kv(key, value) VALUES('x','y')")
        s.conn.commit()
        notes_before = s.conn.execute("SELECT id, raw FROM notes ORDER BY id").fetchall()
        rem_before = s.conn.execute("SELECT * FROM reminders").fetchall()
        kv_before = s.conn.execute("SELECT * FROM kv").fetchall()
        recluster(s, min_notes=10, min_cluster_size=4)
    notes_after = s.conn.execute("SELECT id, raw FROM notes ORDER BY id").fetchall()
    assert [dict(r) for r in notes_after] == [dict(r) for r in notes_before]
    assert [dict(r) for r in s.conn.execute("SELECT * FROM reminders")] == [dict(r) for r in rem_before]
    assert [dict(r) for r in s.conn.execute("SELECT * FROM kv")] == [dict(r) for r in kv_before]


def test_recluster_skips_small_corpus():
    import numpy as np
    rng = np.random.default_rng(0)
    s = _FakeStorage()
    for i in range(5):
        s.add(i, "note", rng.normal(0, 1, 384).tolist())
    res = recluster(s, min_notes=30)
    assert res.skipped
    assert "need at least" in res.reason
    assert s.cluster_topics() == []


def test_clear_clusters_empties_table_and_assignments():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s = _two_themes_with_outliers()
        recluster(s, min_notes=10, min_cluster_size=4)
    assert s.cluster_topics()  # has clusters
    s.clear_clusters()
    assert s.cluster_topics() == []
    remaining = s.conn.execute(
        "SELECT COUNT(*) AS n FROM note_meta WHERE cluster_id IS NOT NULL"
    ).fetchone()["n"]
    assert remaining == 0


def test_label_prefers_shared_tag():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s = _two_themes_with_outliers()
        recluster(s, min_notes=10, min_cluster_size=4)
    labels = {t["label"] for t in s.cluster_topics()}
    # Both themes carry a single shared tag, so labels should be those tags.
    assert "#work" in labels and "#fit" in labels
