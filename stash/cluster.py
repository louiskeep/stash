"""Topic discovery by clustering note embeddings.

`recluster()` runs HDBSCAN over the stored note vectors, groups semantically
similar notes, labels each group from its own words, and leaves notes that do not
fit a dense group as outliers. It is derived data: it writes only the `clusters`
table and `note_meta.cluster_id`, never the raw notes, reminders, or kv, and it
is fully recomputable. It is never on the capture path.

The heavy dependency (scikit-learn, for HDBSCAN and TF-IDF) is an opt-in extra:
`uv sync --extra cluster`. This module imports cleanly whether or not it is
installed; the scikit-learn import happens inside functions, not at module load,
so the rest of stash is unaffected when the extra is absent.
"""


class ClusterUnavailable(RuntimeError):
    """Raised when clustering is requested but scikit-learn is not installed."""

    HINT = ("clustering needs the optional dependencies; install them with "
            "`uv sync --extra cluster` (or `pip install 'stash[cluster]'`).")

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.HINT)


def _probe() -> bool:
    try:
        import sklearn  # noqa: F401
        return True
    except Exception:
        return False


# Evaluated once at import, but via the function above, so a missing scikit-learn
# is a False here rather than an ImportError at module load.
CLUSTER_AVAILABLE = _probe()
