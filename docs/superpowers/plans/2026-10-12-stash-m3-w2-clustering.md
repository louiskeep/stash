# stash Milestone 3 / W2 (Clustering) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover topics from the notes themselves. `recluster()` runs HDBSCAN
over the stored note embeddings, groups semantically similar notes into clusters,
labels each cluster from its own content, and leaves notes that do not fit any
dense group as outliers. The Topics page shows these discovered topics above the
deterministic `#tag` / `@category` grouping. Clustering is a real feature, built
and tested; only its heavy dependency is opt-in.

**Architecture:** A new `stash/cluster.py` with `recluster(storage)` that reads
all embeddings from `vec_notes`, runs HDBSCAN, writes `clusters` rows and
`note_meta.cluster_id` in one transaction, and labels each cluster from its notes'
top TF-IDF terms (falling back to most-common tags). It is pure derived data: it
never touches raw notes, reminders, or kv, and is fully recomputable. A `stash
recluster` CLI command drives it. The Topics page reads a new
`storage.cluster_topics()` and shows a "Discovered topics" section when clusters
exist. HDBSCAN + scikit-learn are an optional `cluster` extra; without them,
`recluster` degrades with a clear message and the rest of stash is unaffected.

**Tech Stack:** Python >=3.11, `scikit-learn>=1.3` (its `sklearn.cluster.HDBSCAN`
and `TfidfVectorizer`), numpy (transitive), stdlib `sqlite3`. scikit-learn is an
optional dependency; the core install does not carry it.

**Spec:** `docs/superpowers/specs/2026-09-24-stash-design.md` (v2), section 6.7
(Topics), section 7 (ML inventory), section 10 (clustering acceptance criteria:
stability on unchanged input, non-empty labels, outliers allowed).

## Global Constraints

- **Clustering is derived data.** `recluster()` writes only `clusters` and
  `note_meta.cluster_id`. It never mutates `notes.raw`, `reminders`, or `kv`, and
  a `reindex` can drop and (on the next `recluster`) rebuild it. The raw note
  stays sacred.
- **Outliers are first-class.** HDBSCAN labels points it cannot place as noise
  (`-1`); those notes keep `cluster_id = NULL`. Not every note must belong to a
  cluster; forcing membership would be wrong.
- **Clustering never blocks capture.** It is a separate, explicit operation
  (`stash recluster`), never on the capture path. Capture, recall, reminders, and
  the rest of the web UI do not depend on it and are unchanged.
- **Small-corpus guard.** Below a minimum note count, `recluster()` does nothing
  but say so: HDBSCAN on a tiny, uneven personal corpus produces either one blob
  or all-outliers, which is noise, not signal. The guard is honest, not a silent
  no-op.
- **The heavy dependency is opt-in, the feature is not.** scikit-learn lives in a
  `[project.optional-dependencies] cluster` extra. The feature itself is fully
  implemented and tested. Without the extra installed, `recluster` prints how to
  install it and exits non-zero; the base install and every other feature are
  unaffected.
- **Deterministic labels, no generative text.** Labels come from TF-IDF keywords
  or tag frequencies (arithmetic over the notes' own words), never from an LLM.
  This holds the product's no-generative-AI line.
- **No em-dashes in prose, comments, or docs.**

## Review Focus

- **Derived-only:** a test asserts `recluster()` leaves `notes`, `reminders`, and
  `kv` byte-for-byte unchanged, and only `clusters` / `note_meta.cluster_id`
  move.
- **Outliers allowed:** a corpus with clear outliers yields some
  `cluster_id = NULL` notes rather than everything assigned.
- **Idempotence / recomputability:** running `recluster()` twice on an unchanged
  corpus yields the same partition (same groups, same outliers), and a `reindex`
  then `recluster` reproduces it. (Cluster *ids* may renumber; the grouping is
  what is stable.)
- **Small-corpus guard:** below the threshold, no `clusters` rows are written and
  the caller is told why.
- **Optional dependency:** with scikit-learn absent, `recluster` does not import
  it at module load (so the app still imports), and the command exits with a
  clear install hint; present, it clusters.
- **Labels non-empty:** every written cluster has a non-empty label.
- **Topics page:** shows discovered topics when clusters exist, and is identical
  to today when they do not.

## New surface

- `stash/cluster.py`: `recluster(storage, min_notes=MIN) -> ReclusterResult`
  (counts: clustered, outliers, clusters) and label helpers.
- `storage` reads: `cluster_topics()` (cluster id, label, size, ordered),
  `notes_by_cluster(cluster_id, limit)`; writes: `replace_clusters(rows,
  assignments)` (one transaction: clear old clusters + cluster_ids, insert new).
- `stash recluster` CLI command.
- Topics page: a "Discovered topics" section + `/topics/cluster/{id}` drill-down.

---

### Task 0: Optional `cluster` extra and a capability probe

**Files:**
- Modify: `pyproject.toml`
- Create: `stash/cluster.py` (skeleton: `CLUSTER_AVAILABLE`, import probe)
- Test: `tests/test_cluster_optional.py`

**Interfaces:**
- Produces: `[project.optional-dependencies] cluster = ["scikit-learn>=1.3"]`; a
  `stash.cluster` module that imports with or without scikit-learn and exposes a
  boolean `CLUSTER_AVAILABLE`.

- [ ] **Step 1:** Add the `cluster` extra. Bump `requires-python` to `>=3.11`
      (already effectively required by the W3 test tooling and tomllib; confirm
      nothing in the core needs 3.10).
- [ ] **Step 2:** `stash/cluster.py` probes for scikit-learn inside a function
      (not at module top), so importing `stash.cluster` never fails when the
      extra is absent. `CLUSTER_AVAILABLE` reflects the probe.
- [ ] **Step 3:** Test that `import stash.cluster` succeeds regardless, and that
      `CLUSTER_AVAILABLE` is a bool.

**Acceptance:** the module imports with the extra absent; the extra is declared;
no top-level scikit-learn import.

---

### Task 1: `recluster()` core (HDBSCAN + labels)

**Files:**
- Modify: `stash/cluster.py`, `stash/storage.py`
- Test: `tests/test_cluster.py` (marked to skip when scikit-learn is absent)

**Interfaces:**
- Produces: `recluster(storage, min_notes=MIN) -> ReclusterResult`; storage
  `all_embeddings() -> [(note_id, vector)]` and `replace_clusters(labels,
  assignments)`.
- Consumes: `vec_notes`, `note_tags`, `notes.raw`.

- [ ] **Step 1:** `storage.all_embeddings()` reads every `(note_id, embedding)`
      from `vec_notes`, unpacking the float32 blob back to a vector (mirror of
      `_pack_f32`).
- [ ] **Step 2:** `recluster()`: if not `CLUSTER_AVAILABLE`, raise a typed
      `ClusterUnavailable` with the install hint. Load embeddings; if fewer than
      `min_notes`, return a result flagged `skipped` without writing. Else run
      `sklearn.cluster.HDBSCAN` (cosine-appropriate: L2-normalize vectors, then
      Euclidean, since the encoder is cosine-space), map labels (`-1` = outlier
      -> `cluster_id` NULL).
- [ ] **Step 3:** Label each non-outlier cluster: `TfidfVectorizer` over the raw
      text of its notes, take the top terms; if a cluster's notes share common
      `#tags`, prefer those. Guarantee a non-empty label (fall back to the single
      most frequent token).
- [ ] **Step 4:** `storage.replace_clusters(...)`: in one transaction, delete all
      `clusters` rows, null every `note_meta.cluster_id`, insert the new
      `clusters` (label, size, updated_at), and set `cluster_id` on assigned
      notes. Derived-only: no other table touched.

**Acceptance (scikit-learn present):** on a seeded corpus with two clear themes
plus an outlier, `recluster()` produces two clusters with non-empty labels and
leaves the outlier `cluster_id` NULL; running it twice gives the same grouping;
`notes`/`reminders`/`kv` are unchanged.

---

### Task 2: `stash recluster` CLI

**Files:**
- Modify: `stash/cli.py`, `stash/reindex.py`
- Test: `tests/test_cluster_cli.py`

**Interfaces:**
- Produces: a `recluster` subcommand that opens storage, runs `recluster()`,
  prints a short summary (N clusters, M clustered, K outliers) or the skip/
  install message; `reindex` also clears `clusters`.

- [ ] **Step 1:** Wire `recluster` into `cli.py`. On `ClusterUnavailable`, print
      the install hint (`uv sync --extra cluster`) and return nonzero; on a skip
      (small corpus), print why and return 0; on success, print the summary.
- [ ] **Step 2:** `reindex()` also clears the `clusters` table (it already nulls
      `cluster_id` via the `note_meta` delete), so a reindex leaves no stale
      cluster rows pointing at rebuilt notes; document that clusters are
      recomputed by `recluster`, not `reindex`.
- [ ] **Step 3:** Test the CLI summary path (with a fake/stubbed clusterer so the
      test does not require scikit-learn) and the unavailable path (exit nonzero
      + hint).

**Acceptance:** `stash recluster` prints a summary when it runs, a clear install
hint and nonzero exit when the extra is missing, and a skip message on a small
corpus; `reindex` leaves no stale `clusters` rows.

---

### Task 3: Topics page shows discovered topics

**Files:**
- Modify: `stash/web.py`, `stash/storage.py`, `stash/templates/topics.html`,
  `stash/templates/topic_list.html`
- Test: `tests/test_web_clusters.py`

**Interfaces:**
- Produces: `storage.cluster_topics()` and `notes_by_cluster()`; the Topics page
  renders a "Discovered topics" section when clusters exist; a
  `/topics/cluster/{id}` drill-down lists that cluster's notes.

- [ ] **Step 1:** `storage.cluster_topics()` returns clusters (id, label, size)
      ordered by size desc; `notes_by_cluster(id, limit)` joins `note_meta`.
- [ ] **Step 2:** `topics_page` passes `clusters` to the template; `topics.html`
      shows a "Discovered topics" section above Tags/Categories only when
      `clusters` is non-empty (so the page is byte-identical to today when
      nothing has been clustered).
- [ ] **Step 3:** `/topics/cluster/{id}` route + handler, reusing
      `topic_list.html` with the cluster label.
- [ ] **Step 4:** Tests (async httpx, no scikit-learn needed: insert `clusters`
      rows and `cluster_id`s directly): the section appears with clusters and is
      absent without; the drill-down lists the right notes; the cluster label and
      note text are autoescaped.

**Acceptance:** with clusters present the Topics page shows them and the
drill-down works; with none, the page matches the pre-W2 render; cluster labels
are escaped.

---

### Task 4: Docs

**Files:**
- Modify: `README.md`, `docs/guides/web-setup.md`, `.env.example` (if a tunable
  lands), and a short note in `docs/guides/` on clustering.

- [ ] **Step 1:** Document `uv sync --extra cluster` + `stash recluster`, when to
      run it (once you have a real corpus), and that outliers are expected.
- [ ] **Step 2:** Note the Docker angle: the base image does not include the
      cluster extra; clustering is a local/opt-in step (or a documented build arg
      if we choose to support it, out of scope otherwise).

**Acceptance:** a reader can install the extra, run `stash recluster`, and see
discovered topics, from the docs alone.

---

## VERIFY / GATE

- Suite green. Cluster-algorithm tests are marked and skip cleanly when
  scikit-learn is absent (as the real-encoder eval already does for its model);
  everything else (CLI summary, storage writes via stubbed assignments, the web
  section, the optional-import probe) runs without the extra.
- Spec section 10 clustering criteria exercised: stability on unchanged input,
  non-empty labels, outliers allowed.
- Independent review at the gate (R2): derived-only writes, outlier handling,
  idempotence, the optional-dependency boundary, and no generative text in
  labels.
- Branch: `feat/m3-w2-clustering` (off `feat/m3-w3-packaging` so it has the
  packaging + extras machinery). On GO, move the W2 entry to
  `docs/RECENTLY-SHIPPED.md` and tick W2 in `ROADMAP.md`, completing Milestone 3.

**Verification note:** this build environment has no PyPI access, so scikit-learn
cannot be installed here. The algorithm path (`recluster()` with real HDBSCAN) is
written against the documented scikit-learn API and must be run in an environment
with the `cluster` extra installed before merge. Everything not needing the extra
(the optional-import boundary, storage writes/reads, the CLI summary and
unavailable paths with a stubbed clusterer, the web section) is verified
in-session.

## Explicitly not in W2

- Auto-reclustering in the serve loop (manual `stash recluster` only, per the
  chosen activation). A scheduled recluster can be a later follow-up.
- Per-cluster rename/merge UI, or editing assignments. Clusters are recomputed,
  not hand-curated, in this slice.
- Any change to capture, recall, reminders, or Telegram.

---
cam
