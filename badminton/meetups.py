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


def _playing(conn, meetup_id: int) -> set[int]:
    return {pid for row in conn.execute(ACTIVE, (meetup_id,)) for pid in row}


def _reserved(conn, meetup_id: int) -> set[int]:
    return {pid for row in conn.execute("SELECT a1, a2, b1, b2 FROM reservations WHERE meetup_id = ?", (meetup_id,)) for pid in row}


def _drop_reservations_with(conn, meetup_id: int, member_ids: list[int] | set[int]) -> bool:
    if not member_ids:
        return False
    placeholders = ",".join("?" for _ in member_ids)
    rows = conn.execute(
        f"SELECT id FROM reservations WHERE meetup_id = ? AND "
        f"(a1 IN ({placeholders}) OR a2 IN ({placeholders}) OR b1 IN ({placeholders}) OR b2 IN ({placeholders}))",
        (meetup_id, *member_ids, *member_ids, *member_ids, *member_ids)).fetchall()
    if not rows:
        return False
    for r in rows:
        conn.execute("DELETE FROM reservations WHERE id = ?", (r["id"],))
    _renumber_reservations(conn, meetup_id)
    return True


def _renumber_reservations(conn, meetup_id: int) -> None:
    rows = conn.execute("SELECT id, seq FROM reservations WHERE meetup_id = ? ORDER BY seq, id", (meetup_id,)).fetchall()
    for idx, r in enumerate(rows, start=1):
        if r["seq"] != idx:
            conn.execute("UPDATE reservations SET seq = ? WHERE id = ?", (idx, r["id"]))


def reservations(conn, meetup_id: int) -> list:
    return conn.execute("SELECT * FROM reservations WHERE meetup_id = ? ORDER BY seq, id", (meetup_id,)).fetchall()


def queue(conn, meetup_id: int) -> list[Player]:
    busy = _playing(conn, meetup_id) | _reserved(conn, meetup_id)
    rows = conn.execute("""SELECT m.id, m.gender, m.grade, a.games, a.queued_at
                           FROM attendees a JOIN members m ON m.id = a.member_id
                           WHERE a.meetup_id = ? AND a.status = 'present'
                             AND m.gender IS NOT NULL AND m.grade IS NOT NULL""", (meetup_id,))
    return sort_queue([Player(r["id"], r["gender"], r["grade"], r["games"], r["queued_at"])
                       for r in rows if r["id"] not in busy])


def manual_candidates(conn, meetup_id: int) -> list:
    """현재 참석 중이며 다른 코트에서 경기 중이 아니고 성별과 급수가 입력된 회원들."""
    playing = _playing(conn, meetup_id)
    rows = conn.execute("""SELECT m.id, m.nickname, m.gender, m.grade, a.games
                           FROM attendees a JOIN members m ON m.id = a.member_id
                           WHERE a.meetup_id = ? AND a.status = 'present'
                             AND m.gender IS NOT NULL AND m.grade IS NOT NULL
                           ORDER BY m.nickname, m.id""", (meetup_id,)).fetchall()
    return [r for r in rows if r["id"] not in playing]


def _recent_mates(conn, meetup_id: int) -> dict[int, set[int]]:
    """사람마다 가장 최근에 끝난 경기에서 같이 친 3명 (다시 뽑기로 취소된 경기는 제외)."""
    mates = {}
    for g in conn.execute("SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND ended_at IS NOT NULL "
                          "AND canceled = 0 ORDER BY ended_at, id", (meetup_id,)):
        four = set(g)
        for p in four:
            mates[p] = four - {p}
    return mates


def refill_reservations(conn, meetup_id: int) -> None:
    meetup = conn.execute("SELECT courts, started FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    if meetup is None or not meetup["started"]:
        return
    courts = meetup["courts"]
    current_count = conn.execute("SELECT count(*) FROM reservations WHERE meetup_id = ?", (meetup_id,)).fetchone()[0]
    if current_count >= courts:
        return
    window, offset = int(get_setting(conn, "mix_window")), get_setting(conn, "female_offset")
    recent = _recent_mates(conn, meetup_id)
    playing = _playing(conn, meetup_id)
    while current_count < courts:
        q = queue(conn, meetup_id)
        if len(q) < 4:
            break
        four = pick_four(q, window, offset, recent)
        if four is None:
            break
        # 새로 만드는 예약이 1순위 예약(current_count == 0)인데,
        # 코트에서 다른 경기가 진행 중이고,
        # 뽑힌 4명이 직전 경기에서 함께 뛴 멤버와 완전히 동일하다면
        # 코트 경기가 끝나고 선수들이 복귀했을 때 섞일 수 있도록 예약을 보류한다.
        if current_count == 0 and playing and recent:
            four_ids = {p.id for p in four}
            if all(four_ids - {p.id} <= recent.get(p.id, set()) for p in four):
                break
        (a1, a2), (b1, b2) = split_teams(four, offset)
        current_count += 1
        conn.execute(
            """INSERT INTO reservations(meetup_id, seq, a1, a2, b1, b2, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (meetup_id, current_count, a1.id, a2.id, b1.id, b2.id, time.time())
        )


def redraw_reservation(conn, reservation_id: int) -> int | None:
    row = conn.execute("SELECT meetup_id FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if row is None:
        return None
    meetup_id = row["meetup_id"]
    conn.execute("DELETE FROM reservations WHERE id = ?", (reservation_id,))
    _renumber_reservations(conn, meetup_id)
    refill_reservations(conn, meetup_id)
    return meetup_id


def fill_courts(conn, meetup_id: int) -> None:
    courts = conn.execute("SELECT courts FROM meetups WHERE id = ?", (meetup_id,)).fetchone()["courts"]
    busy = {r["court"] for r in conn.execute("SELECT court FROM games WHERE meetup_id = ? AND ended_at IS NULL",
                                             (meetup_id,))}
    window, offset = int(get_setting(conn, "mix_window")), get_setting(conn, "female_offset")
    recent = _recent_mates(conn, meetup_id)
    for court in range(1, courts + 1):
        if court in busy:
            continue
        # 1. 예약이 있으면 가장 앞 예약(seq 최소)을 코트 경기로 옮긴다
        resv = conn.execute("SELECT * FROM reservations WHERE meetup_id = ? ORDER BY seq, id LIMIT 1",
                            (meetup_id,)).fetchone()
        if resv is not None:
            conn.execute("INSERT INTO games(meetup_id, court, a1, a2, b1, b2, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (meetup_id, court, resv["a1"], resv["a2"], resv["b1"], resv["b2"], time.time()))
            conn.execute("DELETE FROM reservations WHERE id = ?", (resv["id"],))
            _renumber_reservations(conn, meetup_id)
            continue

        # 2. 예약이 없으면 바로 pick_four
        four = pick_four(queue(conn, meetup_id), window, offset, recent)
        if four is None:
            break
        (a1, a2), (b1, b2) = split_teams(four, offset)
        conn.execute("INSERT INTO games(meetup_id, court, a1, a2, b1, b2, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (meetup_id, court, a1.id, a2.id, b1.id, b2.id, time.time()))

    # 빈 코트를 채운 뒤 남는 예약 칸을 채운다
    refill_reservations(conn, meetup_id)


def start(conn, meetup_id: int, courts: int) -> None:
    conn.execute("UPDATE meetups SET courts = ?, started = 1 WHERE id = ?", (courts, meetup_id))
    fill_courts(conn, meetup_id)


def assign_manual(conn, meetup_id: int, court: int, player_ids: list[int]) -> None:
    meetup = conn.execute("SELECT courts, started FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    if meetup is None or not meetup["started"]:
        raise ValueError("먼저 코트를 시작해 주세요")
    if not 1 <= court <= meetup["courts"]:
        raise ValueError("현재 운영 중인 코트만 선택할 수 있습니다")
    if len(player_ids) != 4 or len(set(player_ids)) != 4:
        raise ValueError("서로 다른 선수 네 명을 선택해 주세요")
    if conn.execute("SELECT 1 FROM games WHERE meetup_id = ? AND court = ? AND ended_at IS NULL",
                    (meetup_id, court)).fetchone():
        raise ValueError("빈 코트에만 수동 배정할 수 있습니다")

    placeholders = ",".join("?" for _ in player_ids)
    present = {row["member_id"] for row in conn.execute(
        f"SELECT member_id FROM attendees WHERE meetup_id = ? AND status = 'present' "
        f"AND member_id IN ({placeholders})", (meetup_id, *player_ids))}
    if present != set(player_ids):
        raise ValueError("현재 참석 중인 선수만 배정할 수 있습니다")
    incomplete = conn.execute(
        f"SELECT count(*) FROM members WHERE id IN ({placeholders}) "
        f"AND (gender IS NULL OR grade IS NULL)", (*player_ids,)).fetchone()[0]
    if incomplete > 0:
        raise ValueError("성별과 급수가 모두 입력된 선수만 배정할 수 있습니다")
    if set(player_ids) & _playing(conn, meetup_id):
        raise ValueError("다른 코트에서 경기 중인 선수는 선택할 수 없습니다")

    # 수동 대진으로 들어간 선수가 예약에 포함되어 있다면 그 예약을 지운다
    _drop_reservations_with(conn, meetup_id, player_ids)

    conn.execute("""INSERT INTO games(meetup_id, court, a1, a2, b1, b2, started_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                 (meetup_id, court, *player_ids, time.time()))
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
    # canceled=1로 표시하여 직전 경기 파트너/상대 벌점 계산에서 제외한다
    conn.execute("UPDATE games SET ended_at = ?, canceled = 1 WHERE id = ?", (time.time(), game_id))
    fill_courts(conn, g["meetup_id"])


def board(conn, meetup_id: int) -> dict:
    member_by_id = {r["id"]: {"id": r["id"], "nickname": r["nickname"], "gender": r["gender"], "grade": r["grade"]} for r in conn.execute(
        "SELECT m.id, m.nickname, m.gender, m.grade FROM members m JOIN attendees a ON a.member_id = m.id WHERE a.meetup_id = ?",
        (meetup_id,))}
    names = {member_id: member["nickname"] for member_id, member in member_by_id.items()}
    courts = conn.execute("SELECT courts FROM meetups WHERE id = ?", (meetup_id,)).fetchone()["courts"]
    active = {r["court"]: r for r in conn.execute("SELECT * FROM games WHERE meetup_id = ? AND ended_at IS NULL",
                                                  (meetup_id,))}
    court_list = []
    for c in sorted(set(range(1, courts + 1)) | set(active)):  # 코트 수를 줄여도 진행 중인 경기는 보여 준다
        g = active.get(c)
        court_list.append({"court": c, "game_id": g["id"] if g else None,
                           "teams": [[names[g["a1"]], names[g["a2"]]], [names[g["b1"]], names[g["b2"]]]] if g else None,
                           "team_players": [[member_by_id[g["a1"]], member_by_id[g["a2"]]],
                                            [member_by_id[g["b1"]], member_by_id[g["b2"]]]] if g else None})
    playing = _playing(conn, meetup_id)
    excluded = []
    for r in conn.execute("""SELECT m.id, m.nickname, m.gender, m.grade, a.status FROM attendees a
                             JOIN members m ON m.id = a.member_id WHERE a.meetup_id = ? ORDER BY m.nickname""",
                          (meetup_id,)):
        if r["id"] in playing:
            continue
        if r["status"] != "present":
            excluded.append({"id": r["id"], "nickname": r["nickname"], "gender": r["gender"], "grade": r["grade"], "reason": STATUS_LABELS[r["status"]]})
        elif r["grade"] is None:
            excluded.append({"id": r["id"], "nickname": r["nickname"], "gender": r["gender"], "grade": r["grade"], "reason": "급수 미입력"})
        elif r["gender"] is None:
            excluded.append({"id": r["id"], "nickname": r["nickname"], "gender": r["gender"], "grade": r["grade"], "reason": "성별 미입력"})
    queued = queue(conn, meetup_id)
    now = time.time()
    queue_players = []
    for player in queued:
        minutes = max(0, int((now - player.queued_at) // 60))
        wait_label = f"{minutes // 60}시간 {minutes % 60}분" if minutes >= 60 else f"{minutes}분"
        queue_players.append({**member_by_id[player.id], "games": player.games,
                              "queued_at": player.queued_at, "wait_label": wait_label})
    resv_list = []
    for r in reservations(conn, meetup_id):
        resv_list.append({
            "id": r["id"],
            "no": r["seq"],
            "teams": [[names.get(r["a1"], "?"), names.get(r["a2"], "?")],
                      [names.get(r["b1"], "?"), names.get(r["b2"], "?")]],
            "team_players": [[member_by_id.get(r["a1"]), member_by_id.get(r["a2"])],
                             [member_by_id.get(r["b1"]), member_by_id.get(r["b2"])]],
        })
    return {"courts": court_list, "reservations": resv_list, "queue": [names[p.id] for p in queued],
            "queue_players": queue_players, "excluded": excluded}


def refill_upcoming(conn) -> None:
    """오늘 이후 시작된 정모의 빈 코트를 대기열로 채운다."""
    for r in conn.execute("SELECT id FROM meetups WHERE date >= ? AND started = 1 ORDER BY date, id",
                          (today(),)).fetchall():
        fill_courts(conn, r["id"])


def on_info_changed(conn, member_id: int, changed: list[tuple], meetup_id: int | None = None) -> None:
    """changed는 members.set_info가 돌려준 (필드, 이전 값, 새 값) 목록.
    비어 있던 성별/급수가 이번에 채워졌을 때만 대기열 맨 뒤로 넣는다 (대기 중인 사람의 급수 수정은 순서 유지)."""
    if any(new is None for _, _, new in changed):
        # 성별 또는 급수가 비워진 경우 예약에서 제거
        if meetup_id:
            if _drop_reservations_with(conn, meetup_id, [member_id]):
                refill_reservations(conn, meetup_id)
        else:
            for r in conn.execute("SELECT meetup_id FROM reservations WHERE a1 = ? OR a2 = ? OR b1 = ? OR b2 = ?",
                                  (member_id, member_id, member_id, member_id)).fetchall():
                if _drop_reservations_with(conn, r["meetup_id"], [member_id]):
                    refill_reservations(conn, r["meetup_id"])
    if any(old is None for _, old, _ in changed):
        now = time.time()
        if meetup_id:
            conn.execute("UPDATE attendees SET queued_at = ? WHERE member_id = ? AND status = 'present' "
                         "AND meetup_id = ?", (now, member_id, meetup_id))
        else:
            conn.execute("""UPDATE attendees SET queued_at = ? WHERE member_id = ? AND status = 'present'
                            AND meetup_id IN (SELECT id FROM meetups WHERE date >= ?)""", (now, member_id, today()))
    refill_upcoming(conn)
