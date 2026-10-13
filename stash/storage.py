"""Typed storage operations over the stash SQLite schema.

Transactions are short and never span a network call. FTS and vec tables are
kept in sync here on every note write.
"""

import re
import sqlite3
import struct

from stash.db import connect, migrate


def _pack_f32(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack_f32(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_THEMES = ("system", "light", "dark")
_SORTS = ("newest", "oldest")


def _normalize_settings(data: dict) -> dict:
    """Validate and normalize a settings document: drop anything that is not a
    known theme/sort or a valid #rrggbb color, so only clean values are stored
    or served and no unchecked value can reach the page."""
    theme = data.get("theme")
    theme = theme if theme in _THEMES else "system"
    sort = data.get("defaultSort")
    sort = sort if sort in _SORTS else "newest"
    colors_in = data.get("colors")
    colors = {}
    if isinstance(colors_in, dict):
        for key, val in colors_in.items():
            # key is "tag:<name>" or "cat:<name>"; value a #rrggbb hex.
            if (isinstance(key, str) and (key.startswith("tag:") or key.startswith("cat:"))
                    and isinstance(val, str) and _HEX_RE.match(val)):
                colors[key] = val.lower()
    return {"theme": theme, "defaultSort": sort, "colors": colors}


def _fts5_match_expr(query: str) -> str:
    """Turn free-form user input into a safe FTS5 MATCH expression.

    Every whitespace-delimited token is quoted as an FTS5 string literal, so
    punctuation and operator keywords (AND/OR/NOT/*, an unbalanced quote or
    paren, ...) are matched as literal text rather than parsed as syntax.
    """
    tokens = query.split()
    quoted = ['"' + tok.replace('"', '""') + '"' for tok in tokens]
    return " ".join(quoted)


class Storage:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @classmethod
    def open(cls, db_path: str, busy_timeout_ms: int = 5000) -> "Storage":
        conn = connect(db_path, busy_timeout_ms)
        migrate(conn)
        return cls(conn)

    def add_note(self, raw, source, created_at,
                 source_chat_id=None, source_msg_id=None) -> int | None:
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO notes(raw, source, source_chat_id, source_msg_id,"
                    " created_at, derived_at) VALUES (?,?,?,?,?,NULL)",
                    (raw, source, source_chat_id, source_msg_id, created_at),
                )
                return cur.lastrowid
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                return None  # scoped dedupe key already present
            raise

    def get_note(self, note_id) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()

    def get_note_by_source_key(self, source, source_chat_id, source_msg_id) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM notes WHERE source=? AND source_chat_id=? AND source_msg_id=?",
            (source, source_chat_id, source_msg_id),
        ).fetchone()

    def set_tags(self, note_id, tags) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM note_tags WHERE note_id=?", (note_id,))
            self.conn.executemany(
                "INSERT INTO note_tags(note_id, tag) VALUES (?,?)",
                [(note_id, t) for t in tags],
            )

    def set_meta(self, note_id, category, intent, cluster_id=None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO note_meta(note_id, category, intent, cluster_id)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT(note_id) DO UPDATE SET"
                " category=excluded.category, intent=excluded.intent,"
                " cluster_id=excluded.cluster_id",
                (note_id, category, intent, cluster_id),
            )

    def set_embedding(self, note_id, vector) -> None:
        row = self.get_note(note_id)
        with self.conn:
            # FTS external content: insert by rowid = note id. Guard the
            # delete on a prior-index check: fts5's implicit-rowid DELETE
            # reads the *current* backing row to work out which terms to
            # remove, so issuing it before anything was ever indexed for
            # this rowid corrupts the shadow index instead of no-op'ing.
            indexed = self.conn.execute(
                "SELECT 1 FROM notes_fts_docsize WHERE id=?", (note_id,)
            ).fetchone()
            if indexed:
                self.conn.execute("DELETE FROM notes_fts WHERE rowid=?", (note_id,))
            self.conn.execute(
                "INSERT INTO notes_fts(rowid, raw) VALUES (?,?)",
                (note_id, row["raw"]),
            )
            self.conn.execute("DELETE FROM vec_notes WHERE note_id=?", (note_id,))
            self.conn.execute(
                "INSERT INTO vec_notes(note_id, embedding) VALUES (?,?)",
                (note_id, _pack_f32(vector)),
            )

    def mark_derived(self, note_id, when) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE notes SET derived_at=? WHERE id=?", (when, note_id))

    def underived_note_ids(self) -> list[int]:
        return [r["id"] for r in self.conn.execute(
            "SELECT id FROM notes WHERE derived_at IS NULL ORDER BY id")]

    def kv_get(self, key) -> str | None:
        r = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def kv_set(self, key, value) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO kv(key, value) VALUES (?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # --- settings (W7): one JSON document in kv -----------------------------
    # Presentation preferences: theme, default sort, and tag/category colors.
    # Stored under the kv key "settings" (which a reindex preserves). Validated
    # and normalized on save so only clean values are ever stored or served.

    def get_settings(self) -> dict:
        import json
        raw = self.kv_get("settings")
        defaults = {"theme": "system", "defaultSort": "newest", "colors": {}}
        if not raw:
            return defaults
        try:
            data = json.loads(raw)
        except Exception:
            return defaults
        return _normalize_settings(data)

    def save_settings(self, data) -> dict:
        import json
        clean = _normalize_settings(data if isinstance(data, dict) else {})
        self.kv_set("settings", json.dumps(clean))
        return clean

    def search_bm25(self, query, limit) -> list[int]:
        if not query.strip():
            return []
        match_expr = _fts5_match_expr(query)
        if not match_expr:
            return []
        try:
            rows = self.conn.execute(
                "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?"
                " ORDER BY bm25(notes_fts) LIMIT ?",
                (match_expr, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r["rowid"] for r in rows]

    def search_vec(self, vector, limit) -> list[int]:
        rows = self.conn.execute(
            "SELECT note_id FROM vec_notes"
            " WHERE embedding MATCH ? AND k=? ORDER BY distance",
            (_pack_f32(vector), limit),
        ).fetchall()
        return [r["note_id"] for r in rows]

    # --- read-only browse queries (web UI) ---------------------------------
    # Plain SELECTs, no schema change. The web routes stay thin by calling
    # these, and the queries are covered by storage tests independently of HTTP.

    def recent_notes(self, limit, before_id=None) -> list[sqlite3.Row]:
        """Notes newest-first, keyset-paginated by id.

        Keyset (id < before_id) rather than OFFSET so pagination is stable
        under concurrent inserts: a new note arriving between page loads never
        shifts rows across the page boundary. `before_id=None` is the first
        page. The caller may request limit+1 to detect whether an older page
        exists.
        """
        if before_id is None:
            return self.conn.execute(
                "SELECT * FROM notes ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM notes WHERE id < ? ORDER BY id DESC LIMIT ?",
            (before_id, limit),
        ).fetchall()

    def notes_by_tag(self, tag, limit) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT n.* FROM notes n"
            " JOIN note_tags t ON t.note_id = n.id"
            " WHERE t.tag = ? ORDER BY n.id DESC LIMIT ?",
            (tag, limit),
        ).fetchall()

    def notes_by_category(self, category, limit) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT n.* FROM notes n"
            " JOIN note_meta m ON m.note_id = n.id"
            " WHERE m.category = ? ORDER BY n.id DESC LIMIT ?",
            (category, limit),
        ).fetchall()

    def tag_counts(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT tag, COUNT(*) AS n FROM note_tags"
            " GROUP BY tag ORDER BY n DESC, tag"
        ).fetchall()

    def category_counts(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT category, COUNT(*) AS n FROM note_meta"
            " WHERE category IS NOT NULL AND category != ''"
            " GROUP BY category ORDER BY n DESC, category"
        ).fetchall()

    def related_by_embedding(self, note_id, limit) -> list[sqlite3.Row]:
        """Nearest notes to this note's own stored embedding, excluding itself.

        Reads the note's vector from vec_notes and runs a KNN for limit+1, then
        drops the note itself (always its own nearest neighbor). Returns [] when
        the note has no stored embedding yet (e.g. still underived).
        """
        vrow = self.conn.execute(
            "SELECT embedding FROM vec_notes WHERE note_id = ?", (note_id,)
        ).fetchone()
        if vrow is None:
            return []
        hits = self.conn.execute(
            "SELECT note_id FROM vec_notes"
            " WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (vrow["embedding"], limit + 1),
        ).fetchall()
        ids = [r["note_id"] for r in hits if r["note_id"] != note_id][:limit]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT * FROM notes WHERE id IN ({placeholders})", ids
        ).fetchall()
        # Preserve nearest-first order from the KNN, which the IN (...) query loses.
        by_id = {r["id"]: r for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def note_detail(self, note_id) -> dict | None:
        """A note with its tags, meta, and reminder, assembled for the detail page.

        Returns a plain dict (not a Row) so the template sees a single object
        carrying raw/created_at/source plus category, intent, tags, and an
        optional reminder sub-dict. None when the note does not exist.
        """
        note = self.get_note(note_id)
        if note is None:
            return None
        meta = self.conn.execute(
            "SELECT category, intent FROM note_meta WHERE note_id = ?", (note_id,)
        ).fetchone()
        tags = [r["tag"] for r in self.conn.execute(
            "SELECT tag FROM note_tags WHERE note_id = ? ORDER BY tag", (note_id,))]
        reminder = self.conn.execute(
            "SELECT status, fire_at, sent_at, attempts FROM reminders WHERE note_id = ?",
            (note_id,),
        ).fetchone()
        return {
            "id": note["id"],
            "raw": note["raw"],
            "source": note["source"],
            "created_at": note["created_at"],
            "category": meta["category"] if meta else None,
            "intent": meta["intent"] if meta else None,
            "tags": tags,
            "reminder": dict(reminder) if reminder else None,
        }

    def list_reminders(self, limit) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT r.note_id, r.status, r.fire_at, r.sent_at, r.attempts, n.raw"
            " FROM reminders r JOIN notes n ON n.id = r.note_id"
            " ORDER BY r.fire_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    # --- clustering (W2): derived topic discovery -------------------------
    # recluster() in stash/cluster.py reads all_embeddings(), computes groups,
    # and persists them with replace_clusters(). The Topics page reads
    # cluster_topics() and notes_by_cluster(). All derived: a reindex rebuilds.

    def all_embeddings(self) -> list[tuple[int, list[float]]]:
        """Every (note_id, vector) in vec_notes, ordered by note_id ascending so
        the clustering input is deterministic. The float32 blob is unpacked back
        to a list of floats."""
        rows = self.conn.execute(
            "SELECT note_id, embedding FROM vec_notes ORDER BY note_id"
        ).fetchall()
        return [(r["note_id"], _unpack_f32(r["embedding"])) for r in rows]

    def replace_clusters(self, clusters, assignments) -> None:
        """Atomically replace the whole clustering.

        `clusters` is a list of (label, size) in cluster-index order; the row's
        1-based rowid becomes its cluster id. `assignments` maps note_id to a
        cluster index (0-based) or None for an outlier. In one transaction:
        clear every old cluster and cluster_id, insert the new clusters, and set
        cluster_id on assigned notes. Touches only `clusters` and
        `note_meta.cluster_id`; raw notes, reminders, and kv are untouched.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute("DELETE FROM clusters")
            self.conn.execute("UPDATE note_meta SET cluster_id = NULL")
            # Insert clusters; capture each new id in index order.
            index_to_id: dict[int, int] = {}
            for idx, (label, size) in enumerate(clusters):
                cur = self.conn.execute(
                    "INSERT INTO clusters(label, size, updated_at) VALUES (?,?,?)",
                    (label, size, now),
                )
                index_to_id[idx] = cur.lastrowid
            for note_id, idx in assignments.items():
                if idx is None:
                    continue  # outlier: leave cluster_id NULL
                # note_meta row exists for every derived note; guard anyway so a
                # not-yet-derived note cannot break the whole recluster.
                self.conn.execute(
                    "UPDATE note_meta SET cluster_id = ? WHERE note_id = ?",
                    (index_to_id[idx], note_id),
                )

    def clear_clusters(self) -> None:
        """Drop all clusters and cluster assignments. Used by reindex so no
        stale cluster rows survive a rebuild (clusters are recomputed by
        recluster, not reindex)."""
        with self.conn:
            self.conn.execute("DELETE FROM clusters")
            self.conn.execute("UPDATE note_meta SET cluster_id = NULL")

    def cluster_topics(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, label, size FROM clusters ORDER BY size DESC, id"
        ).fetchall()

    def notes_by_cluster(self, cluster_id, limit) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT n.* FROM notes n"
            " JOIN note_meta m ON m.note_id = n.id"
            " WHERE m.cluster_id = ? ORDER BY n.id DESC LIMIT ?",
            (cluster_id, limit),
        ).fetchall()

    def cluster_label(self, cluster_id) -> str | None:
        r = self.conn.execute(
            "SELECT label FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
        return r["label"] if r else None

    # --- board (W4): the user's spatial note arrangement -------------------
    # board_layout and board_piles are durable state (reindex leaves them),
    # written by the user dragging and stacking notes on the canvas.

    def board_notes(self) -> list[dict]:
        """Every note with the fields the board needs: raw, created_at, source,
        its tags and category, a pending reminder's fire_at (the timer), and its
        merged-group membership (pile_id + seq) when it is part of a merged card.
        Returns plain dicts so the JSON endpoint can serialize them directly; note
        text is a data value, never interpolated into HTML."""
        notes = self.conn.execute(
            "SELECT n.id, n.raw, n.source, n.created_at,"
            "       m.category,"
            "       b.pile_id, b.seq,"
            "       r.fire_at AS timer, r.status AS timer_status"
            " FROM notes n"
            " LEFT JOIN note_meta m ON m.note_id = n.id"
            " LEFT JOIN board_layout b ON b.note_id = n.id"
            " LEFT JOIN reminders r ON r.note_id = n.id AND r.status = 'pending'"
            " ORDER BY n.id DESC"
        ).fetchall()
        tags_by_note: dict[int, list[str]] = {}
        for row in self.conn.execute("SELECT note_id, tag FROM note_tags ORDER BY tag"):
            tags_by_note.setdefault(row["note_id"], []).append(row["tag"])
        out = []
        for n in notes:
            out.append({
                "id": n["id"],
                "raw": n["raw"],
                "source": n["source"],
                "created_at": n["created_at"],
                "category": n["category"],
                "tags": tags_by_note.get(n["id"], []),
                "timer": n["timer"] if n["timer_status"] == "pending" else None,
                "pile_id": n["pile_id"],
                "seq": n["seq"] if n["seq"] is not None else 0,
            })
        return out

    def save_groups(self, groups) -> int:
        """Replace the merged-note groups. `groups` is a list of ordered note-id
        lists; a list of two or more notes becomes one merged card, in order
        (index 0 = the top segment). Notes not listed, or alone in a list, are
        standalone cards. Rebuilds board_piles and board_layout.pile_id/seq in one
        transaction, and returns the number of groups written.

        A merge is a grouping, never a rewrite: each note's raw stays its own
        untouched row, so a merged card is fully reversible by snipping. This is
        durable state (like saved boards); a reindex never touches it. Unknown
        note ids are ignored rather than raising."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        written = 0
        with self.conn:
            # Detach every note first, then rebuild the groups. Deleting a group
            # row SET NULLs its members via FK, but we clear explicitly so a
            # removed group leaves no dangling reference even mid-rebuild.
            self.conn.execute("UPDATE board_layout SET pile_id = NULL, seq = 0")
            self.conn.execute("DELETE FROM board_piles")
            for group in groups:
                if not isinstance(group, (list, tuple)):
                    continue
                ids: list[int] = []
                for raw_id in group:
                    try:
                        nid = int(raw_id)
                    except (TypeError, ValueError):
                        continue
                    if self.get_note(nid) is not None and nid not in ids:
                        ids.append(nid)
                if len(ids) < 2:
                    continue  # a group needs at least two real notes
                cur = self.conn.execute(
                    "INSERT INTO board_piles(label, collapsed, updated_at)"
                    " VALUES ('', 0, ?)",
                    (now,),
                )
                pid = cur.lastrowid
                for seq, nid in enumerate(ids):
                    self.conn.execute(
                        "INSERT INTO board_layout(note_id, pile_id, seq, updated_at)"
                        " VALUES (?,?,?,?)"
                        " ON CONFLICT(note_id) DO UPDATE SET"
                        " pile_id=excluded.pile_id, seq=excluded.seq,"
                        " updated_at=excluded.updated_at",
                        (nid, pid, seq, now),
                    )
                written += 1
        return written

    def delete_note(self, note_id) -> bool:
        """Delete a note and everything derived or attached to it (tags, meta,
        fts, vec, reminder, board layout) via FK cascade and explicit cleanup for
        the vec/fts virtual tables, which do not honor FKs. The one destructive
        board action, and only when the user asks for it. Returns whether a row
        was deleted."""
        with self.conn:
            # Virtual tables (fts5 external-content, vec0) are not covered by FK
            # cascade; remove their rows explicitly first.
            indexed = self.conn.execute(
                "SELECT 1 FROM notes_fts_docsize WHERE id=?", (note_id,)
            ).fetchone()
            if indexed:
                self.conn.execute("DELETE FROM notes_fts WHERE rowid=?", (note_id,))
            self.conn.execute("DELETE FROM vec_notes WHERE note_id=?", (note_id,))
            cur = self.conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
            return cur.rowcount == 1

    # --- saved boards (W6): named filter sets --------------------------------
    # Durable state like board_layout; a reindex never touches it. `config` is a
    # JSON string stored and returned as-is; the server does not interpret it.

    def list_boards(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, name, config FROM boards"
            " ORDER BY position IS NULL, position, id"
        ).fetchall()

    def save_board(self, name, config_json) -> int:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO boards(name, config, created_at) VALUES (?,?,?)",
                (name, config_json, now),
            )
            return cur.lastrowid

    def delete_board(self, board_id) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM boards WHERE id=?", (board_id,))
            return cur.rowcount == 1

    def claim_one_due_reminder(self, now, lease_seconds, max_attempts, allowed_ids):
        # Claim exactly one reminder per call: pending, due, under the
        # attempt cap, and either never claimed or its lease has expired.
        # One-at-a-time (vs. M1's batch claim) closes the window where a
        # tick could hold several already-claimed-but-not-yet-sent rows
        # while a nested/concurrent tick reclaims and delivers them out
        # from under it -- the next row is only claimed once the current
        # one is fully resolved (sent or deferred). Does NOT increment
        # attempts; that happens at send time via record_attempt.
        # Only claims rows whose chat_id is in the CURRENT allow-list
        # (`allowed_ids`), not whatever allow-list existed when the
        # reminder was created: a sender removed from
        # STASH_ALLOWED_SENDER_IDS after their reminder was captured must
        # never receive it. A NULL chat_id (e.g. a CLI-captured note with
        # no delivery target) can never match an IN (...) list, so it stays
        # pending indefinitely rather than being claimed, failed to
        # deliver, and churned to 'failed' -- the old explicit "IS NOT
        # NULL" guard is subsumed by this membership check.
        if not allowed_ids:
            return None  # nobody currently allowed; an empty IN () is invalid SQL anyway
        from datetime import datetime, timedelta
        cutoff = (datetime.fromisoformat(now)
                  - timedelta(seconds=lease_seconds)).isoformat()
        placeholders = ",".join("?" for _ in allowed_ids)
        with self.conn:
            return self.conn.execute(
                "UPDATE reminders SET claimed_at=?"
                " WHERE id = ("
                "   SELECT id FROM reminders"
                "   WHERE status='pending' AND fire_at<=? AND attempts<?"
                "     AND (claimed_at IS NULL OR claimed_at<?)"
                f"     AND chat_id IN ({placeholders})"
                "   ORDER BY fire_at, id LIMIT 1)"
                " RETURNING *",
                (now, now, max_attempts, cutoff, *allowed_ids),
            ).fetchone()

    def record_attempt(self, rid, expected_claimed_at) -> bool:
        # Count the attempt durably, ownership-checked, BEFORE the caller
        # awaits delivery.send. This is what bounds a crash-loop: even if
        # the process dies mid-send and never reaches defer_reminder, the
        # attempt is already committed, so a later tick's reclaim continues
        # counting toward max_attempts instead of restarting from zero.
        with self.conn:
            cur = self.conn.execute(
                "UPDATE reminders SET attempts=attempts+1"
                " WHERE id=? AND claimed_at=?",
                (rid, expected_claimed_at))
            return cur.rowcount == 1

    def defer_reminder(self, rid, now, max_attempts, expected_claimed_at) -> None:
        # On a failed send: CAS on claimed_at (a stale tick's defer must not
        # clobber a row another tick has since reclaimed or completed). If
        # the row is now at the attempt cap, terminalize it. Otherwise leave
        # it LEASED (claimed_at=now) rather than releasing it outright --
        # that fresh lease window doubles as the retry backoff, so the
        # current tick's own drain loop (and any tick before the lease
        # elapses) will not immediately re-claim and re-attempt it. This is
        # what stops a single-tick outage from burning through the whole
        # attempt cap in one pass.
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET"
                " status=CASE WHEN attempts>=? THEN 'failed' ELSE 'pending' END,"
                " claimed_at=CASE WHEN attempts>=? THEN NULL ELSE ? END"
                " WHERE id=? AND claimed_at=?",
                (max_attempts, max_attempts, now, rid, expected_claimed_at))

    def fail_exhausted_reminders(self, now, max_attempts, lease_seconds):
        # Terminalize reminders that hit the attempt cap while still
        # 'pending' (e.g. a tick that recorded the final attempt but
        # crashed/hung before calling defer_reminder), so they stop
        # lingering as reclaimable forever once claim_one_due_reminder
        # starts excluding attempts>=cap.
        #
        # Lease-aware, mirroring claim_one_due_reminder's own guard: a row whose
        # lease is still live is owned by some tick that may be mid-send on
        # its final attempt. Without this guard, calling this at the start
        # of a concurrent tick could terminalize that row out from under its
        # owner -- flipping it to 'failed' and clearing claimed_at -- so the
        # owner's later mark_reminder_sent CAS (WHERE claimed_at=<token>) no
        # longer matches and silently no-ops, leaving a delivered reminder
        # recorded as 'failed'. Only rows that are unowned (never claimed)
        # or whose lease has actually expired are fair game here.
        from datetime import datetime, timedelta
        cutoff = (datetime.fromisoformat(now)
                  - timedelta(seconds=lease_seconds)).isoformat()
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET status='failed', claimed_at=NULL"
                " WHERE status='pending' AND fire_at<=? AND attempts>=?"
                "   AND (claimed_at IS NULL OR claimed_at<?)",
                (now, max_attempts, cutoff),
            )

    def mark_reminder_sent(self, rid, when, expected_claimed_at) -> bool:
        # Lease-owned compare-and-swap: only complete the row if this tick's
        # claim (expected_claimed_at) still holds. A tick whose claim was
        # superseded by a later reclaim updates 0 rows and is a no-op,
        # instead of clobbering a newer tick's completed 'sent' row. Returns
        # whether the CAS applied, so a caller can tell a real completion
        # from a no-op.
        with self.conn:
            cur = self.conn.execute(
                "UPDATE reminders SET status='sent', sent_at=?, claimed_at=NULL"
                " WHERE id=? AND claimed_at=?", (when, rid, expected_claimed_at))
            return cur.rowcount == 1
