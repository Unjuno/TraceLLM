import sqlite3
import pytest
from trace_llm.store import Store


def test_unknown_database_version_is_not_modified(tmp_path):
    path=tmp_path/'future.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE future_data(value TEXT)')
        db.execute('INSERT INTO future_data VALUES(?)', ('synthetic sentinel',))
        db.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError, match='Unsupported'):
        Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('future_data',)]
        assert db.execute('SELECT value FROM future_data').fetchone()[0]=='synthetic sentinel'
