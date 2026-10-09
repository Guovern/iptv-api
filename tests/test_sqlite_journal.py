import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from utils import db
from utils.channel_repository import ensure_channel_repository, start_run


class JournalConnection:
    def __init__(self, connection, modes, failure_stage, error):
        self.connection = connection
        self.modes = modes
        self.failure_stage = failure_stage
        self.error = error
        self.wal = False

    def execute(self, sql, *args):
        if sql.startswith("PRAGMA journal_mode="):
            mode = sql.split("=")[1].upper()
            self.modes.append(mode)
            self.wal = mode == "WAL"
            if self.wal and self.failure_stage == "mode":
                raise self.error
        if self.wal and self.failure_stage == "write" and sql.startswith("CREATE"):
            raise self.error
        self.last_cursor = self.connection.execute(sql, *args)
        return self.last_cursor

    def executescript(self, sql):
        if "PRAGMA journal_mode=WAL" in sql:
            self.modes.append("WAL")
            self.wal = True
        if self.wal:
            raise self.error
        return self.connection.executescript(sql)

    def cursor(self):
        return self

    def fetchall(self):
        return self.last_cursor.fetchall()

    def __getattr__(self, name):
        return getattr(self.connection, name)


class SqliteJournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "channels.db")
        self.modes = []
        self.connect = sqlite3.connect

    def connections(self, stage, message="disk I/O error"):
        def connect(*args, **kwargs):
            return JournalConnection(self.connect(*args, **kwargs), self.modes, stage,
                                     sqlite3.OperationalError(message))
        return patch("utils.db.sqlite3.connect", side_effect=connect)

    def test_channel_schema_retries_wal_mode_and_first_write_failures(self):
        for stage in ("mode", "write"):
            with self.subTest(stage=stage):
                path = self.path + stage
                self.modes.clear()
                with self.connections(stage):
                    ensure_channel_repository(path)
                    run_id = start_run(path)
                self.assertEqual(self.modes.count("WAL"), 1)
                with self.connect(path) as connection:
                    self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
                    self.assertEqual(connection.execute("SELECT run_id FROM runs").fetchone()[0], run_id)

    def test_result_schema_retries_wal_and_preserves_data(self):
        with self.connections("write"):
            db.replace_result_data(self.path, [{"id": "demo", "url": "http://example.com/live"}])
        with self.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT id FROM result_data").fetchone()[0], "demo")
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_unrelated_schema_errors_are_not_retried(self):
        with self.connections("write", "database is locked"):
            with self.assertRaisesRegex(sqlite3.OperationalError, "database is locked"):
                ensure_channel_repository(self.path)
        self.assertNotIn("DELETE", self.modes)

    def test_fallback_failure_is_propagated(self):
        class BrokenConnection(JournalConnection):
            def executescript(self, sql):
                raise sqlite3.OperationalError("disk I/O error")

        def connect(*args, **kwargs):
            return BrokenConnection(self.connect(*args, **kwargs), self.modes, "mode",
                                    sqlite3.OperationalError("disk I/O error"))

        with patch("utils.db.sqlite3.connect", side_effect=connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, "disk I/O error"):
                ensure_channel_repository(self.path)
        self.assertEqual(self.modes, ["WAL", "DELETE"])

    def test_existing_channel_data_survives_fallback(self):
        run_id = start_run(self.path)
        with self.connections("write"):
            ensure_channel_repository(self.path)
        with self.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT run_id FROM runs").fetchone()[0], run_id)

    def test_healthy_filesystem_keeps_wal(self):
        ensure_channel_repository(self.path)
        with self.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")


if __name__ == "__main__":
    unittest.main()
