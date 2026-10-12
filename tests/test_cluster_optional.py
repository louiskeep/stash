"""Task 0: stash.cluster imports with or without scikit-learn."""

import os

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore


def test_cluster_module_imports_regardless():
    # The import must not fail when scikit-learn is absent; the probe is inside
    # a function, not at module top.
    import stash.cluster as c
    assert isinstance(c.CLUSTER_AVAILABLE, bool)


def test_cluster_extra_declared():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), "rb") as f:
        data = tomllib.load(f)
    extra = data["project"]["optional-dependencies"]["cluster"]
    assert any("scikit-learn" in dep for dep in extra)


def test_no_top_level_sklearn_import():
    # Reading the source, scikit-learn must not be imported at module scope.
    import stash.cluster as c
    src = open(c.__file__, encoding="utf-8").read()
    # Any sklearn import must be indented (inside a function), never at col 0.
    for line in src.splitlines():
        if line.startswith("import sklearn") or line.startswith("from sklearn"):
            raise AssertionError("scikit-learn imported at module scope")


def test_cluster_unavailable_carries_hint():
    from stash.cluster import ClusterUnavailable
    err = ClusterUnavailable()
    assert "uv sync --extra cluster" in str(err)
