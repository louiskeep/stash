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

# Below this many notes, HDBSCAN on a personal corpus produces either one blob or
# all-outliers, which is noise. recluster() refuses rather than write that.
DEFAULT_MIN_NOTES = 30
# HDBSCAN's smallest allowed cluster. Small, because personal topics can be few
# notes, but >1 so a single note is never its own "topic".
DEFAULT_MIN_CLUSTER_SIZE = 3


class ReclusterResult:
    """Outcome of a recluster run, for the CLI summary."""

    def __init__(self, skipped: bool, reason: str = "",
                 n_clusters: int = 0, n_clustered: int = 0,
                 n_outliers: int = 0, n_notes: int = 0) -> None:
        self.skipped = skipped
        self.reason = reason
        self.n_clusters = n_clusters
        self.n_clustered = n_clustered
        self.n_outliers = n_outliers
        self.n_notes = n_notes


def _normalize(vectors):
    import numpy as np
    arr = np.asarray(vectors, dtype="float32")
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # a zero vector stays zero rather than dividing by 0
    return arr / norms


def _label_for(raw_texts, tag_lists) -> str:
    """A short, deterministic, non-empty label for one cluster.

    Prefer a tag shared across most of the cluster's notes (an explicit signal
    the user already gave); otherwise the top TF-IDF terms over the notes' raw
    text. Never generative: this is frequency arithmetic over the notes' own
    words. Always returns a non-empty string.
    """
    from collections import Counter

    # 1) A tag common to a plurality of the cluster's notes.
    tag_counts: Counter = Counter()
    for tags in tag_lists:
        tag_counts.update(set(tags))
    if tag_counts:
        tag, n = tag_counts.most_common(1)[0]
        if n >= max(2, len(raw_texts) // 2):
            return "#" + tag

    # 2) Top TF-IDF terms across the cluster.
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer(stop_words="english", max_features=2000,
                              token_pattern=r"[A-Za-z][A-Za-z0-9'-]{1,}")
        tfidf = vec.fit_transform(raw_texts)
        import numpy as np
        scores = np.asarray(tfidf.sum(axis=0)).ravel()
        terms = vec.get_feature_names_out()
        if len(terms):
            top = [terms[i] for i in np.argsort(scores)[::-1][:3]]
            top = [t for t in top if t.strip()]
            if top:
                return " ".join(top)
    except ValueError:
        # Empty vocabulary (e.g. all stop-words / punctuation). Fall through.
        pass

    # 3) Last resort: the most common non-trivial token in the cluster.
    words: Counter = Counter()
    for text in raw_texts:
        for w in text.split():
            w = w.strip(".,!?;:#@()[]\"'").lower()
            if len(w) > 2:
                words[w] += 1
    if words:
        return words.most_common(1)[0][0]
    return "untitled topic"


def recluster(storage, min_notes: int = DEFAULT_MIN_NOTES,
              min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE) -> ReclusterResult:
    """Discover topics over the note embeddings and persist them.

    Raises ClusterUnavailable if scikit-learn is not installed. Returns a
    skipped result (writing nothing) when there are fewer than `min_notes`
    notes. Otherwise runs HDBSCAN, labels each cluster, and persists via
    storage.replace_clusters(). Derived-only and idempotent on unchanged input.
    """
    if not CLUSTER_AVAILABLE:
        raise ClusterUnavailable()

    pairs = storage.all_embeddings()
    n = len(pairs)
    if n < min_notes:
        return ReclusterResult(
            skipped=True, n_notes=n,
            reason=(f"only {n} notes; need at least {min_notes} before "
                    "clustering is meaningful."))

    note_ids = [nid for nid, _ in pairs]
    vectors = [vec for _, vec in pairs]
    normed = _normalize(vectors)

    from sklearn.cluster import HDBSCAN
    # Euclidean on L2-normalized vectors is monotone with cosine distance, which
    # is the sentence-encoder's space. min_cluster_size caps tiny spurious
    # groups; the rest fall out as noise (label -1 = outlier).
    labels = HDBSCAN(min_cluster_size=min_cluster_size,
                     metric="euclidean", copy=True).fit_predict(normed)

    # Group note indices by HDBSCAN label (excluding -1 outliers), in first-seen
    # order so cluster indexing is deterministic for a given partition.
    from collections import OrderedDict
    groups: "OrderedDict[int, list[int]]" = OrderedDict()
    for i, lab in enumerate(labels):
        lab = int(lab)
        if lab == -1:
            continue
        groups.setdefault(lab, []).append(i)

    # Build clusters (label, size) and the note->cluster-index assignment.
    cluster_rows = []
    assignments: dict[int, int] = {}
    for cluster_index, (_, members) in enumerate(groups.items()):
        raw_texts = [storage.get_note(note_ids[i])["raw"] for i in members]
        tag_lists = [[r["tag"] for r in storage.conn.execute(
            "SELECT tag FROM note_tags WHERE note_id = ?", (note_ids[i],))]
            for i in members]
        label = _label_for(raw_texts, tag_lists)
        cluster_rows.append((label, len(members)))
        for i in members:
            assignments[note_ids[i]] = cluster_index

    n_clustered = len(assignments)
    for nid in note_ids:
        assignments.setdefault(nid, None)  # outliers explicit as None

    storage.replace_clusters(cluster_rows, assignments)
    return ReclusterResult(
        skipped=False, n_clusters=len(cluster_rows), n_clustered=n_clustered,
        n_outliers=n - n_clustered, n_notes=n)
