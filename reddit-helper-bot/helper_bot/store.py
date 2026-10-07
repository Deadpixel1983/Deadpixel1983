"""SQLite-backed record of every post the bot has looked at."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# Lifecycle of a post in the store.
SKIPPED = "skipped"      # looked at, decided not to answer
PENDING = "pending"      # draft written, waiting for human approval
APPROVED = "approved"    # human approved, waiting to be posted
POSTED = "posted"        # comment is live on Reddit
REJECTED = "rejected"    # human rejected the draft
FAILED = "failed"        # posting failed; see reason

STATUSES = {SKIPPED, PENDING, APPROVED, POSTED, REJECTED, FAILED}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id          TEXT PRIMARY KEY,
    niche       TEXT NOT NULL DEFAULT '',
    account     TEXT NOT NULL DEFAULT 'default',
    subreddit   TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    status      TEXT NOT NULL,
    draft       TEXT,
    reason      TEXT,
    comment_id  TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS posts_niche ON posts(niche);

CREATE TABLE IF NOT EXISTS comments_log (
    comment_id  TEXT PRIMARY KEY,
    post_id     TEXT NOT NULL,
    account     TEXT NOT NULL DEFAULT 'default',
    posted_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS comments_log_account ON comments_log(account, posted_at);
"""


@dataclass
class Record:
    id: str
    niche: str
    account: str
    subreddit: str
    title: str
    url: str
    status: str
    draft: str | None
    reason: str | None
    comment_id: str | None
    created_at: float
    updated_at: float


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- reads -------------------------------------------------------------

    def has_seen(self, post_id: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone()
        return row is not None

    def get(self, post_id: str) -> Record | None:
        row = self._conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        return _to_record(row) if row else None

    def list_by_status(self, status: str, *, niche: str | None = None) -> list[Record]:
        sql = "SELECT * FROM posts WHERE status = ?"
        params: list = [status]
        if niche is not None:
            sql += " AND niche = ?"
            params.append(niche)
        rows = self._conn.execute(sql + " ORDER BY created_at ASC", params).fetchall()
        return [_to_record(r) for r in rows]

    def counts(self, *, niche: str | None = None) -> dict[str, int]:
        sql = "SELECT status, COUNT(*) AS n FROM posts"
        params: list = []
        if niche is not None:
            sql += " WHERE niche = ?"
            params.append(niche)
        rows = self._conn.execute(sql + " GROUP BY status", params).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def counts_by_niche(self) -> dict[str, dict[str, int]]:
        rows = self._conn.execute("SELECT niche, status, COUNT(*) AS n FROM posts GROUP BY niche, status").fetchall()
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["niche"], {})[r["status"]] = r["n"]
        return out

    def comments_since(self, since: float, *, account: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM comments_log WHERE account = ? AND posted_at >= ?", (account, since)
        ).fetchone()
        return int(row["n"])

    def last_comment_time(self, *, account: str) -> float | None:
        row = self._conn.execute("SELECT MAX(posted_at) AS t FROM comments_log WHERE account = ?", (account,)).fetchone()
        return float(row["t"]) if row and row["t"] is not None else None

    # -- writes ------------------------------------------------------------

    def record_skip(self, post_id: str, subreddit: str, title: str, url: str, reason: str, *, niche: str = "", account: str = "default") -> None:
        self._upsert(post_id, niche, account, subreddit, title, url, SKIPPED, draft=None, reason=reason)

    def record_draft(self, post_id: str, subreddit: str, title: str, url: str, draft: str, *, niche: str = "", account: str = "default", status: str = PENDING) -> None:
        if status not in {PENDING, APPROVED}:
            raise ValueError(f"draft status must be pending or approved, got {status}")
        self._upsert(post_id, niche, account, subreddit, title, url, status, draft=draft, reason=None)

    def set_status(self, post_id: str, status: str, *, reason: str | None = None, draft: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status}")
        sets = ["status = ?", "updated_at = ?"]
        params: list = [status, time.time()]
        if reason is not None:
            sets.append("reason = ?")
            params.append(reason)
        if draft is not None:
            sets.append("draft = ?")
            params.append(draft)
        params.append(post_id)
        cur = self._conn.execute(f"UPDATE posts SET {', '.join(sets)} WHERE id = ?", params)
        if cur.rowcount == 0:
            raise KeyError(post_id)
        self._conn.commit()

    def record_posted(self, post_id: str, comment_id: str, *, posted_at: float | None = None) -> None:
        posted_at = time.time() if posted_at is None else posted_at
        with self._conn:
            row = self._conn.execute("SELECT account FROM posts WHERE id = ?", (post_id,)).fetchone()
            if row is None:
                raise KeyError(post_id)
            self._conn.execute(
                "UPDATE posts SET status = ?, comment_id = ?, updated_at = ? WHERE id = ?",
                (POSTED, comment_id, posted_at, post_id),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO comments_log (comment_id, post_id, account, posted_at) VALUES (?, ?, ?, ?)",
                (comment_id, post_id, row["account"], posted_at),
            )

    def _upsert(self, post_id: str, niche: str, account: str, subreddit: str, title: str, url: str, status: str, *, draft: str | None, reason: str | None) -> None:
        now = time.time()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO posts (id, niche, account, subreddit, title, url, status, draft, reason, comment_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    niche = excluded.niche,
                    account = excluded.account,
                    status = excluded.status,
                    draft = excluded.draft,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
                """,
                (post_id, niche, account, subreddit, title, url, status, draft, reason, now, now),
            )


def _to_record(row: sqlite3.Row) -> Record:
    return Record(
        id=row["id"],
        niche=row["niche"],
        account=row["account"],
        subreddit=row["subreddit"],
        title=row["title"],
        url=row["url"],
        status=row["status"],
        draft=row["draft"],
        reason=row["reason"],
        comment_id=row["comment_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
