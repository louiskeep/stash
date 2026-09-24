"""Recall evaluation: hit-rate@3 and MRR, hybrid vs FTS-only, real encoder."""

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from stash.recall import rrf_fuse
from stash.storage import Storage


@dataclass
class EvalCase:
    query: str
    note: str
    distractors: list[str] = field(default_factory=list)


def _rank_of(target_id: int, ranking: list[int]) -> int | None:
    for i, nid in enumerate(ranking):
        if nid == target_id:
            return i + 1  # 1-based
    return None


def _metrics(ranks: list[int | None]) -> tuple[float, float]:
    n = len(ranks)
    hits3 = sum(1 for r in ranks if r is not None and r <= 3) / n
    mrr = sum((1.0 / r) for r in ranks if r is not None) / n
    return hits3, mrr


def run_eval(cases: list[EvalCase], embedder) -> dict:
    hybrid_ranks: list[int | None] = []
    fts_ranks: list[int | None] = []
    for case in cases:
        with tempfile.TemporaryDirectory() as d:
            s = Storage.open(str(Path(d) / "eval.db"))
            now = datetime(2026, 9, 24, tzinfo=timezone.utc).isoformat()
            target_id = s.add_note(case.note, "import", now)
            s.set_embedding(target_id, embedder.embed(case.note))
            for text in case.distractors:
                did = s.add_note(text, "import", now)
                s.set_embedding(did, embedder.embed(text))
            bm25 = s.search_bm25(case.query, 50)
            vec = s.search_vec(embedder.embed(case.query), 50)
            hybrid_ranks.append(_rank_of(target_id, rrf_fuse([bm25, vec])))
            fts_ranks.append(_rank_of(target_id, bm25))
    h3, hmrr = _metrics(hybrid_ranks)
    f3, fmrr = _metrics(fts_ranks)
    return {
        "n": len(cases),
        "hybrid_hit_rate_at_3": h3,
        "hybrid_mrr": hmrr,
        "fts_only_hit_rate_at_3": f3,
        "fts_only_mrr": fmrr,
    }


def check_regression(result: dict, baseline_path, tolerance: float = 0.0) -> None:
    p = Path(baseline_path)
    if not p.exists():
        raise AssertionError(
            f"no recorded baseline at {p}; record one with record_baseline()")
    base = json.loads(p.read_text())
    for key in ("hybrid_hit_rate_at_3", "hybrid_mrr"):
        if result[key] + tolerance < base[key]:
            raise AssertionError(
                f"recall regression on {key}: {result[key]:.3f} < "
                f"baseline {base[key]:.3f}")


def record_baseline(result: dict, baseline_path) -> None:
    Path(baseline_path).write_text(json.dumps(result, indent=2, sort_keys=True))
