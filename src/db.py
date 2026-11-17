"""SQLite library: what was downloaded (and deleted), and the channels you added.

One connection shared by the loop thread and web threads, guarded by a lock.
Paths are stored relative to DOWNLOADS_DIR, as posix strings.
"""
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id                  INTEGER PRIMARY KEY,
    title               TEXT,
    username            TEXT,
    ident               TEXT,
    first_downloaded_at TEXT,
    last_downloaded_at  TEXT
);

CREATE TABLE IF NOT EXISTS channel_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL,
    ident      TEXT,
    event      TEXT NOT NULL CHECK (event IN ('added', 'removed')),
    at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS files (
    media_key     TEXT PRIMARY KEY,
    channel_id    INTEGER,
    message_id    INTEGER,
    path          TEXT NOT NULL,
    kind          TEXT,
    mime          TEXT,
    size          INTEGER,
    sha256        TEXT,
    caption       TEXT,
    msg_date      TEXT,
    status        TEXT NOT NULL DEFAULT 'downloaded' CHECK (status IN ('downloaded', 'deleted')),
    downloaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_sha ON files(channel_id, sha256);

CREATE TABLE IF NOT EXISTS ratings (
    path     TEXT PRIMARY KEY,
    rating   TEXT NOT NULL CHECK (rating IN ('good', 'bad', 'delete')),
    rated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS media_info (
    path       TEXT PRIMARY KEY,
    width      INTEGER,
    height     INTEGER,
    duration   REAL,
    has_audio  INTEGER,
    codec      TEXT,
    phash      TEXT,
    error      TEXT,
    scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS study_sources (
    channel_id INTEGER PRIMARY KEY,
    since      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Vectors are float32 little-endian BLOBs, normalized (cosine = dot product).
CREATE TABLE IF NOT EXISTS ml_results (
    path         TEXT PRIMARY KEY,
    model        TEXT NOT NULL,
    clip         BLOB,
    width        INTEGER,
    height       INTEGER,
    face_count   INTEGER,
    error        TEXT,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS faces (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    path   TEXT NOT NULL,
    x REAL, y REAL, w REAL, h REAL,
    score  REAL,
    vector BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_faces_path ON faces(path);
"""

# One row per file on disk: several records can share a path (forwards, kept copies).
KEPT = """kept AS (
    SELECT f.path, MIN(f.channel_id) AS channel_id, MAX(f.kind) AS kind, MAX(f.size) AS size,
           MAX(f.caption) AS caption, MIN(COALESCE(f.msg_date, f.downloaded_at)) AS at,
           MAX(r.rating) AS rating, MAX(m.width) AS width, MAX(m.height) AS height,
           MAX(m.duration) AS duration, MAX(ml.path IS NOT NULL) AS analyzed,
           MAX(ml.face_count) AS face_count, MAX(ml.error) AS ml_error
    FROM files f LEFT JOIN ratings r ON r.path = f.path LEFT JOIN media_info m ON m.path = f.path
         LEFT JOIN ml_results ml ON ml.path = f.path
    WHERE f.status = 'downloaded' AND f.channel_id IS NOT NULL GROUP BY f.path
)"""

RATINGS = ("good", "bad", "delete")

SORTS = {
    "newest": "at DESC", "oldest": "at ASC",
    "name_asc": "path ASC", "name_desc": "path DESC",
    "size_desc": "size DESC", "size_asc": "size ASC",
}


class Library:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def _all(self, sql, args=()):
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def _one(self, sql, args=()):
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def _run(self, sql, args=()):
        with self._lock, self._conn:
            return self._conn.execute(sql, args).rowcount

    # ─── files ──────────────────────────────────────────────────
    def get_file(self, media_key: str):
        return self._one("SELECT * FROM files WHERE media_key = ?", (media_key,))

    def save_file(self, media_key, channel_id, message_id, path, kind, mime, size,
                  sha256, caption, msg_date) -> None:
        self._run(
            """INSERT INTO files (media_key, channel_id, message_id, path, kind, mime, size,
                                  sha256, caption, msg_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (media_key) DO UPDATE SET
                   channel_id = excluded.channel_id, message_id = excluded.message_id,
                   path = excluded.path, kind = excluded.kind, mime = excluded.mime,
                   size = excluded.size, sha256 = excluded.sha256, caption = excluded.caption,
                   msg_date = excluded.msg_date, status = 'downloaded',
                   downloaded_at = datetime('now'), deleted_at = NULL""",
            (media_key, channel_id, message_id, path, kind, mime, size, sha256, caption, msg_date),
        )

    def find_copy(self, channel_id: int, sha256: str, exclude: str):
        """Paths of kept files in this channel with the same content."""
        rows = self._all(
            """SELECT DISTINCT path FROM files
               WHERE channel_id = ? AND sha256 = ? AND status = 'downloaded' AND path != ?
               ORDER BY downloaded_at""",
            (channel_id, sha256, exclude),
        )
        return [r["path"] for r in rows]

    def mark_deleted(self, paths) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                """UPDATE files SET status = 'deleted', deleted_at = datetime('now')
                   WHERE path = ? AND status = 'downloaded'""",
                [(p,) for p in paths],
            )
            self._conn.executemany("DELETE FROM ratings WHERE path = ?", [(p,) for p in paths])
            self._conn.executemany("DELETE FROM media_info WHERE path = ?", [(p,) for p in paths])
            self._conn.executemany("DELETE FROM ml_results WHERE path = ?", [(p,) for p in paths])
            self._conn.executemany("DELETE FROM faces WHERE path = ?", [(p,) for p in paths])

    def repoint(self, old: str, new: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE files SET path = ? WHERE path = ?", (new, old))
            # The kept copy keeps its own rating if it has one.
            self._conn.execute("UPDATE OR IGNORE ratings SET path = ? WHERE path = ?", (new, old))
            self._conn.execute("DELETE FROM ratings WHERE path = ?", (old,))
            self._conn.execute("DELETE FROM media_info WHERE path = ?", (old,))
            self._conn.execute("DELETE FROM ml_results WHERE path = ?", (old,))
            self._conn.execute("DELETE FROM faces WHERE path = ?", (old,))

    def kept_files(self):
        return self._all(
            """SELECT path, channel_id, sha256, size, caption, downloaded_at FROM files
               WHERE status = 'downloaded'"""
        )

    # ─── viewer + files manager ─────────────────────────────────
    def channel_cards(self, kinds=None) -> list:
        where, args = _kinds_filter(kinds)
        return self._all(
            f"""WITH {KEPT}
                SELECT k.channel_id, COALESCE(c.title, 'Channel ' || k.channel_id) AS title,
                       COUNT(*) AS n, SUM(k.size) AS size, MAX(k.at) AS last,
                       SUM(k.kind = 'images') AS images, SUM(k.kind = 'videos') AS videos,
                       SUM(k.kind = 'audio') AS audio,
                       (SELECT k2.path || '|' || k2.kind FROM kept k2 WHERE k2.channel_id = k.channel_id
                          AND k2.kind IN ('images', 'videos') ORDER BY k2.at DESC LIMIT 1) AS cover
                FROM kept k LEFT JOIN channels c ON c.id = k.channel_id
                WHERE 1 {where} GROUP BY k.channel_id""",
            args,
        )

    def channel_files(self, channel_id: int, kinds=None, kind="", q="", sort="newest",
                      limit=50, offset=0, rating=""):
        """(rows, total) for one channel page."""
        where, args = _kinds_filter(kinds)
        where += " AND k.channel_id = ?"
        args.append(channel_id)
        if kind:
            where += " AND k.kind = ?"
            args.append(kind)
        if q:
            where += " AND (k.path LIKE ? OR k.caption LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
        if rating == "none":
            where += " AND k.rating IS NULL"
        elif rating:
            where += " AND k.rating = ?"
            args.append(rating)
        total = self._one(f"WITH {KEPT} SELECT COUNT(*) AS n FROM kept k WHERE 1 {where}", args)["n"]
        rows = self._all(
            f"WITH {KEPT} SELECT * FROM kept k WHERE 1 {where} "
            f"ORDER BY {SORTS.get(sort, SORTS['newest'])} LIMIT ? OFFSET ?",
            args + [limit, offset],
        )
        return rows, total

    def channel_paths(self, channel_ids) -> list:
        marks = ",".join("?" * len(channel_ids))
        return [r["path"] for r in self._all(
            f"""SELECT DISTINCT path FROM files
                WHERE status = 'downloaded' AND channel_id IN ({marks})""", list(channel_ids))]

    def known_paths(self, paths) -> set:
        known = set()
        for p in paths:
            if self._one("SELECT 1 FROM files WHERE path = ? AND status = 'downloaded'", (p,)):
                known.add(p)
        return known

    # ─── ratings ────────────────────────────────────────────────
    def set_rating(self, path: str, rating) -> None:
        if rating is None:
            self._run("DELETE FROM ratings WHERE path = ?", (path,))
        else:
            self._run(
                """INSERT INTO ratings (path, rating) VALUES (?, ?)
                   ON CONFLICT (path) DO UPDATE SET rating = excluded.rating,
                                                    rated_at = datetime('now')""",
                (path, rating),
            )

    def marked_files(self) -> list:
        """Kept files rated 'delete', with their channel title."""
        return self._all(
            f"""WITH {KEPT}
                SELECT k.*, COALESCE(c.title, 'Channel ' || k.channel_id) AS title
                FROM kept k LEFT JOIN channels c ON c.id = k.channel_id
                WHERE k.rating = 'delete' ORDER BY title, k.at DESC""")

    def mark_to_delete(self, paths) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                """INSERT INTO ratings (path, rating) VALUES (?, 'delete')
                   ON CONFLICT (path) DO UPDATE SET rating = 'delete', rated_at = datetime('now')""",
                [(p,) for p in paths],
            )

    def marked_summary(self) -> dict:
        row = self._one(f"WITH {KEPT} SELECT COUNT(*) AS n, SUM(size) AS size "
                        "FROM kept WHERE rating = 'delete'")
        return {"count": row["n"], "size": row["size"] or 0}

    # ─── media info ─────────────────────────────────────────────
    def unscanned(self) -> list:
        return self._all(
            f"""WITH {KEPT} SELECT k.path, k.kind FROM kept k
                WHERE k.kind IN ('images', 'videos', 'audio')
                  AND NOT EXISTS (SELECT 1 FROM media_info m WHERE m.path = k.path)""")

    def save_media_info(self, path, width, height, duration, has_audio, codec, phash, error) -> None:
        self._run(
            """INSERT OR REPLACE INTO media_info
                   (path, width, height, duration, has_audio, codec, phash, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (path, width, height, duration, has_audio, codec, phash, error),
        )

    def media_info_summary(self) -> dict:
        row = self._one(
            f"""WITH {KEPT} SELECT
                    COUNT(*) AS total,
                    SUM(EXISTS (SELECT 1 FROM media_info m WHERE m.path = k.path)) AS scanned,
                    SUM(EXISTS (SELECT 1 FROM media_info m WHERE m.path = k.path
                                AND m.error IS NOT NULL)) AS failed
                FROM kept k WHERE k.kind IN ('images', 'videos', 'audio')""")
        return {"files": row["total"], "scanned": row["scanned"] or 0, "failed": row["failed"] or 0}

    def short_videos(self, seconds: float) -> list:
        """Kept videos shorter than `seconds` that are not marked to delete yet."""
        return self._all(
            f"""WITH {KEPT} SELECT path, size, duration FROM kept
                WHERE kind = 'videos' AND duration IS NOT NULL AND duration < ?
                  AND rating IS NOT 'delete'""", (seconds,))

    # ─── study sources + ML results ─────────────────────────────
    def is_study_source(self, channel_id: int) -> bool:
        return self._one("SELECT 1 FROM study_sources WHERE channel_id = ?", (channel_id,)) is not None

    def set_study_source(self, channel_id: int, on: bool) -> None:
        if on:
            self._run("INSERT OR IGNORE INTO study_sources (channel_id) VALUES (?)", (channel_id,))
        else:
            self._run("DELETE FROM study_sources WHERE channel_id = ?", (channel_id,))

    def ml_todo(self, channel_id: int, model: str) -> list:
        """Images of a study-source channel with no result from this model yet."""
        return self._all(
            f"""WITH {KEPT} SELECT k.path FROM kept k
                WHERE k.channel_id = ? AND k.kind = 'images'
                  AND EXISTS (SELECT 1 FROM study_sources s WHERE s.channel_id = k.channel_id)
                  AND NOT EXISTS (SELECT 1 FROM ml_results r WHERE r.path = k.path AND r.model = ?)
                ORDER BY k.at""", (channel_id, model))

    def ml_summary(self, channel_id: int, model=None) -> dict:
        """Counts for one channel; with a model, only results from that model count as done."""
        row = self._one(
            f"""WITH {KEPT} SELECT COUNT(*) AS images,
                    COUNT(r.path) AS done,
                    SUM(r.error IS NOT NULL) AS errors,
                    SUM(r.face_count > 0) AS with_faces,
                    SUM(r.face_count) AS faces
                FROM kept k LEFT JOIN ml_results r
                     ON r.path = k.path AND (? IS NULL OR r.model = ?)
                WHERE k.channel_id = ? AND k.kind = 'images'""", (model, model, channel_id))
        return {"images": row["images"], "done": row["done"], "errors": row["errors"] or 0,
                "with_faces": row["with_faces"] or 0, "faces": row["faces"] or 0}

    def save_ml_result(self, path, model, clip=None, width=None, height=None, faces=(),
                       error=None) -> None:
        """Replace the result for one image. faces: [(x, y, w, h, score, vector bytes)]."""
        with self._lock, self._conn:
            if not self._conn.execute(
                    "SELECT 1 FROM files WHERE path = ? AND status = 'downloaded'", (path,)).fetchone():
                return  # deleted while it was being analyzed
            self._conn.execute("DELETE FROM faces WHERE path = ?", (path,))
            self._conn.execute(
                """INSERT OR REPLACE INTO ml_results
                       (path, model, clip, width, height, face_count, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (path, model, clip, width, height, None if error else len(faces), error))
            self._conn.executemany(
                "INSERT INTO faces (path, x, y, w, h, score, vector) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(path, *f) for f in faces])

    # ─── channels ───────────────────────────────────────────────
    def channel_titles(self) -> dict:
        return {str(r["id"]): r["title"] for r in self._all("SELECT id, title FROM channels")}

    def save_channel(self, channel_id: int, title, username) -> None:
        self._run(
            """INSERT INTO channels (id, title, username) VALUES (?, ?, ?)
               ON CONFLICT (id) DO UPDATE SET title = excluded.title, username = excluded.username""",
            (channel_id, title, username),
        )

    def channel_downloaded(self, channel_id: int, title, username) -> None:
        self.save_channel(channel_id, title, username)
        self._run(
            """UPDATE channels SET last_downloaded_at = datetime('now'),
                   first_downloaded_at = COALESCE(first_downloaded_at, datetime('now'))
               WHERE id = ?""",
            (channel_id,),
        )

    def extra_idents(self) -> list:
        return [r["ident"] for r in
                self._all("SELECT ident FROM channels WHERE ident IS NOT NULL ORDER BY rowid")]

    def add_extra(self, channel_id: int, title, username, ident: str) -> None:
        self.save_channel(channel_id, title, username)
        with self._lock, self._conn:
            self._conn.execute("UPDATE channels SET ident = ? WHERE id = ?", (ident, channel_id))
            self._conn.execute(
                "INSERT INTO channel_events (channel_id, ident, event) VALUES (?, ?, 'added')",
                (channel_id, ident),
            )

    def remove_extra(self, ident: str) -> None:
        with self._lock, self._conn:
            for r in self._conn.execute("SELECT id FROM channels WHERE ident = ?", (ident,)).fetchall():
                self._conn.execute(
                    "INSERT INTO channel_events (channel_id, ident, event) VALUES (?, ?, 'removed')",
                    (r["id"], ident),
                )
            self._conn.execute("UPDATE channels SET ident = NULL WHERE ident = ?", (ident,))


def _kinds_filter(kinds):
    if not kinds:
        return "", []
    return f" AND k.kind IN ({','.join('?' * len(kinds))})", list(kinds)

