import math
import time
from datetime import date as date_type, datetime, timedelta
from zoneinfo import ZoneInfo

from .announce import announcement_text
from .db import get_setting, transaction
from .matching import Player, pick_four, sort_queue, split_teams
from .members import create_member, upsert_somoim
from .somoim import SomoimMember

STATUS_LABELS = {"present": "참석", "late": "지각", "away": "휴식", "gone": "퇴장", "canceled": "취소"}
EXCLUDED_ORDER = {"noinfo": 0, "away": 1, "late": 2, "gone": 3, "canceled": 4}
SEOUL = ZoneInfo("Asia/Seoul")
ACTIVE = "SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND ended_at IS NULL"
COURT_REST_SECONDS = 15  # 경기를 끝낸 코트는 이만큼 비워 둔다 (선수 교대 시간)
MEMO_MAX = 100


class ArchivedMeetupError(ValueError):
    def __init__(self, meetup_id: int):
        super().__init__("보관된 정모입니다. 먼저 복구해 주세요")
        self.meetup_id = meetup_id


def session_day(now: datetime) -> str:
    # ponytail: 새벽 6시 전은 전날 정모로 본다 (22시 정모가 자정을 넘김). 새벽 정모가 생기면 정모별 종료 시각으로 바꿀 것
    return (now - timedelta(hours=6)).date().isoformat()


def today() -> str:
    return session_day(datetime.now(SEOUL))


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


def _rest_until(conn, meetup_id: int) -> dict[int, float]:
    """코트별로 다음 경기를 넣어도 되는 시각 (다시 뽑기로 취소된 경기는 대기 없이 바로 채운다)."""
    return {r["court"]: r["ended"] + COURT_REST_SECONDS for r in conn.execute(
        "SELECT court, max(ended_at) AS ended FROM games WHERE meetup_id = ? AND ended_at IS NOT NULL "
        "AND canceled = 0 GROUP BY court", (meetup_id,))}


def _playing(conn, meetup_id: int) -> set[int]:
    return {pid for row in conn.execute(ACTIVE, (meetup_id,)) for pid in row}


def _reserved(conn, meetup_id: int) -> set[int]:
    return {pid for row in conn.execute("SELECT a1, a2, b1, b2 FROM reservations WHERE meetup_id = ?", (meetup_id,)) for pid in row}


def _drop_reservations_with(conn, meetup_id: int, member_ids: list[int] | set[int], *, source: str | None = None) -> bool:
    if not member_ids:
        return False
    placeholders = ",".join("?" for _ in member_ids)
    source_sql = " AND source = ?" if source else ""
    rows = conn.execute(
        f"SELECT id FROM reservations WHERE meetup_id = ? AND "
        f"(a1 IN ({placeholders}) OR a2 IN ({placeholders}) OR b1 IN ({placeholders}) OR b2 IN ({placeholders}))"
        f"{source_sql}",
        (meetup_id, *member_ids, *member_ids, *member_ids, *member_ids, *((source,) if source else ()))).fetchall()
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


def _recent_mates(conn, meetup_id: int) -> dict[int, set[int]]:
    """사람마다 가장 최근에 끝난 경기에서 같이 친 3명 (다시 뽑기로 취소된 경기는 제외)."""
    mates = {}
    for g in conn.execute("SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND ended_at IS NOT NULL "
                          "AND canceled = 0 ORDER BY ended_at, id", (meetup_id,)):
        four = set(g)
        for p in four:
            mates[p] = four - {p}
    return mates


def refill_reservations(conn, meetup_id: int, avoid: frozenset[int] | None = None) -> None:
    meetup = conn.execute("SELECT courts, started, resv_hold, archived_at FROM meetups WHERE id = ?",
                          (meetup_id,)).fetchone()
    if meetup is None or meetup["archived_at"] is not None or not meetup["started"]:
        return

    if not int(get_setting(conn, "auto_reserve", 1)):
        deleted = conn.execute("DELETE FROM reservations WHERE meetup_id = ? AND source = 'auto'",
                               (meetup_id,)).rowcount
        if deleted > 0:
            _renumber_reservations(conn, meetup_id)
        return

    auto_rows = conn.execute("SELECT id FROM reservations WHERE meetup_id = ? AND source = 'auto' "
                             "ORDER BY seq, id", (meetup_id,)).fetchall()
    for row in auto_rows[max(0, meetup["courts"]):]:
        conn.execute("DELETE FROM reservations WHERE id = ?", (row["id"],))
    if len(auto_rows) > meetup["courts"]:
        _renumber_reservations(conn, meetup_id)
    if meetup["resv_hold"]:  # 운영진이 예약을 삭제했으면 다음 경기가 코트에 들어갈 때까지 새 예약을 만들지 않는다
        return
    courts = meetup["courts"]
    auto_count = conn.execute("SELECT count(*) FROM reservations WHERE meetup_id = ? AND source = 'auto'",
                              (meetup_id,)).fetchone()[0]
    if auto_count >= courts:
        return
    window, offset = int(get_setting(conn, "mix_window")), get_setting(conn, "female_offset")
    recent = _recent_mates(conn, meetup_id)
    playing = _playing(conn, meetup_id)
    while auto_count < courts:
        q = queue(conn, meetup_id)
        if len(q) < 8:
            break
        four = pick_four(q, window, offset, recent, avoid)
        if four is None:
            break
        # 예약이 아직 없는데 새 매칭이 직전 경기와 완전히 같으면,
        # 코트에서 다른 경기가 진행 중이고,
        # 뽑힌 4명이 직전 경기에서 함께 뛴 멤버와 완전히 동일하다면
        # 코트 경기가 끝나고 선수들이 복귀했을 때 섞일 수 있도록 예약을 보류한다.
        if auto_count == 0 and playing and recent:  # 수동 예약은 운영진이 고른 것이라 따지지 않는다
            four_ids = {p.id for p in four}
            if all(four_ids - {p.id} <= recent.get(p.id, set()) for p in four):
                break
        (a1, a2), (b1, b2) = split_teams(four, offset)
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM reservations WHERE meetup_id = ?",
                           (meetup_id,)).fetchone()[0]
        conn.execute(
            """INSERT INTO reservations(meetup_id, seq, a1, a2, b1, b2, created_at, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'auto')""",
            (meetup_id, seq, a1.id, a2.id, b1.id, b2.id, time.time())
        )
        auto_count += 1


def add_manual_reservation(conn, meetup_id: int, player_ids: list[int]) -> int:
    meetup = conn.execute("SELECT started, archived_at FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    if meetup is None or meetup["archived_at"] is not None:
        raise ValueError("활성 정모를 찾을 수 없습니다")
    if not meetup["started"]:
        raise ValueError("먼저 코트를 시작해 주세요")
    if len(player_ids) != 4 or len(set(player_ids)) != 4:
        raise ValueError("서로 다른 선수 네 명을 선택해 주세요")

    placeholders = ",".join("?" for _ in player_ids)
    present = {row["member_id"] for row in conn.execute(
        f"SELECT member_id FROM attendees WHERE meetup_id = ? AND status = 'present' "
        f"AND member_id IN ({placeholders})", (meetup_id, *player_ids))}
    if present != set(player_ids):
        raise ValueError("현재 참석 중인 선수만 예약할 수 있습니다")
    if set(player_ids) & _reserved(conn, meetup_id):
        raise ValueError("이미 예약된 선수는 다시 선택할 수 없습니다")

    seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM reservations WHERE meetup_id = ?",
                       (meetup_id,)).fetchone()[0]
    reservation_id = conn.execute(
        """INSERT INTO reservations(meetup_id, seq, a1, a2, b1, b2, created_at, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'manual')""",
        (meetup_id, seq, *player_ids, time.time())).lastrowid
    refill_reservations(conn, meetup_id)
    return reservation_id


def reservation_waiting_for_game(conn, reservation_id: int) -> bool:
    row = conn.execute("SELECT meetup_id, a1, a2, b1, b2 FROM reservations WHERE id = ?",
                       (reservation_id,)).fetchone()
    return bool(row and {row[key] for key in ("a1", "a2", "b1", "b2")} & _playing(conn, row["meetup_id"]))


def update_reservation(conn, reservation_id: int, player_ids: list[int]) -> tuple[int, bool] | None:
    row = conn.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if row is None:
        return None
    meetup_id = row["meetup_id"]
    meetup = conn.execute("SELECT started, archived_at FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    if meetup is None or meetup["archived_at"] is not None:
        raise ValueError("활성 정모를 찾을 수 없습니다")
    if not meetup["started"]:
        raise ValueError("먼저 코트를 시작해 주세요")
    if len(player_ids) != 4 or len(set(player_ids)) != 4:
        raise ValueError("서로 다른 선수 네 명을 선택해 주세요")

    placeholders = ",".join("?" for _ in player_ids)
    present = {candidate["member_id"] for candidate in conn.execute(
        f"SELECT member_id FROM attendees WHERE meetup_id = ? AND status = 'present' "
        f"AND member_id IN ({placeholders})", (meetup_id, *player_ids))}
    if present != set(player_ids):
        raise ValueError("현재 참석 중인 선수만 예약할 수 있습니다")
    other_reserved = {player_id for other in conn.execute(
        "SELECT a1, a2, b1, b2 FROM reservations WHERE meetup_id = ? AND id != ?",
        (meetup_id, reservation_id)) for player_id in other}
    if set(player_ids) & other_reserved:
        raise ValueError("다른 예약에 포함된 선수는 선택할 수 없습니다")

    # An operator-edited lineup is now manually controlled, including its gender/grade mix.
    conn.execute("UPDATE reservations SET a1 = ?, a2 = ?, b1 = ?, b2 = ?, source = 'manual' WHERE id = ?",
                 (*player_ids, reservation_id))
    refill_reservations(conn, meetup_id)
    return meetup_id, bool(set(player_ids) & _playing(conn, meetup_id))


def prioritize_reservation(conn, reservation_id: int) -> int | None:
    row = conn.execute("SELECT meetup_id FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if row is None:
        return None
    meetup_id = row["meetup_id"]
    conn.execute("UPDATE reservations SET seq = 0 WHERE id = ?", (reservation_id,))
    conn.execute("UPDATE reservations SET seq = seq + 1 WHERE meetup_id = ? AND id != ?",
                 (meetup_id, reservation_id))
    _renumber_reservations(conn, meetup_id)
    return meetup_id


def delete_reservation(conn, reservation_id: int) -> int | None:
    """예약을 지우고 그 4명을 대기열로 돌린다. 다음 경기가 코트에 들어갈 때까지 새 예약을 만들지 않는다."""
    row = conn.execute("SELECT meetup_id FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM reservations WHERE id = ?", (reservation_id,))
    conn.execute("UPDATE meetups SET resv_hold = 1 WHERE id = ?", (row["meetup_id"],))
    _renumber_reservations(conn, row["meetup_id"])
    return row["meetup_id"]


def redraw_reservation(conn, reservation_id: int) -> int | None:
    row = conn.execute("SELECT meetup_id, source FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if row is None:
        return None
    if row["source"] == "manual":
        raise ValueError("수동 예약은 다시 뽑을 수 없습니다. 삭제 후 새로 예약해 주세요")
    meetup_id = row["meetup_id"]
    old = conn.execute("SELECT a1, a2, b1, b2 FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    conn.execute("DELETE FROM reservations WHERE id = ?", (reservation_id,))
    _renumber_reservations(conn, meetup_id)
    refill_reservations(conn, meetup_id, avoid=frozenset(old))
    return meetup_id


def _record_game_assignment(conn, meetup_id: int, game_id: int, court: int, player_ids: list[int],
                            source: str, reservation_id: int | None = None, sequence: int | None = None) -> None:
    from . import audit

    player_names = {r["id"]: r["nickname"] for r in conn.execute(
        "SELECT id, nickname FROM members WHERE id IN (?, ?, ?, ?)", player_ids)}
    names = [player_names.get(player_id, "?") for player_id in player_ids]
    source_labels = {"manual": "수동 예약", "auto": "자동 예약", "queue": "대기열 자동 매칭"}
    label = source_labels[source]
    audit.record(
        conn, action="game_assigned", summary=f"{label} 경기 배정: {court}코트 · " + ", ".join(names),
        entity_type="game", entity_id=game_id, meetup_id=meetup_id,
        details={"source": source, "reservation_id": reservation_id, "sequence": sequence,
                 "court": court, "players": names})


def _reservation_state(conn, meetup_id: int, reservation, playing: set[int]) -> str:
    player_ids = [reservation[key] for key in ("a1", "a2", "b1", "b2")]
    if len(set(player_ids)) != 4 or any(player_id is None for player_id in player_ids):
        return "invalid"
    if set(player_ids) & playing:
        return "playing"
    rows = conn.execute(
        "SELECT a.member_id, a.status, m.gender, m.grade FROM attendees a "
        "JOIN members m ON m.id = a.member_id WHERE a.meetup_id = ? AND a.member_id IN (?, ?, ?, ?)",
        (meetup_id, *player_ids)).fetchall()
    if len(rows) != 4 or any(row["status"] != "present" for row in rows):
        return "invalid"
    if reservation["source"] == "auto":
        if not all(row["gender"] is not None and row["grade"] is not None for row in rows):
            return "invalid"
        if sum(row["gender"] == "F" for row in rows) not in (0, 2, 4):
            return "invalid"
    return "ready"


def start_reservation_now(conn, reservation_id: int) -> tuple[int | None, str, int | None, int | None]:
    """지정한 예약을 빈 코트에 즉시 시작한다. 운영자 직접 투입은 15초 코트 휴식을 건너뛴다."""
    reservation = conn.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if reservation is None:
        return None, "missing", None, None
    meetup_id = reservation["meetup_id"]
    meetup = conn.execute("SELECT courts, started, archived_at FROM meetups WHERE id = ?",
                          (meetup_id,)).fetchone()
    if meetup is None or meetup["archived_at"] is not None or not meetup["started"]:
        return meetup_id, "inactive", None, None
    playing = _playing(conn, meetup_id)
    state = _reservation_state(conn, meetup_id, reservation, playing)
    if state == "playing":
        return meetup_id, "playing", None, None
    if state != "ready":
        return meetup_id, "invalid", None, None

    active_courts = {row["court"] for row in conn.execute(
        "SELECT court FROM games WHERE meetup_id = ? AND ended_at IS NULL", (meetup_id,))}
    court = next((number for number in range(1, meetup["courts"] + 1) if number not in active_courts), None)
    if court is None:
        return meetup_id, "no_court", None, None

    player_ids = [reservation[key] for key in ("a1", "a2", "b1", "b2")]
    with transaction(conn):
        game_id = conn.execute(
            "INSERT INTO games(meetup_id, court, a1, a2, b1, b2, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (meetup_id, court, *player_ids, time.time())).lastrowid
        conn.execute("DELETE FROM reservations WHERE id = ?", (reservation_id,))
        _renumber_reservations(conn, meetup_id)
        conn.execute("UPDATE meetups SET resv_hold = 0 WHERE id = ?", (meetup_id,))
        _record_game_assignment(conn, meetup_id, game_id, court, player_ids, reservation["source"],
                                reservation_id=reservation_id, sequence=reservation["seq"])
        refill_reservations(conn, meetup_id)
    return meetup_id, "started", game_id, court


def fill_courts(conn, meetup_id: int, force: bool = False, avoid: frozenset[int] | None = None) -> None:
    """빈 코트를 채운다. force가 아니면 방금 경기가 끝난 코트는 COURT_REST_SECONDS 동안 비워 둔다."""
    meetup = conn.execute("SELECT courts, archived_at FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    if meetup is None or meetup["archived_at"] is not None:
        return
    if not force and not int(get_setting(conn, "auto_fill", 1)):
        refill_reservations(conn, meetup_id, avoid)
        return
    courts = meetup["courts"]
    busy = {r["court"] for r in conn.execute("SELECT court FROM games WHERE meetup_id = ? AND ended_at IS NULL",
                                             (meetup_id,))}
    if not force:
        now = time.time()
        busy |= {c for c, until in _rest_until(conn, meetup_id).items() if until > now}
    window, offset = int(get_setting(conn, "mix_window")), get_setting(conn, "female_offset")
    recent = _recent_mates(conn, meetup_id)
    started_any = False
    for court in range(1, courts + 1):
        if court in busy:
            continue
        # 1. 예약이 있으면 가장 앞 예약(seq 최소)을 코트 경기로 옮긴다
        resv = None
        playing = _playing(conn, meetup_id)
        for candidate in conn.execute("SELECT * FROM reservations WHERE meetup_id = ? ORDER BY seq, id",
                                      (meetup_id,)).fetchall():
            state = _reservation_state(conn, meetup_id, candidate, playing)
            if state == "playing":
                continue
            if state == "invalid":
                conn.execute("DELETE FROM reservations WHERE id = ?", (candidate["id"],))
                _renumber_reservations(conn, meetup_id)
                continue
            resv = candidate
            break
        if resv is not None:
            resv_pids = [resv[key] for key in ("a1", "a2", "b1", "b2")]
            with transaction(conn):
                game_id = conn.execute(
                    "INSERT INTO games(meetup_id, court, a1, a2, b1, b2, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (meetup_id, court, resv["a1"], resv["a2"], resv["b1"], resv["b2"], time.time())
                ).lastrowid
                conn.execute("DELETE FROM reservations WHERE id = ?", (resv["id"],))
                _renumber_reservations(conn, meetup_id)
                _record_game_assignment(conn, meetup_id, game_id, court, resv_pids, resv["source"],
                                        reservation_id=resv["id"], sequence=resv["seq"])
            started_any = True
            continue

        # 2. 예약이 없으면 바로 pick_four
        four = pick_four(queue(conn, meetup_id), window, offset, recent, avoid)
        if four is None:
            break
        (a1, a2), (b1, b2) = split_teams(four, offset)
        player_ids = [a1.id, a2.id, b1.id, b2.id]
        with transaction(conn):
            game_id = conn.execute(
                "INSERT INTO games(meetup_id, court, a1, a2, b1, b2, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (meetup_id, court, *player_ids, time.time())
            ).lastrowid
            _record_game_assignment(conn, meetup_id, game_id, court, player_ids, "queue")
        started_any = True

    # 빈 코트를 채운 뒤 남는 예약 칸을 채운다. 경기가 새로 들어갔거나 운영진이 자동 매칭을 눌렀으면
    # 예약 삭제로 멈춰 둔 자동 예약을 다시 켠다
    if started_any or force:
        conn.execute("UPDATE meetups SET resv_hold = 0 WHERE id = ?", (meetup_id,))
    refill_reservations(conn, meetup_id)


def start(conn, meetup_id: int, courts: int) -> None:
    row = conn.execute("SELECT archived_at FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    if row is None or row["archived_at"] is not None:
        raise ValueError("활성 정모를 찾을 수 없습니다")
    conn.execute("UPDATE meetups SET courts = ?, started = 1, resv_hold = 0 WHERE id = ?", (courts, meetup_id))
    fill_courts(conn, meetup_id)


def _game(conn, game_id: int):
    return conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()


def end_game(conn, game_id: int) -> None:
    g = _game(conn, game_id)
    if g is None or g["ended_at"] is not None:
        return
    now = time.time()
    with transaction(conn):
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
    fill_courts(conn, g["meetup_id"], avoid=frozenset((g["a1"], g["a2"], g["b1"], g["b2"])))


def _minutes_label(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    return f"{minutes // 60}시간 {minutes % 60}분" if minutes >= 60 else f"{minutes}분"


def _clock_label(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def board(conn, meetup_id: int) -> dict:
    now = time.time()
    member_by_id = {r["id"]: {"id": r["id"], "nickname": r["nickname"], "gender": r["gender"], "grade": r["grade"],
                              "memo": r["memo"]} for r in conn.execute(
        "SELECT m.id, m.nickname, m.gender, m.grade, a.memo FROM members m JOIN attendees a ON a.member_id = m.id "
        "WHERE a.meetup_id = ?", (meetup_id,))}
    names = {member_id: member["nickname"] for member_id, member in member_by_id.items()}
    courts = conn.execute("SELECT courts FROM meetups WHERE id = ?", (meetup_id,)).fetchone()["courts"]
    active = {r["court"]: r for r in conn.execute("SELECT * FROM games WHERE meetup_id = ? AND ended_at IS NULL",
                                                  (meetup_id,))}
    rest_until = _rest_until(conn, meetup_id)
    court_list = []
    for c in sorted(set(range(1, courts + 1)) | set(active)):  # 코트 수를 줄여도 진행 중인 경기는 보여 준다
        g = active.get(c)
        court_list.append({"court": c, "game_id": g["id"] if g else None,
                           "started_at": g["started_at"] if g else None,
                           "elapsed_clock": _clock_label(now - g["started_at"]) if g else None,
                           "ready_in": None if g or rest_until.get(c, 0) <= now else math.ceil(rest_until[c] - now),
                           "announce": announcement_text(c, [names.get(g[k], "") for k in ("a1", "a2", "b1", "b2")]) if g else None,
                           "teams": [[names.get(g["a1"], "?"), names.get(g["a2"], "?")],
                                     [names.get(g["b1"], "?"), names.get(g["b2"], "?")]] if g else None,
                           "team_players": [[member_by_id.get(g["a1"]), member_by_id.get(g["a2"])],
                                            [member_by_id.get(g["b1"]), member_by_id.get(g["b2"])]] if g else None})
    playing = _playing(conn, meetup_id)
    reserved = _reserved(conn, meetup_id)
    last_end = {}
    for g in conn.execute("SELECT a1, a2, b1, b2, ended_at FROM games WHERE meetup_id = ? AND ended_at IS NOT NULL "
                          "AND canceled = 0", (meetup_id,)):
        for pid in (g["a1"], g["a2"], g["b1"], g["b2"]):
            last_end[pid] = max(last_end.get(pid, 0), g["ended_at"])
    excluded = []
    for r in conn.execute("""SELECT m.id, m.nickname, m.gender, m.grade, a.status, a.games, a.memo FROM attendees a
                             JOIN members m ON m.id = a.member_id WHERE a.meetup_id = ?""", (meetup_id,)):
        if r["status"] == "canceled":
            continue
        if r["id"] in playing or r["id"] in reserved:
            continue
        if r["status"] != "present":
            state, reason = r["status"], STATUS_LABELS[r["status"]]
        elif r["grade"] is None:
            state, reason = "noinfo", "급수 미입력"
        elif r["gender"] is None:
            state, reason = "noinfo", "성별 미입력"
        else:
            continue
        excluded.append({"id": r["id"], "nickname": r["nickname"], "gender": r["gender"], "grade": r["grade"],
                         "games": r["games"], "memo": r["memo"], "state": state, "reason": reason})
    excluded.sort(key=lambda e: (EXCLUDED_ORDER[e["state"]], e["nickname"]))
    queued = queue(conn, meetup_id)
    queue_cards = []
    for order, player in enumerate(queued, 1):
        since = last_end.get(player.id, player.queued_at)
        queue_cards.append({**member_by_id[player.id], "games": player.games, "order": order,
                            "played": player.id in last_end, "since": since, "since_label": _minutes_label(now - since)})
    resv_list = []
    for r in reservations(conn, meetup_id):
        player_ids = [r[key] for key in ("a1", "a2", "b1", "b2")]
        resv_list.append({
            "id": r["id"],
            "no": r["seq"],
            "source": r["source"],
            "waiting_for_game": bool(set(player_ids) & playing),
            "teams": [[names.get(r["a1"], "?"), names.get(r["a2"], "?")],
                      [names.get(r["b1"], "?"), names.get(r["b2"], "?")]],
            "team_players": [[member_by_id.get(r["a1"]), member_by_id.get(r["a2"])],
                             [member_by_id.get(r["b1"]), member_by_id.get(r["b2"])]],
        })
    return {"courts": court_list, "reservations": resv_list, "queue": [names[p.id] for p in queued],
            "queue_cards": queue_cards, "excluded": excluded,
            "auto_reserve": bool(int(get_setting(conn, "auto_reserve", 1))),
            "auto_fill": bool(int(get_setting(conn, "auto_fill", 1)))}


def refill_upcoming(conn) -> None:
    """오늘 이후 시작된 정모의 빈 코트를 대기열로 채운다."""
    for r in conn.execute("SELECT id FROM meetups WHERE date >= ? AND started = 1 AND archived_at IS NULL ORDER BY date, id",
                          (today(),)).fetchall():
        fill_courts(conn, r["id"])


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
