"""SQLite persistence. Short transactions; model computation never holds a DB lock."""
from contextlib import contextmanager
import sqlite3

SCHEMA = '''
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pages(
 id TEXT PRIMARY KEY, title TEXT NOT NULL, revision INTEGER NOT NULL,
 updated REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS versions(
 id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES pages(id),
 revision INTEGER NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL,
 created REAL NOT NULL, UNIQUE(page_id, revision));
CREATE TABLE IF NOT EXISTS checkpoints(
 id TEXT PRIMARY KEY, parent_id TEXT, previous_id TEXT, kind TEXT NOT NULL,
 created REAL NOT NULL, expires REAL NOT NULL, digest TEXT NOT NULL,
 available INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, signature TEXT NOT NULL,
 version_id TEXT NOT NULL REFERENCES versions(id), repeats INTEGER NOT NULL,
 status TEXT NOT NULL, created REAL NOT NULL, started REAL, finished REAL,
 before_id TEXT, after_id TEXT, metrics TEXT NOT NULL DEFAULT '{}', error TEXT);
CREATE TABLE IF NOT EXISTS responses(
 id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id),
 checkpoint_id TEXT NOT NULL, created REAL NOT NULL,
 raw_tokens TEXT NOT NULL, text TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS job_queue ON jobs(status, created);
CREATE INDEX IF NOT EXISTS response_page ON responses(version_id, created);
'''


class Store:
    def __init__(self, path):
        self.path = path
        db = sqlite3.connect(self.path, isolation_level=None)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError('Unsupported database version')
            db.execute('PRAGMA user_version=1')
        finally:
            db.close()

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args)]
