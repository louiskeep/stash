# Discovered topics (clustering)

stash can discover topics in your notes automatically, grouping ones that are
close in meaning even when you never tagged them. This is optional and off by
default: it needs an extra dependency and only earns its place once you have a
real corpus.

## Install the extra

Clustering uses HDBSCAN and TF-IDF from scikit-learn, which is not part of the
base install:

```bash
uv sync --extra cluster
```

(Or `pip install 'stash[cluster]'`.) The base install, and every other feature,
work without it; only topic discovery needs it.

## Run it

```bash
uv run stash recluster
```

This reads your note embeddings, groups similar notes, labels each group from its
own most distinctive words (or a tag the group shares), and records the result.
It prints a short summary: how many topics it found, how many notes were
clustered, and how many were left as outliers.

Open the web UI's Topics page and you will see a "Discovered topics" section
above your tags and categories. Each topic links to its notes.

## When to run it

Run `recluster` once you have enough notes for topics to be meaningful (stash
uses a floor of 30; below that it declines and tells you so). On a small or very
uneven collection, clustering either lumps everything together or calls
everything an outlier, so it is deliberately a manual step you take when your
corpus is ready, not something that runs on its own.

Rerun it whenever you have added a batch of notes; it recomputes from scratch
each time.

## What to expect

- **Outliers are normal.** Notes that do not fit any dense group stay
  unclustered. That is correct: not every note belongs to a topic.
- **Topics are recomputed, not hand-edited.** `recluster` replaces the previous
  result each run. There is no per-topic rename or merge in this version.
- **Nothing is rewritten.** Clustering is derived data over your embeddings. Your
  raw notes are never touched, and a `stash reindex` clears topics (rerun
  `recluster` to rebuild them).
- **No generative AI.** Topic labels come from word-frequency math over your own
  notes, never from a language model, in keeping with stash's recall-only stance.

## In Docker

The base image does not include the cluster extra. Topic discovery is a
local/opt-in step; run `recluster` in an environment where you installed the
extra, or build a custom image that adds it.
