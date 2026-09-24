import json
from pathlib import Path

import pytest

from stash.embed import SentenceTransformerEmbedder
from stash.eval import EvalCase, run_eval, check_regression

FIX = Path(__file__).parent / "fixtures" / "recall_eval.json"
BASELINE = Path(__file__).parent.parent / "stash" / "eval_baseline.json"


def _cases():
    return [EvalCase(**c) for c in json.loads(FIX.read_text())]


@pytest.mark.eval
def test_hybrid_beats_or_meets_recorded_baseline():
    result = run_eval(_cases(), SentenceTransformerEmbedder())
    assert result["n"] == 3
    assert 0.0 <= result["hybrid_hit_rate_at_3"] <= 1.0
    # Hybrid should not do worse than FTS-only on this set.
    assert result["hybrid_hit_rate_at_3"] >= result["fts_only_hit_rate_at_3"]
    check_regression(result, BASELINE)
