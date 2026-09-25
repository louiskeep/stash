"""Hybrid recall: BM25 + vector, fused with Reciprocal Rank Fusion."""

from dataclasses import dataclass


@dataclass
class Result:
    note_id: int
    raw: str
    source: str
    created_at: str
    tags: list[str]


def rrf_fuse(rankings: list[list[int]], k: int = 60) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda i: scores[i], reverse=True)


def _tags(storage, note_id: int) -> list[str]:
    return [r["tag"] for r in storage.conn.execute(
        "SELECT tag FROM note_tags WHERE note_id=?", (note_id,))]


def search(storage, embedder, query, limit: int = 10, pool: int = 50,
           query_embedding: list[float] | None = None):
    if not query.strip():
        return []
    bm25 = storage.search_bm25(query, pool)
    qvec = query_embedding if query_embedding is not None else embedder.embed(query)
    vec = storage.search_vec(qvec, pool)
    fused = rrf_fuse([bm25, vec])[:limit]
    results = []
    for nid in fused:
        row = storage.get_note(nid)
        if row is None:
            continue
        results.append(Result(
            note_id=nid, raw=row["raw"], source=row["source"],
            created_at=row["created_at"], tags=_tags(storage, nid)))
    return results
