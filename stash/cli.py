"""stash command line: capture and recall without a bot or web UI."""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

from stash.capture import capture
from stash.embed import SentenceTransformerEmbedder
from stash.recall import search
from stash.reindex import reindex
from stash.capture import repair
from stash.config import load_config
from stash.storage import Storage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv=None, storage=None, embedder=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="stash")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add"); p_add.add_argument("text")
    p_search = sub.add_parser("search"); p_search.add_argument("query")
    sub.add_parser("reindex")
    sub.add_parser("repair")
    sub.add_parser("tags")  # deterministic Topics: tag -> count
    sub.add_parser("serve")  # Telegram poller + reminder scheduler daemon
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        from stash.serve import serve
        asyncio.run(serve(load_config(os.environ)))
        return 0

    cfg = load_config(os.environ) if storage is None or embedder is None else None
    if storage is None:
        storage = Storage.open(cfg.db_path, cfg.busy_timeout_ms)
    if embedder is None:
        embedder = SentenceTransformerEmbedder(cfg.embed_model)

    # Spec 6.11: startup includes crash repair. underived_note_ids() is a
    # cheap query, so when nothing needs healing this adds no model-load
    # cost; repair() only runs (and only then loads the embedder) when a
    # prior crash left a note derived-incomplete.
    if storage.underived_note_ids():
        repair(storage, embedder)

    # Spec 6.5: record which embedding model produced the stored vectors,
    # and reindex when it changes, so stored and query vectors are never
    # drawn from two different vector spaces.
    stored_model = storage.kv_get("embed_model")
    current_model = embedder.name
    if stored_model is None:
        storage.kv_set("embed_model", current_model)
    elif stored_model != current_model:
        reindex(storage, embedder)
        storage.kv_set("embed_model", current_model)

    if args.cmd == "add":
        r = capture(storage, embedder, args.text, "cli", _now())
        print(r.receipt)
        return 0
    if args.cmd == "search":
        for res in search(storage, embedder, args.query):
            tags = (" " + " ".join(f"#{t}" for t in res.tags)) if res.tags else ""
            print(f"[{res.created_at}] {res.raw}{tags}")
        return 0
    if args.cmd == "reindex":
        reindex(storage, embedder); print("reindexed."); return 0
    if args.cmd == "repair":
        n = repair(storage, embedder); print(f"repaired {n} notes."); return 0
    if args.cmd == "tags":
        for r in storage.conn.execute(
            "SELECT tag, COUNT(*) AS n FROM note_tags"
            " GROUP BY tag ORDER BY n DESC, tag"):
            print(f"#{r['tag']} ({r['n']})")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
