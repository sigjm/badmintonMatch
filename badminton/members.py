import re
import time

from werkzeug.security import check_password_hash, generate_password_hash

from .db import transaction
from .matching import GRADE_POINTS


def upsert_somoim(conn, mid: str, nickname: str) -> int:
    row = conn.execute("SELECT id FROM members WHERE somoim_mid = ?", (mid,)).fetchone()
    if row:
        conn.execute("UPDATE members SET nickname = ? WHERE id = ?", (nickname, row["id"]))
        return row["id"]
    # 붙여넣기로 먼저 만들어진 같은 닉네임 회원이 딱 한 명이면 그 회원을 소모임 회원과 연결한다 (급수 유지)
    pasted = conn.execute("SELECT id FROM members WHERE nickname = ? AND somoim_mid IS NULL AND is_guest = 0",
                          (nickname,)).fetchall()
    if len(pasted) == 1:
        conn.execute("UPDATE members SET somoim_mid = ? WHERE id = ?", (mid, pasted[0]["id"]))
        return pasted[0]["id"]
    return conn.execute("INSERT INTO members(somoim_mid, nickname) VALUES (?, ?)", (mid, nickname)).lastrowid


def create_member(conn, nickname: str, *, is_guest: bool = False, gender: str | None = None,
                  grade: str | None = None) -> int:
    _check_codes(gender, grade)
    return conn.execute("INSERT INTO members(nickname, gender, grade, is_guest) VALUES (?, ?, ?, ?)",
                        (nickname, gender, grade, int(is_guest))).lastrowid


def _check_codes(gender, grade):
    if gender not in (None, "M", "F") or grade not in (None, *GRADE_POINTS):
        raise ValueError("성별 또는 급수 값이 올바르지 않습니다")


def rename_member(conn, member_id: int, nickname: str, by: str) -> None:
    nickname = (nickname or "").strip()
    if not nickname:
        raise ValueError("이름을 입력해 주세요")
    if len(nickname) > 40:
        raise ValueError("이름은 40자 이하여야 합니다")
    row = conn.execute("SELECT nickname FROM members WHERE id = ?", (member_id,)).fetchone()
    if not row:
        raise ValueError("존재하지 않는 회원입니다")
    old_nick = row["nickname"]
    if old_nick != nickname:
        now = time.time()
        with transaction(conn):
            conn.execute("UPDATE members SET nickname = ? WHERE id = ?", (nickname, member_id))
            conn.execute("INSERT INTO changes(member_id, by, field, old, new, at) VALUES (?, ?, 'nickname', ?, ?, ?)",
                         (member_id, by, old_nick, nickname, now))


def set_info(conn, member_id: int, gender: str | None, grade: str | None, by: str) -> list[tuple]:
    """바뀐 필드를 (필드, 이전 값, 새 값) 목록으로 돌려준다."""
    _check_codes(gender, grade)
    row = conn.execute("SELECT gender, grade FROM members WHERE id = ?", (member_id,)).fetchone()
    now = time.time()
    changed = []
    with transaction(conn):
        for field, new in (("gender", gender), ("grade", grade)):
            if row[field] != new:
                changed.append((field, row[field], new))
                conn.execute(f"UPDATE members SET {field} = ? WHERE id = ?", (new, member_id))
                conn.execute("INSERT INTO changes(member_id, by, field, old, new, at) VALUES (?, ?, ?, ?, ?, ?)",
                             (member_id, by, field, row[field], new, now))
    return changed


def set_pin(conn, member_id: int, pin: str) -> None:
    if not re.fullmatch(r"\d{4}", pin or ""):
        raise ValueError("PIN은 숫자 4자리")
    conn.execute("UPDATE members SET pin_hash = ? WHERE id = ?", (generate_password_hash(pin), member_id))


def check_pin(conn, member_id: int, pin: str) -> bool:
    row = conn.execute("SELECT pin_hash FROM members WHERE id = ?", (member_id,)).fetchone()
    return bool(row and row["pin_hash"] and check_password_hash(row["pin_hash"], pin or ""))


def reset_pin(conn, member_id: int) -> None:
    conn.execute("UPDATE members SET pin_hash = NULL WHERE id = ?", (member_id,))


def list_members(conn, *, guests: bool | None = None) -> list:
    where = "" if guests is None else f"WHERE is_guest = {int(guests)}"
    return conn.execute(f"SELECT * FROM members {where} ORDER BY nickname, id").fetchall()


def changes(conn, limit: int = 100) -> list:
    return conn.execute("""SELECT c.*, m.nickname, strftime('%m-%d %H:%M', c.at, 'unixepoch', '+9 hours') AS when_kst
                           FROM changes c JOIN members m ON m.id = c.member_id
                           ORDER BY c.at DESC, c.id DESC LIMIT ?""", (limit,)).fetchall()
