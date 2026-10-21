import sqlite3

import pytest

from badminton import db
from badminton.groups import add_group, ensure_default_group, list_groups, parse_gid
from badminton.meetups import get_or_create_meetup

GID = "9b34753d-4105-4c55-9310-bb923ba2a8e61"


def test_parse_gid_from_link_or_id():
    assert parse_gid(f"https://www.somoim.co.kr/{GID}") == GID
    assert parse_gid(f" https://somoim.co.kr/{GID}?utm=share ") == GID
    assert parse_gid(GID) == GID
    with pytest.raises(ValueError):
        parse_gid("https://example.com/hello")


def test_add_group_is_idempotent_and_updates_name(conn):
    a = add_group(conn, GID, "금요일 야간민턴")
    b = add_group(conn, GID, "금야민")
    assert a == b and [g["name"] for g in list_groups(conn)] == ["금야민"]


def test_ensure_default_group_backfills_meetups(conn):
    mid = get_or_create_meetup(conn, "2026-10-23", "금야민+게1", "e1")
    gid = ensure_default_group(conn, GID, "금요일 야간민턴")
    assert conn.execute("SELECT group_id FROM meetups WHERE id = ?", (mid,)).fetchone()[0] == gid


def test_connect_migrates_old_meetups_table(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE meetups(id INTEGER PRIMARY KEY, date TEXT NOT NULL, name TEXT NOT NULL, eid TEXT UNIQUE,"
                " courts INTEGER NOT NULL DEFAULT 0, started INTEGER NOT NULL DEFAULT 0)")
    old.execute("INSERT INTO meetups(date, name) VALUES ('2026-10-23', 'x')")
    old.commit()
    old.close()
    c = db.connect(path)
    assert "group_id" in [r[1] for r in c.execute("PRAGMA table_info(meetups)")]
    assert c.execute("SELECT count(*) FROM meetups").fetchone()[0] == 1
