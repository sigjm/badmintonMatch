"""참석자: 참석 추가, 소모임 동기화, 상태·메모, 수동 대진 후보, 정보 변경 반영."""

import time

from ..members import create_member, upsert_somoim
from ..somoim import SomoimMember
from .common import MEMO_MAX, STATUS_LABELS, _playing, _reserved, today
from .scheduling import _drop_reservations_with, refill_reservations, refill_upcoming


def add_attendee(conn, meetup_id: int, member_id: int, source: str) -> None:
    row = conn.execute("SELECT status FROM attendees WHERE meetup_id = ? AND member_id = ?",
                       (meetup_id, member_id)).fetchone()
    now = time.time()
    if row is None:
        conn.execute("INSERT INTO attendees(meetup_id, member_id, status, source, queued_at) "
                     "VALUES (?, ?, 'present', ?, ?)", (meetup_id, member_id, source, now))
    elif row["status"] != "present":
        conn.execute("UPDATE attendees SET status = 'present', queued_at = ? WHERE meetup_id = ? AND member_id = ?",
                     (now, meetup_id, member_id))


def sync_somoim(conn, meetup_id: int, members: list[SomoimMember]) -> dict:
    result = {"added": 0, "canceled": 0, "restored": 0}
    present_ids = set()
    for m in members:
        member_id = upsert_somoim(conn, m.mid, m.nickname)
        present_ids.add(member_id)
        row = conn.execute("SELECT status FROM attendees WHERE meetup_id = ? AND member_id = ?",
                           (meetup_id, member_id)).fetchone()
        if row is None:
            add_attendee(conn, meetup_id, member_id, "somoim")
            result["added"] += 1
        elif row["status"] == "canceled":
            conn.execute("UPDATE attendees SET status = 'present', queued_at = ? WHERE meetup_id = ? AND member_id = ?",
                         (time.time(), meetup_id, member_id))
            result["restored"] += 1
    rows = conn.execute("SELECT member_id FROM attendees WHERE meetup_id = ? AND source = 'somoim' "
                        "AND status != 'canceled'", (meetup_id,)).fetchall()
    canceled_ids = []
    for r in rows:
        if r["member_id"] not in present_ids:
            conn.execute("UPDATE attendees SET status = 'canceled' WHERE meetup_id = ? AND member_id = ?",
                         (meetup_id, r["member_id"]))
            canceled_ids.append(r["member_id"])
            result["canceled"] += 1
    if canceled_ids:
        _drop_reservations_with(conn, meetup_id, canceled_ids)
        refill_reservations(conn, meetup_id)
    return result


def add_pasted(conn, meetup_id: int, text: str) -> dict:
    result = {"added": [], "created": [], "ambiguous": []}
    for nick in dict.fromkeys(line.strip() for line in text.splitlines() if line.strip()):
        ids = [r["id"] for r in conn.execute("SELECT id FROM members WHERE nickname = ? AND is_guest = 0", (nick,))]
        if len(ids) > 1:
            result["ambiguous"].append(nick)
            continue
        if ids:
            result["added"].append(nick)
        else:
            ids = [create_member(conn, nick)]
            result["created"].append(nick)
        add_attendee(conn, meetup_id, ids[0], "paste")
    return result


def set_status(conn, meetup_id: int, member_id: int, status: str) -> None:
    if status not in STATUS_LABELS:
        raise ValueError(f"알 수 없는 상태: {status}")
    row = conn.execute("SELECT status FROM attendees WHERE meetup_id = ? AND member_id = ?",
                       (meetup_id, member_id)).fetchone()
    if row is None or row["status"] == status:
        return
    if status == "present":
        conn.execute("UPDATE attendees SET status = ?, queued_at = ? WHERE meetup_id = ? AND member_id = ?",
                     (status, time.time(), meetup_id, member_id))
    else:
        conn.execute("UPDATE attendees SET status = ? WHERE meetup_id = ? AND member_id = ?",
                     (status, meetup_id, member_id))
        if _drop_reservations_with(conn, meetup_id, [member_id]):
            refill_reservations(conn, meetup_id)


def set_memo(conn, meetup_id: int, member_id: int, memo: str) -> None:
    memo = (memo or "").strip()
    if len(memo) > MEMO_MAX:
        raise ValueError(f"메모는 {MEMO_MAX}자까지 쓸 수 있습니다")
    conn.execute("UPDATE attendees SET memo = ? WHERE meetup_id = ? AND member_id = ?",
                 (memo or None, meetup_id, member_id))


def manual_candidates(conn, meetup_id: int) -> list:
    """현재 참석 중이며 경기 중이거나 다른 예약에 든 선수를 뺀 수동 선택 후보."""
    busy = _playing(conn, meetup_id) | _reserved(conn, meetup_id)
    rows = conn.execute("""SELECT m.id, m.nickname, m.gender, m.grade, a.games, a.memo
                           FROM attendees a JOIN members m ON m.id = a.member_id
                           WHERE a.meetup_id = ? AND a.status = 'present'
                           ORDER BY m.nickname, m.id""", (meetup_id,)).fetchall()
    return [r for r in rows if r["id"] not in busy]


def playing_candidates(conn, meetup_id: int) -> list:
    """현재 경기 중이고 아직 예약되지 않은 참석자."""
    playing = _playing(conn, meetup_id)
    reserved = _reserved(conn, meetup_id)
    rows = conn.execute("""SELECT m.id, m.nickname, m.gender, m.grade, a.games, a.memo
                           FROM attendees a JOIN members m ON m.id = a.member_id
                           WHERE a.meetup_id = ? AND a.status = 'present'
                           ORDER BY m.nickname, m.id""", (meetup_id,)).fetchall()
    return [{**dict(row), "state": "playing"} for row in rows
            if row["id"] in playing and row["id"] not in reserved]


def on_info_changed(conn, member_id: int, changed: list[tuple], meetup_id: int | None = None) -> None:
    """회원 정보는 정모 간 공유되므로 모든 활성 정모의 예약을 갱신한다.
    meetup_id는 기존 호출 호환을 위해 받지만 범위를 제한하지 않는다.
    비어 있던 성별/급수가 이번에 채워졌을 때만 대기열 맨 뒤로 넣는다."""
    if any(field == "gender" or new is None for field, _, new in changed):
        # 성별이 바뀌거나 성별/급수가 비워진 경우 예약에서 제거 (성비 불일치 3:1/1:3 방지)
        for r in conn.execute("SELECT DISTINCT r.meetup_id FROM reservations r "
                              "JOIN meetups mt ON mt.id = r.meetup_id "
                              "WHERE mt.archived_at IS NULL AND r.source = 'auto' "
                              "AND (r.a1 = ? OR r.a2 = ? OR r.b1 = ? OR r.b2 = ?)",
                              (member_id, member_id, member_id, member_id)).fetchall():
            if _drop_reservations_with(conn, r["meetup_id"], [member_id], source="auto"):
                refill_reservations(conn, r["meetup_id"])
    if any(old is None for _, old, _ in changed):
        now = time.time()
        conn.execute("""UPDATE attendees SET queued_at = ? WHERE member_id = ? AND status = 'present'
                        AND meetup_id IN (SELECT id FROM meetups WHERE date >= ? AND archived_at IS NULL)""",
                     (now, member_id, today()))
    refill_upcoming(conn)
