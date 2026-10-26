"""정모 생애: 만들기, 보관·복구, 코트 시작, 정모 끝."""

import time
from datetime import date as date_type

from ..db import transaction
from .common import ArchivedMeetupError
from .scheduling import fill_courts


def get_or_create_meetup(conn, date: str, name: str, eid: str | None, group_id: int | None = None) -> int:
    if eid:
        row = conn.execute("SELECT id, archived_at FROM meetups WHERE eid = ?", (eid,)).fetchone()
        if row:
            if row["archived_at"] is not None:
                raise ArchivedMeetupError(row["id"])
            return row["id"]

    rows = conn.execute("SELECT id, archived_at FROM meetups WHERE date = ? AND name = ? AND group_id IS ? "
                        "ORDER BY id DESC", (date, name, group_id)).fetchall()
    active = next((candidate for candidate in rows if candidate["archived_at"] is None), None)
    if active:
        return active["id"]
    archived = next(iter(rows), None)
    if archived:
        raise ArchivedMeetupError(archived["id"])
    return conn.execute("INSERT INTO meetups(date, name, eid, group_id) VALUES (?, ?, ?, ?)",
                        (date, name, eid, group_id)).lastrowid


def archive_meetup(conn, meetup_id: int) -> bool:
    exists = conn.execute("SELECT 1 FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    if exists is None:
        return False
    conn.execute("UPDATE meetups SET archived_at = COALESCE(archived_at, ?) WHERE id = ?",
                 (time.time(), meetup_id))
    return True


def restore_meetup(conn, meetup_id: int) -> bool:
    exists = conn.execute("SELECT 1 FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    if exists is None:
        return False
    conn.execute("UPDATE meetups SET archived_at = NULL WHERE id = ?", (meetup_id,))
    return True


def create_manual_meetup(conn, date: str, name: str, group_id: int) -> tuple[int, bool]:
    name = (name or "").strip()
    try:
        parsed_date = date_type.fromisoformat(date or "")
    except (TypeError, ValueError):
        raise ValueError("정모 날짜를 확인해 주세요") from None
    if parsed_date.isoformat() != date:
        raise ValueError("정모 날짜를 확인해 주세요")
    if not name:
        raise ValueError("정모 이름을 입력해 주세요")
    if len(name) > 120:
        raise ValueError("정모 이름은 120자 이하여야 합니다")
    if conn.execute("SELECT 1 FROM groups WHERE id = ?", (group_id,)).fetchone() is None:
        raise ValueError("모임을 찾을 수 없습니다")

    rows = conn.execute("SELECT id, archived_at FROM meetups WHERE group_id = ? AND date = ? AND name = ? "
                        "ORDER BY id DESC", (group_id, date, name)).fetchall()
    active = next((row for row in rows if row["archived_at"] is None), None)
    if active:
        return active["id"], False
    archived = next(iter(rows), None)
    if archived:
        raise ArchivedMeetupError(archived["id"])
    meetup_id = conn.execute("INSERT INTO meetups(date, name, eid, group_id) VALUES (?, ?, NULL, ?)",
                             (date, name, group_id)).lastrowid
    return meetup_id, True


def start(conn, meetup_id: int, courts: int) -> None:
    row = conn.execute("SELECT started, started_at, archived_at FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    if row is None or row["archived_at"] is not None:
        raise ValueError("활성 정모를 찾을 수 없습니다")
    now = time.time()
    started_at = now if (not row["started"] and row["started_at"] is None) else row["started_at"]
    conn.execute("UPDATE meetups SET courts = ?, started = 1, started_at = ?, ended_at = NULL, resv_hold = 0 WHERE id = ?",
                 (courts, started_at, meetup_id))
    conn.execute("DELETE FROM court_states WHERE meetup_id = ? AND court > ?", (meetup_id, courts))
    fill_courts(conn, meetup_id)


def end_meetup(conn, meetup_id: int) -> None:
    row = conn.execute("SELECT started, ended_at, archived_at FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    if row is None or row["archived_at"] is not None:
        raise ValueError("활성 정모를 찾을 수 없습니다")
    if not row["started"]:
        raise ValueError("이미 종료되었거나 진행 중이지 않은 정모입니다")

    now = time.time()
    with transaction(conn):
        active_games = conn.execute(
            "SELECT id, a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND ended_at IS NULL",
            (meetup_id,)).fetchall()
        for g in active_games:
            conn.execute("UPDATE games SET ended_at = ? WHERE id = ?", (now, g["id"]))
            conn.execute(
                "UPDATE attendees SET games = games + 1, queued_at = ? WHERE meetup_id = ? AND member_id IN (?, ?, ?, ?)",
                (now, meetup_id, g["a1"], g["a2"], g["b1"], g["b2"]))
        conn.execute("DELETE FROM reservations WHERE meetup_id = ?", (meetup_id,))
        conn.execute("DELETE FROM court_states WHERE meetup_id = ?", (meetup_id,))
        conn.execute("UPDATE meetups SET started = 0, ended_at = ?, resv_hold = 0 WHERE id = ?",
                     (now, meetup_id))
