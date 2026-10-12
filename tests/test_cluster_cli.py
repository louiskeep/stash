"""Task 2: the `stash recluster` CLI command and reindex cluster clearing.

These stub the clusterer so they run without scikit-learn, exercising the CLI's
summary, skip, and unavailable paths and the exit codes.
"""

import stash.cli as cli
import stash.cluster as cluster
from stash.cluster import ReclusterResult, ClusterUnavailable


class _Storage:
    """Minimal storage: recluster is stubbed, so the command only needs an
    object to pass through. reindex clearing is tested against real Storage."""


def test_recluster_cli_summary(monkeypatch, capsys):
    def fake(storage, **kw):
        return ReclusterResult(skipped=False, n_clusters=3, n_clustered=40,
                               n_outliers=5, n_notes=45)
    monkeypatch.setattr(cluster, "recluster", fake)
    rc = cli.main(["recluster"], storage=_Storage(), embedder=object())
    assert rc == 0
    out = capsys.readouterr().out
    assert "3 topics" in out and "5 outliers" in out


def test_recluster_cli_skip(monkeypatch, capsys):
    def fake(storage, **kw):
        return ReclusterResult(skipped=True, reason="only 4 notes; need at least 30.")
    monkeypatch.setattr(cluster, "recluster", fake)
    rc = cli.main(["recluster"], storage=_Storage(), embedder=object())
    assert rc == 0
    assert "skipped" in capsys.readouterr().out


def test_recluster_cli_unavailable(monkeypatch, capsys):
    def fake(storage, **kw):
        raise ClusterUnavailable()
    monkeypatch.setattr(cluster, "recluster", fake)
    rc = cli.main(["recluster"], storage=_Storage(), embedder=object())
    assert rc == 1
    assert "uv sync --extra cluster" in capsys.readouterr().err
