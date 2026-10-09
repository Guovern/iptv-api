import json
import logging
from functools import wraps
import os
import sqlite3
from threading import Lock, RLock


_schema_lock = Lock()
_write_lock = Lock()
_migrated_dbs = {}
_journal_lock = RLock()
_rollback_journal_dbs = {}

_RESULT_DATA_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS result_data ("
    "id TEXT PRIMARY KEY, url TEXT, headers TEXT, video_codec TEXT, "
    "audio_codec TEXT, resolution TEXT, fps REAL)"
)

_RESULT_DATA_COLUMNS = {
    "id": "TEXT",
    "url": "TEXT",
    "headers": "TEXT",
    "video_codec": "TEXT",
    "audio_codec": "TEXT",
    "resolution": "TEXT",
    "fps": "REAL",
}


def _configure_connection(conn):
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def get_db_connection(db_path, *, journal_mode=None):
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        if journal_mode is not None:
            if journal_mode not in ("WAL", "DELETE"):
                raise ValueError("Unsupported SQLite journal mode")
            conn.execute(f"PRAGMA journal_mode={journal_mode}")
        return _configure_connection(conn)
    except Exception:
        conn.close()
        raise


def _database_signature(db_path):
    try:
        stat = os.stat(db_path)
        return stat.st_dev, stat.st_ino
    except OSError:
        return None


def with_journal_fallback(initialize):
    """Retry schema initialization with rollback journaling when WAL I/O fails."""
    @wraps(initialize)
    def wrapped(db_path):
        key = os.fspath(db_path)
        with _journal_lock:
            signature = _database_signature(db_path)
            mode = (
                "DELETE" if signature is not None
                and _rollback_journal_dbs.get(key) == signature else "WAL"
            )
            try:
                return initialize(db_path, journal_mode=mode)
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", None)
                wal_io_error = (
                    code is not None and (code & 0xff) in (sqlite3.SQLITE_IOERR, sqlite3.SQLITE_CANTOPEN)
                ) or (code is None and str(exc) in ("disk I/O error", "unable to open database file"))
                if mode != "WAL" or not wal_io_error:
                    raise
                result = initialize(db_path, journal_mode="DELETE")
                _rollback_journal_dbs[key] = _database_signature(db_path)
                logging.getLogger(__name__).warning(
                    "SQLite WAL initialization failed; using DELETE journal mode for %s", db_path
                )
                return result
    return wrapped


def return_db_connection(db_path, conn):
    if conn is None:
        return
    try:
        if conn.in_transaction:
            conn.rollback()
    finally:
        conn.close()


@with_journal_fallback
def ensure_result_data_schema(db_path, *, journal_mode="WAL"):
    try:
        signature = (os.stat(db_path).st_dev, os.stat(db_path).st_ino)
    except OSError:
        signature = None
    if signature is not None and _migrated_dbs.get(db_path) == signature:
        return

    with _schema_lock:
        try:
            signature = (os.stat(db_path).st_dev, os.stat(db_path).st_ino)
        except OSError:
            signature = None
        if signature is not None and _migrated_dbs.get(db_path) == signature:
            return

        conn = get_db_connection(db_path, journal_mode=journal_mode)
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(_RESULT_DATA_SCHEMA)
            cursor.execute("PRAGMA table_info(result_data)")
            existing = {row[1] for row in cursor.fetchall()}
            for column, column_type in _RESULT_DATA_COLUMNS.items():
                if column not in existing:
                    cursor.execute(f"ALTER TABLE result_data ADD COLUMN {column} {column_type}")
            cursor.execute("PRAGMA user_version=1")
            conn.commit()
            stat_result = os.stat(db_path)
            _migrated_dbs[db_path] = (stat_result.st_dev, stat_result.st_ino)
        except Exception:
            conn.rollback()
            raise
        finally:
            return_db_connection(db_path, conn)


def replace_result_data(db_path, rows):
    ensure_result_data_schema(db_path)
    values = [
        (
            str(item.get("id")),
            item.get("url"),
            json.dumps(item.get("headers"), ensure_ascii=False) if item.get("headers") else None,
            item.get("video_codec"),
            item.get("audio_codec"),
            item.get("resolution"),
            item.get("fps"),
        )
        for item in rows
        if item.get("id") is not None and item.get("url")
    ]

    with _write_lock:
        conn = get_db_connection(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM result_data")
            conn.executemany(
                "INSERT INTO result_data "
                "(id, url, headers, video_codec, audio_codec, resolution, fps) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            return_db_connection(db_path, conn)


def sync_result_data(db_path, rows):
    ensure_result_data_schema(db_path)
    values = {
        str(item.get("id")): (
            item.get("url"),
            json.dumps(item.get("headers"), ensure_ascii=False, sort_keys=True) if item.get("headers") else None,
            item.get("video_codec"),
            item.get("audio_codec"),
            item.get("resolution"),
            item.get("fps"),
        )
        for item in rows
        if item.get("id") is not None and item.get("url")
    }

    with _write_lock:
        conn = get_db_connection(db_path)
        try:
            existing = {
                row[0]: tuple(row[1:])
                for row in conn.execute(
                    "SELECT id, url, headers, video_codec, audio_codec, resolution, fps FROM result_data"
                )
            }
            for item_id, value in list(values.items()):
                previous = existing.get(item_id)
                if not previous:
                    continue
                values[item_id] = (
                    value[0],
                    value[1],
                    value[2] if value[2] is not None else previous[2],
                    value[3] if value[3] is not None else previous[3],
                    value[4] if value[4] is not None else previous[4],
                    value[5] if value[5] is not None else previous[5],
                )
            changed = [
                (item_id, *value)
                for item_id, value in values.items()
                if existing.get(item_id) != value
            ]
            removed = [(item_id,) for item_id in existing.keys() - values.keys()]
            if not changed and not removed:
                return
            conn.execute("BEGIN IMMEDIATE")
            if changed:
                conn.executemany(
                    "INSERT INTO result_data "
                    "(id, url, headers, video_codec, audio_codec, resolution, fps) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "url=excluded.url, headers=excluded.headers, video_codec=excluded.video_codec, "
                    "audio_codec=excluded.audio_codec, resolution=excluded.resolution, fps=excluded.fps",
                    changed,
                )
            if removed:
                conn.executemany("DELETE FROM result_data WHERE id=?", removed)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            return_db_connection(db_path, conn)
