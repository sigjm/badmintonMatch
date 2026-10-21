import re
import time

from werkzeug.security import check_password_hash, generate_password_hash

from .matching import GRADE_POINTS


def upsert_somoim(conn, mid: str, nickname: str) -> int:
    row = conn.execute("SELECT id FROM members WHERE somoim_mid = ?", (mid,)).fetchone()
    if row:
        conn.execute("UPDATE members SET nickname = ? WHERE id = ?", (nickname, row["id"]))
        return row["id"]
    return conn.execute("INSERT INTO members(somoim_mid, nickname) VALUES (?, ?)", (mid, nickname)).lastrowid


def create_member(conn, nickname: str, *, is_guest: bool = False, gender: str | None = None,
                  grade: str | None = None) -> int:
    _check_codes(gender, grade)
    return conn.execute("INSERT INTO members(nickname, gender, grade, is_guest) VALUES (?, ?, ?, ?)",
                        (nickname, gender, grade, int(is_guest))).lastrowid


def _check_codes(gender, grade):
    if gender not in (None, "M", "F") or grade not in (None, *GRADE_POINTS):
        raise ValueError("성별 또는 급수 값이 올바르지 않습니다")


def set_info(conn, member_id: int, gender: str | None, grade: str | None, by: str) -> None:
    _check_codes(gender, grade)
    row = conn.execute("SELECT gender, grade FROM members WHERE id = ?", (member_id,)).fetchone()
    now = time.time()
    for field, new in (("gender", gender), ("grade", grade)):
        if row[field] != new:
            conn.execute(f"UPDATE members SET {field} = ? WHERE id = ?", (new, member_id))
            conn.execute("INSERT INTO changes(member_id, by, field, old, new, at) VALUES (?, ?, ?, ?, ?, ?)",
                         (member_id, by, field, row[field], new, now))


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
    return conn.execute("""SELECT c.*, m.nickname FROM changes c JOIN members m ON m.id = c.member_id
                           ORDER BY c.at DESC, c.id DESC LIMIT ?""", (limit,)).fetchall()
