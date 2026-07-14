"""
Tracks what content has already been served to the model for each file,
so read tools can avoid re-sending identical content and instead return
a short "unchanged" notice - saving tokens on repeat calls.

Cache entries are invalidated whenever the file's mtime/size changes, so
a real edit always produces a fresh read. Keyed on the resolved absolute
path plus a "view" tag (e.g. a line range, or "symbols") since different
views of the same file are tracked independently - reading lines 1-50
doesn't mark the symbol outline as already-served, and vice versa.

Also holds a lightweight symbol index (just names/kinds/lines, no
bodies) per file, so tools can cheaply check "does this file have a
function called X" without re-parsing, and so list_symbols results can
be reused across calls in the same way.

Backed by a SQLite database on disk (code_engine/.cache/joshua_cache.db)
so both the "already served" state and the parsed symbol index survive
server restarts, not just process lifetime. An in-memory dict sits in
front of SQLite as a hot-path cache within a single process run, so
repeat calls in the same session never touch disk.
"""

import json
import sqlite3
import threading
from pathlib import Path

_DB_DIR = Path(__file__).parent / ".cache"
_DB_PATH = _DB_DIR / "joshua_cache.db"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

# In-process hot caches, mirroring what's in SQLite, to avoid a disk
# round-trip on every single tool call within the same server run.
_SERVED_MEM: dict[str, tuple[float, int, str]] = {}
_SYMBOL_MEM: dict[str, tuple[float, int, list]] = {}


def _get_conn() -> sqlite3.Connection:
    global _conn

    if _conn is not None:
        return _conn

    _DB_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS served (
            path TEXT NOT NULL,
            view_tag TEXT NOT NULL,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            PRIMARY KEY (path, view_tag)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symbol_index (
            path TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            data TEXT NOT NULL
        )
        """
    )

    conn.commit()

    _conn = conn
    return conn


def _range_key(start_line: int | None, end_line: int | None) -> str:
    return f"range:{start_line or ''}:{end_line or ''}"


def check_and_mark(path: Path, start_line: int | None, end_line: int | None) -> bool:
    """
    Returns True if this exact (file, range) was already served since the
    file last changed on disk - i.e. it's safe to skip resending the body.
    Marks it as served for next time regardless.
    """

    return _check_and_mark_view(path, _range_key(start_line, end_line))


def check_and_mark_symbols(path: Path) -> bool:
    """Same idea as check_and_mark, but for the 'list symbols' view of a file."""

    return _check_and_mark_view(path, "symbols")


def _check_and_mark_view(path: Path, view_tag: str) -> bool:
    stat = path.stat()
    key = str(path)
    current = (stat.st_mtime, stat.st_size, view_tag)

    with _lock:
        mem_key = f"{key}\x00{view_tag}"
        previous = _SERVED_MEM.get(mem_key)

        if previous is None:
            conn = _get_conn()
            row = conn.execute(
                "SELECT mtime, size FROM served WHERE path = ? AND view_tag = ?",
                (key, view_tag),
            ).fetchone()

            if row is not None:
                previous = (row[0], row[1], view_tag)

        already_served = (
            previous is not None
            and previous[0] == current[0]
            and previous[1] == current[1]
        )

        _SERVED_MEM[mem_key] = current

        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO served (path, view_tag, mtime, size)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path, view_tag) DO UPDATE SET
                mtime = excluded.mtime, size = excluded.size
            """,
            (key, view_tag, current[0], current[1]),
        )
        conn.commit()

    return already_served


def get_symbol_index(path: Path):
    """Return cached symbols for this file if still valid for its current mtime/size, else None."""

    stat = path.stat()
    key = str(path)

    with _lock:
        cached = _SYMBOL_MEM.get(key)

        if cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            return cached[2]

        conn = _get_conn()
        row = conn.execute(
            "SELECT mtime, size, data FROM symbol_index WHERE path = ?",
            (key,),
        ).fetchone()

        if row is not None and row[0] == stat.st_mtime and row[1] == stat.st_size:
            symbols = json.loads(row[2])
            _SYMBOL_MEM[key] = (row[0], row[1], symbols)
            return symbols

    return None


def set_symbol_index(path: Path, symbols) -> None:
    stat = path.stat()
    key = str(path)

    with _lock:
        _SYMBOL_MEM[key] = (stat.st_mtime, stat.st_size, symbols)

        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO symbol_index (path, mtime, size, data)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                mtime = excluded.mtime, size = excluded.size, data = excluded.data
            """,
            (key, stat.st_mtime, stat.st_size, json.dumps(symbols)),
        )
        conn.commit()


def invalidate(path: Path) -> None:
    """Call after an edit/write so the next read/list is never skipped."""

    key = str(path)

    with _lock:
        for k in list(_SERVED_MEM.keys()):
            if k.startswith(f"{key}\x00"):
                _SERVED_MEM.pop(k, None)

        _SYMBOL_MEM.pop(key, None)

        conn = _get_conn()
        conn.execute("DELETE FROM served WHERE path = ?", (key,))
        conn.execute("DELETE FROM symbol_index WHERE path = ?", (key,))
        conn.commit()


def clear() -> None:
    with _lock:
        _SERVED_MEM.clear()
        _SYMBOL_MEM.clear()

        conn = _get_conn()
        conn.execute("DELETE FROM served")
        conn.execute("DELETE FROM symbol_index")
        conn.commit()


def prune(existing_paths) -> int:
    """Remove cache rows for files no longer present on disk. Pass an
    iterable of absolute path strings that currently exist; anything in
    the cache but not in that set is deleted. Returns rows removed.
    """

    existing = set(existing_paths)

    with _lock:
        conn = _get_conn()

        cached_paths = set()
        for row in conn.execute("SELECT DISTINCT path FROM served"):
            cached_paths.add(row[0])
        for row in conn.execute("SELECT path FROM symbol_index"):
            cached_paths.add(row[0])

        stale = cached_paths - existing
        removed = 0

        for key in stale:
            conn.execute("DELETE FROM served WHERE path = ?", (key,))
            conn.execute("DELETE FROM symbol_index WHERE path = ?", (key,))
            removed += conn.total_changes

            for k in list(_SERVED_MEM.keys()):
                if k.startswith(f"{key}\x00"):
                    _SERVED_MEM.pop(k, None)
            _SYMBOL_MEM.pop(key, None)

        conn.commit()

    return len(stale)

