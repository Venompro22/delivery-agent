"""
Tiny database layer so the same code runs on two databases:

- SQLite   (local development and tests) when DATABASE_URL is not set
- Postgres (production on Render + Neon) when DATABASE_URL is set

The rest of the app keeps writing SQL in SQLite style ("?" placeholders).
On Postgres, that SQL is translated right before it runs.

Usage:
    con = db.connect(path)                     # path is only used by SQLite
    rows = con.execute("SELECT * FROM t WHERE id = ?", (5,)).fetchall()
    con.commit()
    con.close()
"""
import os
import re
import sqlite3

try:
    import psycopg
except ImportError:  # psycopg is only needed when DATABASE_URL is set
    psycopg = None


def database_url() -> str:
    """Return the Postgres URL from the environment, or '' to use SQLite."""
    return os.environ.get("DATABASE_URL", "").strip()


def is_postgres() -> bool:
    """True when the app should talk to Postgres instead of SQLite."""
    return database_url().startswith(("postgres://", "postgresql://"))


# Catch db.IntegrityError instead of sqlite3.IntegrityError:
# it matches the error from both databases (e.g. duplicate phone number).
IntegrityError = (sqlite3.IntegrityError,) + (
    (psycopg.IntegrityError,) if psycopg else ()
)


def translate(sql: str, has_params: bool) -> str:
    """Rewrite SQLite-flavoured SQL into Postgres-flavoured SQL."""
    if has_params:
        # psycopg uses % for placeholders, so a literal % (e.g. in LIKE '%x%')
        # must be doubled first, then "?" becomes "%s".
        sql = sql.replace("%", "%%").replace("?", "%s")

    # Auto-increment ids: SQLite keyword -> Postgres type.
    sql = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
                 "BIGSERIAL PRIMARY KEY", sql, flags=re.I)
    # Binary data (photos): SQLite BLOB -> Postgres BYTEA.
    sql = re.sub(r"\bBLOB\b", "BYTEA", sql, flags=re.I)
    # SQLite REAL is a 64-bit float; Postgres REAL is only 32-bit (~6 digits),
    # which would round epoch timestamps by minutes. Use DOUBLE PRECISION.
    sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql, flags=re.I)

    # "INSERT OR IGNORE ..." -> "INSERT ... ON CONFLICT DO NOTHING".
    if re.match(r"\s*INSERT\s+OR\s+IGNORE\b", sql, flags=re.I):
        sql = re.sub(r"INSERT\s+OR\s+IGNORE", "INSERT", sql, count=1, flags=re.I)
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


class Row(tuple):
    """A Postgres row that works like sqlite3.Row: row[0] and row["phone"]."""

    def __new__(cls, names, values):
        row = super().__new__(cls, values)
        row._names = names
        return row

    def __getitem__(self, key):
        if isinstance(key, str):
            key = self._names.index(key)
        return tuple.__getitem__(self, key)

    def keys(self):
        """Lets dict(row) work, exactly like sqlite3.Row."""
        return list(self._names)


def _row_factory(cursor):
    """psycopg calls this once per query to know how to build each row."""
    names = [col.name for col in cursor.description] if cursor.description else []
    return lambda values: Row(names, values)


class PgConnection:
    """Wraps a psycopg connection so it behaves like a sqlite3.Connection."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=None):
        # SQLite asks "which columns does this table have?" with PRAGMA table_info.
        # Answer with the same row shape: (cid, name, type, notnull, default, pk).
        m = re.match(r"\s*PRAGMA\s+table_info\((\w+)\)", sql, flags=re.I)
        if m:
            return self.raw.execute(
                "SELECT ordinal_position - 1, column_name, data_type, "
                "(is_nullable = 'NO')::int, column_default, 0 "
                "FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (m.group(1).lower(),))
        if re.match(r"\s*PRAGMA\b", sql, flags=re.I):
            # Other SQLite-only settings (journal mode etc.) mean nothing here.
            return self.raw.execute("SELECT 1")
        return self.raw.execute(translate(sql, params is not None), params)

    def executemany(self, sql, seq_of_params):
        cur = self.raw.cursor()
        cur.executemany(translate(sql, True), seq_of_params)
        return cur

    def executescript(self, sql):
        """Several statements at once (used for CREATE TABLE blocks)."""
        return self.raw.execute(translate(sql, False))

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    # "with con:" behaves like sqlite3: commit on success, rollback on error.
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.raw.commit()
        else:
            self.raw.rollback()
        return False


def connect(sqlite_path: str, dict_rows: bool = False):
    """
    Open a connection to the right database.

    sqlite_path: file used when running on SQLite (ignored on Postgres).
    dict_rows:   on SQLite, return sqlite3.Row objects (row["name"]).
                 Postgres rows always support both row[0] and row["name"].
    """
    if is_postgres():
        if psycopg is None:
            raise RuntimeError("DATABASE_URL is set but psycopg is not installed")
        return PgConnection(psycopg.connect(database_url(), row_factory=_row_factory))

    con = sqlite3.connect(sqlite_path)
    if dict_rows:
        con.row_factory = sqlite3.Row
    return con 