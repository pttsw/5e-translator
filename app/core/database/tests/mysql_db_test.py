import pytest

from app.core.database.mysql_db import MySQLDatabase


class FakeConnection:
    def __init__(self):
        self.begins = 0
        self.commits = 0
        self.rollbacks = 0

    def begin(self):
        self.begins += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _database_with_fake_connection():
    database = object.__new__(MySQLDatabase)
    database.connection = FakeConnection()
    database.cursor = object()
    database._ensure_connection = lambda: True
    return database


def test_execute_transaction_commits_once_after_callback():
    database = _database_with_fake_connection()

    result = database.execute_transaction(lambda cursor: cursor)

    assert result is database.cursor
    assert database.connection.begins == 1
    assert database.connection.commits == 1
    assert database.connection.rollbacks == 0


def test_execute_transaction_rolls_back_callback_failure():
    database = _database_with_fake_connection()

    with pytest.raises(ValueError, match="failed"):
        database.execute_transaction(
            lambda _cursor: (_ for _ in ()).throw(ValueError("failed"))
        )

    assert database.connection.begins == 1
    assert database.connection.commits == 0
    assert database.connection.rollbacks == 1
