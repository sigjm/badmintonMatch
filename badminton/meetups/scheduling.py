"""코트·예약·경기: 빈 코트 채우기, 예약 만들기·고치기·지우기, 게임 끝내기·다시 뽑기, 코트 상태."""

import time

from ..db import get_setting, transaction
from ..matching import MIX_WINDOW, pick_four, split_teams
from .common import COURT_STATE_LABELS, _court_states, _playing, _recent_mates, _reserved, _rest_until, queue, today


def get_court_state(conn, meetup_id: int, court: int) -> str:
    row = conn.execute("SELECT state FROM court_states WHERE meetup_id = ? AND court = ?",
                       (meetup_id, court)).fetchone()
    return row["state"] if row else "game"


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


def _prefer_mixed(conn, meetup_id: int, mix_ratio: float) -> bool:
    """지금까지 잡힌 경기·예약의 혼복 비율을 보고 이번 매칭에서 혼복을 우선할지 정한다."""
    if mix_ratio <= 0:
        return False
    female = {r["id"] for r in conn.execute("SELECT id FROM members WHERE gender = 'F'")}
    rows = conn.execute(
        "SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND canceled = 0 "
        "UNION ALL "
        "SELECT a1, a2, b1, b2 FROM reservations WHERE meetup_id = ?",
        (meetup_id, meetup_id),
    ).fetchall()
    mixed = sum(sum(p in female for p in row) == 2 for row in rows)
    return mixed < round((mix_ratio / 100) * (len(rows) + 1))


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

    courts = max(0, meetup["courts"] - conn.execute(
        "SELECT count(*) FROM court_states WHERE meetup_id = ? AND court BETWEEN 1 AND ?",
        (meetup_id, meetup["courts"])).fetchone()[0])
    auto_rows = conn.execute("SELECT id FROM reservations WHERE meetup_id = ? AND source = 'auto' "
                             "ORDER BY seq, id", (meetup_id,)).fetchall()
    for row in auto_rows[courts:]:
        conn.execute("DELETE FROM reservations WHERE id = ?", (row["id"],))
    if len(auto_rows) > courts:
        _renumber_reservations(conn, meetup_id)
    if meetup["resv_hold"]:  # 운영진이 예약을 삭제했으면 다음 경기가 코트에 들어갈 때까지 새 예약을 만들지 않는다
        return
    auto_count = conn.execute("SELECT count(*) FROM reservations WHERE meetup_id = ? AND source = 'auto'",
                              (meetup_id,)).fetchone()[0]
    if auto_count >= courts:
        return
    offset, mix_ratio = get_setting(conn, "female_offset"), get_setting(conn, "mix_ratio")
    recent = _recent_mates(conn, meetup_id)
    playing = _playing(conn, meetup_id)
    while auto_count < courts:
        q = queue(conn, meetup_id)
        if len(q) < 8:
            break
        four = pick_four(q, MIX_WINDOW, offset, recent, avoid,
                         prefer_mixed=_prefer_mixed(conn, meetup_id, mix_ratio))
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
    from .. import audit

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
    blocked_courts = active_courts | set(_court_states(conn, meetup_id))
    court = next((number for number in range(1, meetup["courts"] + 1) if number not in blocked_courts), None)
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


def fill_courts(conn, meetup_id: int, force: bool = False, avoid: frozenset[int] | None = None,
                ready_courts: set[int] | None = None) -> None:
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
    busy |= set(_court_states(conn, meetup_id))
    if not force:
        now = time.time()
        busy |= {c for c, until in _rest_until(conn, meetup_id).items()
                 if until > now and (not ready_courts or c not in ready_courts)}
    offset, mix_ratio = get_setting(conn, "female_offset"), get_setting(conn, "mix_ratio")
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
        four = pick_four(queue(conn, meetup_id), MIX_WINDOW, offset, recent, avoid,
                         prefer_mixed=_prefer_mixed(conn, meetup_id, mix_ratio))
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


def set_court_state(conn, meetup_id: int, court: int, state: str) -> None:
    if state not in COURT_STATE_LABELS:
        raise ValueError("코트 상태를 확인해 주세요")
    meetup = conn.execute("SELECT courts, archived_at FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    if meetup is None or meetup["archived_at"] is not None:
        raise ValueError("활성 정모를 찾을 수 없습니다")
    if isinstance(court, bool) or not isinstance(court, int) or not (1 <= court <= meetup["courts"]):
        raise ValueError("유효하지 않은 코트 번호입니다")
    playing = conn.execute("SELECT 1 FROM games WHERE meetup_id = ? AND court = ? AND ended_at IS NULL",
                           (meetup_id, court)).fetchone()
    if playing is not None:
        raise ValueError("경기 중인 코트는 상태를 바꿀 수 없습니다")
    if state == "game":
        conn.execute("DELETE FROM court_states WHERE meetup_id = ? AND court = ?", (meetup_id, court))
        fill_courts(conn, meetup_id, ready_courts={court})
    else:
        conn.execute(
            "INSERT INTO court_states(meetup_id, court, state) VALUES (?, ?, ?) "
            "ON CONFLICT(meetup_id, court) DO UPDATE SET state = excluded.state",
            (meetup_id, court, state))
        refill_reservations(conn, meetup_id)


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


def refill_upcoming(conn) -> None:
    """오늘 이후 시작된 정모의 빈 코트를 대기열로 채운다."""
    for r in conn.execute("SELECT id FROM meetups WHERE date >= ? AND started = 1 AND archived_at IS NULL ORDER BY date, id",
                          (today(),)).fetchall():
        fill_courts(conn, r["id"])
