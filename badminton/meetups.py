import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .db import get_setting
from .matching import Player, pick_four, sort_queue, split_teams
from .members import create_member, upsert_somoim
from .somoim import SomoimMember

STATUS_LABELS = {"present": "참석", "late": "지각", "away": "자리 비움", "gone": "귀가", "canceled": "취소"}
SEOUL = ZoneInfo("Asia/Seoul")
ACTIVE = "SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND ended_at IS NULL"


def session_day(now: datetime) -> str:
    # ponytail: 새벽 6시 전은 전날 정모로 본다 (22시 정모가 자정을 넘김). 새벽 정모가 생기면 정모별 종료 시각으로 바꿀 것
    return (now - timedelta(hours=6)).date().isoformat()


def today() -> str:
    return session_day(datetime.now(SEOUL))


def get_or_create_meetup(conn, date: str, name: str, eid: str | None, group_id: int | None = None) -> int:
    if eid:
        row = conn.execute("SELECT id FROM meetups WHERE eid = ?", (eid,)).fetchone()
    else:
        row = conn.execute("SELECT id FROM meetups WHERE date = ? AND name = ? AND eid IS NULL AND group_id IS ?",
                           (date, name, group_id)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO meetups(date, name, eid, group_id) VALUES (?, ?, ?, ?)",
                        (date, name, eid, group_id)).lastrowid


def add_attendee(conn, meetup_id: int, member_id: int, source: str) -> None:
    conn.execute("INSERT OR IGNORE INTO attendees(meetup_id, member_id, status, source, queued_at) "
                 "VALUES (?, ?, 'present', ?, ?)", (meetup_id, member_id, source, time.time()))


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
    for r in rows:
        if r["member_id"] not in present_ids:
            conn.execute("UPDATE attendees SET status = 'canceled' WHERE meetup_id = ? AND member_id = ?",
                         (meetup_id, r["member_id"]))
            result["canceled"] += 1
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


def _playing(conn, meetup_id: int) -> set[int]:
    return {pid for row in conn.execute(ACTIVE, (meetup_id,)) for pid in row}


def queue(conn, meetup_id: int) -> list[Player]:
    playing = _playing(conn, meetup_id)
    rows = conn.execute("""SELECT m.id, m.gender, m.grade, a.games, a.queued_at
                           FROM attendees a JOIN members m ON m.id = a.member_id
                           WHERE a.meetup_id = ? AND a.status = 'present'
                             AND m.gender IS NOT NULL AND m.grade IS NOT NULL""", (meetup_id,))
    return sort_queue([Player(r["id"], r["gender"], r["grade"], r["games"], r["queued_at"])
                       for r in rows if r["id"] not in playing])


def _recent_mates(conn, meetup_id: int) -> dict[int, set[int]]:
    """사람마다 가장 최근에 끝난 경기에서 같이 친 3명."""
    mates = {}
    for g in conn.execute("SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND ended_at IS NOT NULL "
                          "ORDER BY ended_at, id", (meetup_id,)):
        four = set(g)
        for p in four:
            mates[p] = four - {p}
    return mates


def fill_courts(conn, meetup_id: int) -> None:
    courts = conn.execute("SELECT courts FROM meetups WHERE id = ?", (meetup_id,)).fetchone()["courts"]
    busy = {r["court"] for r in conn.execute("SELECT court FROM games WHERE meetup_id = ? AND ended_at IS NULL",
                                             (meetup_id,))}
    window, offset = int(get_setting(conn, "mix_window")), get_setting(conn, "female_offset")
    recent = _recent_mates(conn, meetup_id)
    for court in range(1, courts + 1):
        if court in busy:
            continue
        four = pick_four(queue(conn, meetup_id), window, offset, recent)
        if four is None:
            return
        (a1, a2), (b1, b2) = split_teams(four, offset)
        conn.execute("INSERT INTO games(meetup_id, court, a1, a2, b1, b2, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (meetup_id, court, a1.id, a2.id, b1.id, b2.id, time.time()))


def start(conn, meetup_id: int, courts: int) -> None:
    conn.execute("UPDATE meetups SET courts = ?, started = 1 WHERE id = ?", (courts, meetup_id))
    fill_courts(conn, meetup_id)


def _game(conn, game_id: int):
    return conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()


def end_game(conn, game_id: int) -> None:
    g = _game(conn, game_id)
    if g is None or g["ended_at"] is not None:
        return
    now = time.time()
    conn.execute("UPDATE games SET ended_at = ? WHERE id = ?", (now, game_id))
    conn.execute("UPDATE attendees SET games = games + 1, queued_at = ? WHERE meetup_id = ? AND member_id IN (?, ?, ?, ?)",
                 (now, g["meetup_id"], g["a1"], g["a2"], g["b1"], g["b2"]))
    fill_courts(conn, g["meetup_id"])


def redraw(conn, game_id: int) -> None:
    g = _game(conn, game_id)
    if g is None or g["ended_at"] is not None:
        return
    # 지우지 않고 끝난 것으로 표시: 새 경기가 같은 id를 받아 다른 폰의 오래된 "경기 끝"이 새 경기를 끝내는 일을 막는다
    conn.execute("UPDATE games SET ended_at = ? WHERE id = ?", (time.time(), game_id))
    fill_courts(conn, g["meetup_id"])


def board(conn, meetup_id: int) -> dict:
    names = {r["id"]: r["nickname"] for r in conn.execute(
        "SELECT m.id, m.nickname FROM members m JOIN attendees a ON a.member_id = m.id WHERE a.meetup_id = ?",
        (meetup_id,))}
    courts = conn.execute("SELECT courts FROM meetups WHERE id = ?", (meetup_id,)).fetchone()["courts"]
    active = {r["court"]: r for r in conn.execute("SELECT * FROM games WHERE meetup_id = ? AND ended_at IS NULL",
                                                  (meetup_id,))}
    court_list = []
    for c in sorted(set(range(1, courts + 1)) | set(active)):  # 코트 수를 줄여도 진행 중인 경기는 보여 준다
        g = active.get(c)
        court_list.append({"court": c, "game_id": g["id"] if g else None,
                           "teams": [[names[g["a1"]], names[g["a2"]]], [names[g["b1"]], names[g["b2"]]]] if g else None})
    excluded = []
    for r in conn.execute("""SELECT m.nickname, m.gender, m.grade, a.status FROM attendees a
                             JOIN members m ON m.id = a.member_id WHERE a.meetup_id = ? ORDER BY m.nickname""",
                          (meetup_id,)):
        if r["status"] != "present":
            excluded.append({"nickname": r["nickname"], "reason": STATUS_LABELS[r["status"]]})
        elif r["grade"] is None:
            excluded.append({"nickname": r["nickname"], "reason": "급수 미입력"})
        elif r["gender"] is None:
            excluded.append({"nickname": r["nickname"], "reason": "성별 미입력"})
    return {"courts": court_list, "queue": [names[p.id] for p in queue(conn, meetup_id)], "excluded": excluded}


def refill_today(conn) -> None:
    for r in conn.execute("SELECT id FROM meetups WHERE date = ? AND started = 1", (today(),)).fetchall():
        fill_courts(conn, r["id"])


def on_info_changed(conn, member_id: int, changed: list[tuple]) -> None:
    """changed는 members.set_info가 돌려준 (필드, 이전 값, 새 값) 목록.
    비어 있던 성별/급수가 이번에 채워졌을 때만 대기열 맨 뒤로 넣는다 (대기 중인 사람의 급수 수정은 순서 유지)."""
    if any(old is None for _, old, _ in changed):
        conn.execute("""UPDATE attendees SET queued_at = ? WHERE member_id = ? AND status = 'present'
                        AND meetup_id IN (SELECT id FROM meetups WHERE date = ?)""", (time.time(), member_id, today()))
    refill_today(conn)
