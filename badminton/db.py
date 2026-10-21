from contextlib import contextmanager
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS members(
  id INTEGER PRIMARY KEY, somoim_mid TEXT UNIQUE, nickname TEXT NOT NULL,
  gender TEXT CHECK(gender IN ('M','F')), grade TEXT CHECK(grade IN ('S','A','B','C','D','N')),
  pin_hash TEXT, is_guest INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS changes(
  id INTEGER PRIMARY KEY, member_id INTEGER REFERENCES members, by TEXT, field TEXT, old TEXT, new TEXT, at REAL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value REAL);
CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY, gid TEXT UNIQUE NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meetups(
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, name TEXT NOT NULL, eid TEXT UNIQUE,
  courts INTEGER NOT NULL DEFAULT 0, started INTEGER NOT NULL DEFAULT 0, group_id INTEGER REFERENCES groups);
CREATE TABLE IF NOT EXISTS attendees(
  meetup_id INTEGER REFERENCES meetups, member_id INTEGER REFERENCES members, status TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN ('somoim','guest','paste')), games INTEGER NOT NULL DEFAULT 0,
  queued_at REAL NOT NULL, PRIMARY KEY(meetup_id, member_id));
CREATE TABLE IF NOT EXISTS games(
  id INTEGER PRIMARY KEY, meetup_id INTEGER REFERENCES meetups, court INTEGER NOT NULL,
  a1 INTEGER, a2 INTEGER, b1 INTEGER, b2 INTEGER, started_at REAL NOT NULL, ended_at REAL,
  canceled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS reservations(
  id INTEGER PRIMARY KEY, meetup_id INTEGER NOT NULL REFERENCES meetups, seq INTEGER NOT NULL,
  a1 INTEGER, a2 INTEGER, b1 INTEGER, b2 INTEGER, created_at REAL NOT NULL);
INSERT OR IGNORE INTO settings VALUES ('female_offset', 1.5), ('mix_window', 6);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    # 모임 여러 개 지원 전에 만든 DB에는 meetups.group_id가 없다
    if "group_id" not in [r[1] for r in conn.execute("PRAGMA table_info(meetups)")]:
        conn.execute("ALTER TABLE meetups ADD COLUMN group_id INTEGER REFERENCES groups")
    if "canceled" not in [r[1] for r in conn.execute("PRAGMA table_info(games)")]:
        conn.execute("ALTER TABLE games ADD COLUMN canceled INTEGER NOT NULL DEFAULT 0")
    return conn


DEFAULT_SETTINGS = {"female_offset": 1.5, "mix_window": 6.0}


def get_setting(conn, key: str, default: float | None = None) -> float:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is not None and row[0] is not None:
        return float(row[0])
    if default is not None:
        return float(default)
    return float(DEFAULT_SETTINGS.get(key, 0.0))


def set_setting(conn, key: str, value: float) -> None:
    conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))


@contextmanager
def transaction(conn: sqlite3.Connection):
    """autocommit 모드(isolation_level=None)에서도 안전한 원자적 트랜잭션을 보장한다."""
    if conn.in_transaction:
        yield conn
    else:
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
