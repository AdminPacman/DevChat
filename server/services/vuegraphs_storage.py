"""
SQLite-backed storage helpers for Vue graph editor payloads.
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional


_INITIALIZED_PATHS: set[Path] = set()


def _get_db_path() -> Path:
    """Resolve the SQLite database path, allowing overrides via env."""
    return Path(os.getenv("VUEGRAPHS_DB_PATH", "data/vuegraphs.db"))


def _looks_like_json_object(text: Optional[str]) -> bool:
    """True when text is plausibly a serialized VueFlow graph rather than YAML.

    Cheap prefix check first so we do not json.loads() 45 rows of YAML on every
    start-up; the parse only runs on candidates that already look like objects.
    """
    if not text:
        return False
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return False
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and "nodes" in parsed


def _migrate_layout_column(connection: sqlite3.Connection) -> int:
    """Split layout out of `content`, one time, without losing anything.

    THE BUG THIS FIXES: `content` was a single untyped TEXT column holding EITHER
    the VueFlow JSON (which carries every node's x/y) OR the raw YAML source,
    depending on who wrote last. `tools/sync_vuegraphs.py` globs the whole
    yaml_instance/ directory and blind-writes YAML into it, so ONE `make sync`
    destroyed the saved layout of every graph in the library — and the frontend
    swallowed the resulting JSON.parse failure and silently regenerated a cramped
    auto-layout over the user's work.

    With layout in its own column, sync writing `content` can no longer touch it.
    The conflation was the defect; this removes it rather than guarding it.

    Non-destructive by design: JSON content is COPIED into `layout` and the
    original `content` is left exactly as it was. Nothing is deleted, so a bad
    migration costs nothing and sync will replace the stale YAML naturally.
    """
    columns = {row[1] for row in connection.execute("PRAGMA table_info(vuegraphs)")}
    if "layout" not in columns:
        connection.execute("ALTER TABLE vuegraphs ADD COLUMN layout TEXT")

    migrated = 0
    rows = connection.execute(
        "SELECT filename, content FROM vuegraphs WHERE layout IS NULL"
    ).fetchall()
    for filename, content in rows:
        if _looks_like_json_object(content):
            connection.execute(
                "UPDATE vuegraphs SET layout = ? WHERE filename = ?",
                (content, filename),
            )
            migrated += 1
    return migrated


def _ensure_db_initialized() -> Path:
    """Create the SQLite database and table if they do not already exist."""
    db_path = _get_db_path()
    if db_path not in _INITIALIZED_PATHS or not db_path.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vuegraphs (
                    filename TEXT PRIMARY KEY,
                    content TEXT NOT NULL
                )
                """
            )
            _migrate_layout_column(connection)
            connection.commit()
        _INITIALIZED_PATHS.add(db_path)
    return db_path


def save_vuegraph_content(filename: str, content: str) -> None:
    """Insert or update the stored YAML content for the provided filename.

    Writes `content` ONLY. `layout` is deliberately absent from this statement:
    that is what makes `make sync` structurally incapable of destroying a saved
    canvas layout, rather than merely discouraged from it.
    """
    db_path = _ensure_db_initialized()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO vuegraphs (filename, content)
            VALUES (?, ?)
            ON CONFLICT(filename) DO UPDATE SET content=excluded.content
            """,
            (filename, content),
        )
        connection.commit()


def save_vuegraph_layout(filename: str, layout: str) -> None:
    """Insert or update the stored canvas layout (VueFlow JSON) for a filename.

    Mirror image of save_vuegraph_content: touches `layout` ONLY, so persisting a
    dragged node can never clobber the graph's YAML either. The row may not exist
    yet (a graph opened before it was ever synced), hence the empty-string content
    default on insert — `content` is NOT NULL.
    """
    db_path = _ensure_db_initialized()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO vuegraphs (filename, content, layout)
            VALUES (?, '', ?)
            ON CONFLICT(filename) DO UPDATE SET layout=excluded.layout
            """,
            (filename, layout),
        )
        connection.commit()


def fetch_vuegraph_layout(filename: str) -> Optional[str]:
    """Return the stored canvas layout for filename, or None when absent.

    Falls back to `content` when it still holds VueFlow JSON — covers a row written
    by an older build between this deploy and its first layout save. Returning None
    is a normal, expected answer (a graph that has never been arranged); the caller
    must treat it as "compute a fresh layout", not as an error.
    """
    db_path = _ensure_db_initialized()
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            "SELECT layout, content FROM vuegraphs WHERE filename = ?",
            (filename,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        layout, content = row
        if layout:
            return layout
        return content if _looks_like_json_object(content) else None


def fetch_vuegraph_content(filename: str) -> Optional[str]:
    """Return the stored content for filename, or None when absent."""
    db_path = _ensure_db_initialized()
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            "SELECT content FROM vuegraphs WHERE filename = ?",
            (filename,),
        )
        row = cursor.fetchone()
        return row[0] if row else None
