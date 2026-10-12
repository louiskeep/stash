"""Task 3: the Topics page shows discovered topics (clusters) when present.

Clusters are inserted directly (no scikit-learn needed) so these run in the
normal web suite.
"""

from datetime import datetime, timezone

from stash.capture import capture
from tests.conftest import login


def _now():
    return datetime.now(timezone.utc).isoformat()


def _add_cluster(storage, label, note_ids):
    storage.conn.execute(
        "INSERT INTO clusters(label, size, updated_at) VALUES (?,?,?)",
        (label, len(note_ids), _now()))
    cid = storage.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    for nid in note_ids:
        storage.conn.execute(
            "UPDATE note_meta SET cluster_id = ? WHERE note_id = ?", (cid, nid))
    storage.conn.commit()
    return cid


async def test_topics_shows_discovered_section_when_clusters_exist(client, storage, embedder):
    n1 = capture(storage, embedder, "invoice from the vendor", "web", _now()).note_id
    n2 = capture(storage, embedder, "pay the vendor invoice", "web", _now()).note_id
    _add_cluster(storage, "vendor invoices", [n1, n2])
    await login(client)
    r = await client.get("/topics")
    assert r.status_code == 200
    assert "Discovered topics" in r.text
    assert "vendor invoices" in r.text


async def test_topics_hides_discovered_section_without_clusters(client, seed):
    await login(client)
    r = await client.get("/topics")
    assert r.status_code == 200
    assert "Discovered topics" not in r.text


async def test_cluster_drilldown_lists_its_notes(client, storage, embedder):
    n1 = capture(storage, embedder, "alpha note", "web", _now()).note_id
    n2 = capture(storage, embedder, "beta note", "web", _now()).note_id
    other = capture(storage, embedder, "unrelated", "web", _now()).note_id
    cid = _add_cluster(storage, "my topic", [n1, n2])
    await login(client)
    r = await client.get(f"/topics/cluster/{cid}")
    assert r.status_code == 200
    assert "alpha note" in r.text and "beta note" in r.text
    assert "unrelated" not in r.text


async def test_cluster_drilldown_404_for_missing(client, seed):
    await login(client)
    assert (await client.get("/topics/cluster/999")).status_code == 404


async def test_cluster_label_is_escaped(client, storage, embedder):
    n1 = capture(storage, embedder, "x", "web", _now()).note_id
    _add_cluster(storage, "<script>alert(1)</script>", [n1])
    await login(client)
    r = await client.get("/topics")
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text
