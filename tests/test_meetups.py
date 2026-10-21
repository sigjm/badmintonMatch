from datetime import datetime

import pytest

from badminton.members import create_member, set_info
from badminton.meetups import (SEOUL, add_attendee, add_pasted, board, end_game, get_or_create_meetup,
                               on_info_changed, queue, redraw, redraw_reservation, refill_reservations,
                               reservations, session_day, set_status, start, sync_somoim, today)
from badminton.somoim import SomoimMember


def mk(conn, nick, g, grade, guest=False):
    return create_member(conn, nick, is_guest=guest, gender=g, grade=grade)


def seat(conn, mid, *ids):
    for i in ids:
        add_attendee(conn, mid, i, "paste")


def SM(mid, nick):
    return SomoimMember(mid, nick, frozenset({1}), False)


def member_id_of(conn, nick):
    return conn.execute("SELECT id FROM members WHERE nickname = ?", (nick,)).fetchone()[0]


def status_of(conn, mid, nick):
    return conn.execute("SELECT status FROM attendees WHERE meetup_id = ? AND member_id = ?",
                        (mid, member_id_of(conn, nick))).fetchone()[0]


def current_game(conn, mid):
    return conn.execute("SELECT id FROM games WHERE meetup_id = ? AND ended_at IS NULL", (mid,)).fetchone()[0]


def game_players(conn, gid):
    return list(conn.execute("SELECT a1, a2, b1, b2 FROM games WHERE id = ?", (gid,)).fetchone())


def one_court_game(conn, extra=0):
    mid = get_or_create_meetup(conn, today(), "t", None)
    ids = [mk(conn, "m1", "M", "B"), mk(conn, "m2", "M", "C"), mk(conn, "f1", "F", "B"), mk(conn, "f2", "F", "C")]
    ids += [mk(conn, f"x{i}", "M", "N") for i in range(extra)]  # 초심이라 첫 경기에서 빠지고 대기한다
    seat(conn, mid, *ids)
    start(conn, mid, 1)
    return mid, current_game(conn, mid)


def test_start_fills_courts_mixed(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"m{i}", "M", "B") for i in range(4)], *[mk(conn, f"f{i}", "F", "B") for i in range(4)])
    start(conn, mid, 2)
    b = board(conn, mid)
    assert all(c["teams"] for c in b["courts"]) and b["queue"] == []


def test_end_game_twice_is_noop(conn):  # Review Focus 2
    mid, gid = one_court_game(conn)
    end_game(conn, gid)
    end_game(conn, gid)
    assert [r["games"] for r in conn.execute("SELECT games FROM attendees")] == [1, 1, 1, 1]
    assert conn.execute("SELECT count(*) FROM games").fetchone()[0] == 2  # 끝난 경기 + 새로 채운 경기


def test_short_queue_leaves_court_empty_then_fills(conn):  # Review Focus 3
    mid = get_or_create_meetup(conn, today(), "t", None)
    ids = [mk(conn, f"p{i}", "M", "B") for i in range(3)] + [mk(conn, "nograde", "M", None)]
    seat(conn, mid, *ids)
    start(conn, mid, 1)
    assert board(conn, mid)["courts"][0]["teams"] is None
    on_info_changed(conn, ids[3], set_info(conn, ids[3], "M", "C", by="self"))
    assert board(conn, mid)["courts"][0]["teams"] is not None


def test_excluded_reasons(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    a, b = mk(conn, "a", "M", None), mk(conn, "b", "M", "A")
    seat(conn, mid, a, b)
    set_status(conn, mid, b, "late")
    assert {e["nickname"]: e["reason"] for e in board(conn, mid)["excluded"]} == {"a": "급수 미입력", "b": "지각"}


def test_sync_marks_removed_canceled_and_keeps_guests(conn):
    mid = get_or_create_meetup(conn, "2026-10-23", "금야민+게1", "e1")
    sync_somoim(conn, mid, [SM("x", "엑스"), SM("y", "와이")])
    g = mk(conn, "게스트", "F", "C", guest=True)
    add_attendee(conn, mid, g, "guest")
    assert sync_somoim(conn, mid, [SM("x", "엑스")]) == {"added": 0, "canceled": 1, "restored": 0}
    assert status_of(conn, mid, "와이") == "canceled" and status_of(conn, mid, "게스트") == "present"


def test_sync_restores_rejoined_but_not_manual_status(conn):
    mid = get_or_create_meetup(conn, "2026-10-23", "금야민+게1", "e1")
    sync_somoim(conn, mid, [SM("x", "엑스"), SM("y", "와이")])
    sync_somoim(conn, mid, [SM("x", "엑스")])  # 와이 취소
    set_status(conn, mid, member_id_of(conn, "엑스"), "gone")  # 엑스는 운영진이 귀가 처리
    assert sync_somoim(conn, mid, [SM("x", "엑스"), SM("y", "와이")]) == {"added": 0, "canceled": 0, "restored": 1}
    assert status_of(conn, mid, "와이") == "present" and status_of(conn, mid, "엑스") == "gone"


def test_redraw_keeps_queue_order(conn):
    mid, gid = one_court_game(conn, extra=1)  # 5명: 4명 경기 중, 1명 대기

    def snap():
        return dict(conn.execute("SELECT member_id, queued_at FROM attendees").fetchall())

    before = snap()
    away = game_players(conn, gid)[0]
    set_status(conn, mid, away, "away")
    redraw(conn, gid)
    assert away not in game_players(conn, current_game(conn, mid))
    assert snap() == before


def test_paste_reports_ambiguous(conn):  # Review Focus 4
    mid = get_or_create_meetup(conn, today(), "t", None)
    mk(conn, "철수", "M", "A")
    mk(conn, "철수", "M", "B")
    mk(conn, "영희", "F", "A")
    r = add_pasted(conn, mid, " 영희 \n\n철수\n새사람\n")
    assert r == {"added": ["영희"], "created": ["새사람"], "ambiguous": ["철수"]}


def test_queue_excludes_playing_and_incomplete(conn):
    mid, gid = one_court_game(conn, extra=1)
    assert [p.id for p in queue(conn, mid)] == [member_id_of(conn, "x0")]


def test_session_day_runs_past_midnight():
    assert session_day(datetime(2026, 10, 24, 1, 30, tzinfo=SEOUL)) == "2026-10-23"
    assert session_day(datetime(2026, 10, 24, 7, 0, tzinfo=SEOUL)) == "2026-10-24"


def test_board_keeps_stranded_court_after_reducing(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"p{i}", "M", "B") for i in range(12)])
    start(conn, mid, 3)
    start(conn, mid, 2)
    courts = board(conn, mid)["courts"]
    assert [c["court"] for c in courts] == [1, 2, 3] and courts[2]["teams"] is not None


def test_redraw_gives_new_game_and_stale_end_is_noop(conn):
    mid, gid = one_court_game(conn, extra=1)
    set_status(conn, mid, game_players(conn, gid)[0], "away")
    redraw(conn, gid)
    new = current_game(conn, mid)
    end_game(conn, gid)  # 다른 폰의 오래된 화면에서 누른 경기 끝
    assert new != gid and current_game(conn, mid) == new
    assert {r["games"] for r in conn.execute("SELECT games FROM attendees")} == {0}


def test_resave_same_info_keeps_queue_spot(conn):
    mid, gid = one_court_game(conn, extra=1)
    waiting = member_id_of(conn, "x0")
    before = conn.execute("SELECT queued_at FROM attendees WHERE member_id = ?", (waiting,)).fetchone()[0]
    on_info_changed(conn, waiting, set_info(conn, waiting, "M", "N", by="self"))
    assert conn.execute("SELECT queued_at FROM attendees WHERE member_id = ?", (waiting,)).fetchone()[0] == before


def test_pasted_member_adopted_by_later_import(conn):
    mid = get_or_create_meetup(conn, "2026-10-23", "금야민+게1", "e1")
    add_pasted(conn, mid, "새사람")
    m = member_id_of(conn, "새사람")
    set_info(conn, m, "F", "B", by="admin")
    sync_somoim(conn, mid, [SM("z", "새사람")])
    assert conn.execute("SELECT count(*) FROM members WHERE nickname = '새사람'").fetchone()[0] == 1
    assert conn.execute("SELECT somoim_mid, grade FROM members WHERE id = ?", (m,)).fetchone()[:] == ("z", "B")


def test_paste_ignores_guests_with_same_name(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    mk(conn, "철수", "M", "A", guest=True)
    mk(conn, "철수", "M", "B")
    assert add_pasted(conn, mid, "철수")["added"] == ["철수"]


def test_next_round_does_not_repeat_same_four(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    seat(conn, mid, *[mk(conn, f"p{i}", "M", "A") for i in range(8)])
    start(conn, mid, 1)
    first = set(game_players(conn, current_game(conn, mid)))
    end_game(conn, current_game(conn, mid))   # 대기하던 4명이 친다
    end_game(conn, current_game(conn, mid))   # 이제 8명 모두 1판 → 직전 멤버끼리 다시 묶이지 않아야 함
    assert set(game_players(conn, current_game(conn, mid))) != first


def test_redrawn_game_excluded_from_recent_mates(conn):
    mid, gid = one_court_game(conn, extra=1)
    set_status(conn, mid, game_players(conn, gid)[0], "away")
    redraw(conn, gid)
    # redraw된 게임은 canceled=1이므로 _recent_mates에 남지 않는다
    from badminton.meetups import _recent_mates
    assert _recent_mates(conn, mid) == {}


def test_guest_reactivated_on_readd(conn):
    mid = get_or_create_meetup(conn, today(), "t", None)
    g = mk(conn, "게스트1", "M", "A", guest=True)
    add_attendee(conn, mid, g, "guest")
    set_status(conn, mid, g, "away")
    assert conn.execute("SELECT status FROM attendees WHERE meetup_id = ? AND member_id = ?", (mid, g)).fetchone()[0] == "away"
    # 게스트 다시 추가 시 present로 복구
    add_attendee(conn, mid, g, "guest")
    assert conn.execute("SELECT status FROM attendees WHERE meetup_id = ? AND member_id = ?", (mid, g)).fetchone()[0] == "present"


def test_on_info_changed_with_specific_meetup_id(conn):
    future_mid = get_or_create_meetup(conn, "2099-12-31", "미래정모", None)
    m = create_member(conn, "미래회원")  # 성별, 급수 없음
    add_attendee(conn, future_mid, m, "paste")
    before_q = conn.execute("SELECT queued_at FROM attendees WHERE meetup_id = ? AND member_id = ?", (future_mid, m)).fetchone()[0]
    # 성별/급수 채우기
    changed = set_info(conn, m, "M", "B", by="admin")
    on_info_changed(conn, m, changed, meetup_id=future_mid)
    after_q = conn.execute("SELECT queued_at FROM attendees WHERE meetup_id = ? AND member_id = ?", (future_mid, m)).fetchone()[0]
    assert after_q >= before_q


def test_assign_manual_validations(conn):
    from badminton.meetups import assign_manual
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 처음에는 3명만 참석시켜 코트 1, 2가 모두 빈 상태로 시작되게 함
    p1 = mk(conn, "선수1", "M", "A")
    p2 = mk(conn, "선수2", "M", "B")
    p3 = mk(conn, "선수3", "F", "A")
    for p in (p1, p2, p3):
        add_attendee(conn, mid, p, "paste")
    start(conn, mid, 2)  # 코트 2면 시작 (4명 미만이므로 자동 매칭 없이 코트 1, 2 모두 비어 있음)

    # 추가 선수 등록
    p4 = mk(conn, "선수4", "F", "B")
    p5 = create_member(conn, "선수5", grade="A")  # gender is None
    p6 = create_member(conn, "선수6", gender="M")  # grade is None
    p7 = mk(conn, "선수7", "M", "C")
    for p in (p4, p5, p6, p7):
        add_attendee(conn, mid, p, "paste")

    # 1. 코트 범위 밖 거부
    with pytest.raises(ValueError, match="현재 운영 중인 코트"):
        assign_manual(conn, mid, 0, [p1, p2, p3, p4])
    with pytest.raises(ValueError, match="현재 운영 중인 코트"):
        assign_manual(conn, mid, 3, [p1, p2, p3, p4])

    # 2. 같은 사람 중복 거부
    with pytest.raises(ValueError, match="서로 다른 선수 네 명"):
        assign_manual(conn, mid, 1, [p1, p1, p3, p4])

    # 3. 참석 상태가 아닌 사람 거부
    set_status(conn, mid, p1, "away")
    with pytest.raises(ValueError, match="현재 참석 중인 선수만"):
        assign_manual(conn, mid, 1, [p1, p2, p3, p4])
    set_status(conn, mid, p1, "present")

    # 4. 급수/성별 비어 있는 사람 거부
    with pytest.raises(ValueError, match="성별과 급수"):
        assign_manual(conn, mid, 1, [p1, p2, p3, p5])
    with pytest.raises(ValueError, match="성별과 급수"):
        assign_manual(conn, mid, 1, [p1, p2, p3, p6])

    # 5. 정상 배정: p1, p2, p3, p4 -> 코트 1에 배정 (팀 A: p1, p2 / 팀 B: p3, p4)
    assign_manual(conn, mid, 1, [p1, p2, p3, p4])
    game = conn.execute("SELECT * FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()
    assert game is not None
    assert (game["a1"], game["a2"], game["b1"], game["b2"]) == (p1, p2, p3, p4)

    # 6. 이미 경기 중인 사람 거부
    p8 = mk(conn, "선수8", "F", "C")
    p9 = mk(conn, "선수9", "M", "D")
    p10 = mk(conn, "선수10", "F", "D")
    for p in (p8, p9, p10):
        add_attendee(conn, mid, p, "paste")
    with pytest.raises(ValueError, match="다른 코트에서 경기 중인"):
        assign_manual(conn, mid, 2, [p1, p7, p8, p9])


def test_reservations_max_capped_by_courts(conn):
    # start 후 대기 8명, 코트 1 -> 예약 최대 1개 (코트 수만큼)
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 총 12명 (남6, 여6)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(6)] + [mk(conn, f"f{i}", "F", "A") for i in range(6)]
    seat(conn, mid, *players)
    start(conn, mid, 1)

    # 코트 1면에 4명 경기 중
    resvs = reservations(conn, mid)
    assert len(resvs) == 1
    assert resvs[0]["seq"] == 1

    # 남은 대기자는 4명 (예약된 4명은 queue에서 제외)
    q = queue(conn, mid)
    assert len(q) == 4

    # board() 계약 확인
    b = board(conn, mid)
    assert "reservations" in b
    assert len(b["reservations"]) == 1
    assert b["reservations"][0]["no"] == 1
    assert len(b["reservations"][0]["teams"]) == 2
    assert len(b["queue"]) == 4


def test_reservations_one_slot_when_eight_waiting_on_three_courts(conn):
    # (1) 20명·코트 3 시작 직후 대기 8명이면 예약은 1개, 예약 안 된 대기 4명.
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 총 20명 (코트 3면에 12명, 대기 8명)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(10)] + [mk(conn, f"f{i}", "F", "A") for i in range(10)]
    seat(conn, mid, *players)
    start(conn, mid, 3)

    resvs = reservations(conn, mid)
    assert len(resvs) == 1
    assert resvs[0]["seq"] == 1

    # 남은 대기자(예약 안 된 사람)는 4명
    assert len(queue(conn, mid)) == 4
    b = board(conn, mid)
    assert len(b["reservations"]) == 1
    assert len(b["queue"]) == 4


def test_end_game_promotes_reservation_and_mixes_eight_candidates(conn):
    # (2) 경기 하나가 끝나면 예약1이 코트로 들어가고, 새 예약이 8명 후보에서 만들어지며 방금 끝난 4명과 완전히 같은 조가 아니다.
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 총 20명 (코트 3면에 12명, 대기 8명)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(10)] + [mk(conn, f"f{i}", "F", "A") for i in range(10)]
    seat(conn, mid, *players)
    start(conn, mid, 3)

    resv1 = reservations(conn, mid)[0]
    r1_four = {resv1["a1"], resv1["a2"], resv1["b1"], resv1["b2"]}

    # 코트 1 경기 종료: 코트 1에 있던 4명
    g1 = conn.execute("SELECT id, a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()
    g1_id = g1["id"]
    g1_four = {g1["a1"], g1["a2"], g1["b1"], g1["b2"]}
    end_game(conn, g1_id)

    # 1. 예약 1이 코트 1로 들어감
    g1_new = conn.execute("SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()
    assert set(g1_new) == r1_four

    # 2. 새 예약이 8명 후보(이전 대기 4명 + 방금 끝난 4명)에서 만들어짐
    new_resvs = reservations(conn, mid)
    assert len(new_resvs) == 1
    new_r_four = {new_resvs[0]["a1"], new_resvs[0]["a2"], new_resvs[0]["b1"], new_resvs[0]["b2"]}

    # 방금 끝난 4명과 완전히 같은 조가 아님 (섞였음)
    assert new_r_four != g1_four


def test_end_game_promotes_reservation_and_shifts_and_refills(conn):
    # 경기 끝 -> 예약 1이 그 코트로, 예약 번호가 당겨지고 새 예약이 채워진다.
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 총 28명 (코트 3면에 12명, 대기 16명 -> 예약 3개 최대)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(14)] + [mk(conn, f"f{i}", "F", "A") for i in range(14)]
    seat(conn, mid, *players)
    start(conn, mid, 3)

    resvs_before = reservations(conn, mid)
    assert len(resvs_before) == 3
    r1_four = {resvs_before[0]["a1"], resvs_before[0]["a2"], resvs_before[0]["b1"], resvs_before[0]["b2"]}
    r2_id = resvs_before[1]["id"]
    r2_four = {resvs_before[1]["a1"], resvs_before[1]["a2"], resvs_before[1]["b1"], resvs_before[1]["b2"]}

    # 코트 1 경기 종료
    g1 = conn.execute("SELECT id FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()[0]
    end_game(conn, g1)

    # 1. 예약 1의 선수들이 코트 1의 새 경기에 투입됨
    g1_new = conn.execute("SELECT a1, a2, b1, b2 FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()
    assert set(g1_new) == r1_four

    # 2. 예약 번호가 당겨짐 (기존 예약 2가 이제 seq 1이 됨)
    resvs_after = reservations(conn, mid)
    assert len(resvs_after) == 3
    assert resvs_after[0]["id"] == r2_id
    assert resvs_after[0]["seq"] == 1
    assert {resvs_after[0]["a1"], resvs_after[0]["a2"], resvs_after[0]["b1"], resvs_after[0]["b2"]} == r2_four

    # 3. 새 예약이 마지막 번호(seq 3)로 채워짐
    assert [r["seq"] for r in resvs_after] == [1, 2, 3]


def test_reservations_keep_members_when_other_games_end(conn):
    # 예약은 다른 경기가 끝나도 멤버가 바뀌지 않는다.
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 총 20명 (코트 2면(8명), 대기 12명 -> 예약 2개)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(10)] + [mk(conn, f"f{i}", "F", "A") for i in range(10)]
    seat(conn, mid, *players)
    start(conn, mid, 2)

    r_init = reservations(conn, mid)
    assert len(r_init) == 2
    r2_members = (r_init[1]["a1"], r_init[1]["a2"], r_init[1]["b1"], r_init[1]["b2"])

    # 코트 1 경기 종료 -> 예약 1이 코트 1로 승격
    g1 = conn.execute("SELECT id FROM games WHERE meetup_id = ? AND court = 1 AND ended_at IS NULL", (mid,)).fetchone()[0]
    end_game(conn, g1)

    # 기존 예약 2는 번호만 1로 당겨지고 4명 멤버는 그대로 유지되어야 함
    r_next = reservations(conn, mid)
    assert (r_next[0]["a1"], r_next[0]["a2"], r_next[0]["b1"], r_next[0]["b2"]) == r2_members


def test_away_status_drops_reservation_and_keeps_others_queue_time(conn):
    # 예약된 사람을 away로 바꾸면 그 예약만 사라지고 나머지 3명은 queued_at이 그대로다.
    mid = get_or_create_meetup(conn, today(), "t", None)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(6)] + [mk(conn, f"f{i}", "F", "A") for i in range(6)]
    seat(conn, mid, *players)
    start(conn, mid, 1)  # 코트 1면(4명), 대기 8명 -> 예약 1개, 대기 4명

    r1 = reservations(conn, mid)[0]
    p_away = r1["a1"]
    other_3 = [r1["a2"], r1["b1"], r1["b2"]]
    times_before = {p: conn.execute("SELECT queued_at FROM attendees WHERE meetup_id = ? AND member_id = ?", (mid, p)).fetchone()[0] for p in other_3}

    # a1 선수를 away로 변경
    set_status(conn, mid, p_away, "away")

    # 1. 예약 1이 해제되고, 새로 채워진 예약에는 away된 사람이 없음
    resvs = reservations(conn, mid)
    if resvs:
        assert p_away not in (resvs[0]["a1"], resvs[0]["a2"], resvs[0]["b1"], resvs[0]["b2"])

    # 2. 나머지 3명의 queued_at은 변경되지 않고 그대로 유지
    for p in other_3:
        t_after = conn.execute("SELECT queued_at FROM attendees WHERE meetup_id = ? AND member_id = ?", (mid, p)).fetchone()[0]
        assert t_after == times_before[p]


def test_redraw_reservation(conn):
    # redraw_reservation
    mid = get_or_create_meetup(conn, today(), "t", None)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(6)] + [mk(conn, f"f{i}", "F", "A") for i in range(6)]
    seat(conn, mid, *players)
    start(conn, mid, 1)

    r1 = reservations(conn, mid)[0]
    r1_id = r1["id"]
    t1 = r1["created_at"]
    ret_mid = redraw_reservation(conn, r1_id)
    assert ret_mid == mid

    # 해당 예약을 지우고 새 예약으로 다시 채움
    r_new = reservations(conn, mid)
    assert len(r_new) == 1
    assert r_new[0]["seq"] == 1
    assert r_new[0]["created_at"] >= t1

    # 없는 id일 때는 None
    assert redraw_reservation(conn, 99999) is None


def test_queue_excludes_reserved_players(conn):
    # queue()에서 예약된 사람이 빠진다.
    mid = get_or_create_meetup(conn, today(), "t", None)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(6)] + [mk(conn, f"f{i}", "F", "A") for i in range(6)]
    seat(conn, mid, *players)
    start(conn, mid, 1)  # 코트 1면(4명), 예약 1(4명), 대기열 4명

    resv_players = {reservations(conn, mid)[0][k] for k in ("a1", "a2", "b1", "b2")}
    q_players = {p.id for p in queue(conn, mid)}
    # 예약된 선수들은 대기열에 포함되지 않아야 함
    assert resv_players.isdisjoint(q_players)
    assert len(q_players) == 4


def test_reservation_does_not_make_uneven_gender_ratios(conn):
    # 남1여3 (또는 여1남3)이 예약으로도 만들어지지 않는다.
    mid = get_or_create_meetup(conn, today(), "t", None)
    # 경기 중 4명 (남2 여2) + 남은 대기 4명 (남1 여3)
    p_game = [mk(conn, "g_m1", "M", "A"), mk(conn, "g_m2", "M", "A"), mk(conn, "g_f1", "F", "A"), mk(conn, "g_f2", "F", "A")]
    p_uneven = [mk(conn, "u_m1", "M", "A"), mk(conn, "u_f1", "F", "A"), mk(conn, "u_f2", "F", "A"), mk(conn, "u_f3", "F", "A")]
    seat(conn, mid, *p_game, *p_uneven)
    start(conn, mid, 1)

    # 남1여3은 성비 규칙에 위배되므로 예약이 생기지 않음
    assert len(reservations(conn, mid)) == 0
    # 4명은 예약되지 않은 채 대기열에 그대로 남음
    assert len(queue(conn, mid)) == 4


def test_info_cleared_drops_reservation(conn):
    # 급수나 성별이 비워지면 그 회원이 속한 예약이 해제된다
    mid = get_or_create_meetup(conn, today(), "t", None)
    players = [mk(conn, f"m{i}", "M", "A") for i in range(6)] + [mk(conn, f"f{i}", "F", "A") for i in range(6)]
    seat(conn, mid, *players)
    start(conn, mid, 1)  # 코트 1면(4명), 예약 1개(4명), 대기 4명

    r1 = reservations(conn, mid)[0]
    target_member = r1["a1"]

    # target_member의 급수를 None으로 변경
    changed = set_info(conn, target_member, "M", None, by="admin")
    on_info_changed(conn, target_member, changed, meetup_id=mid)

    # 1. 새로 채워진 예약에는 target_member가 없어야 함
    new_resvs = reservations(conn, mid)
    if new_resvs:
        assert target_member not in (new_resvs[0]["a1"], new_resvs[0]["a2"], new_resvs[0]["b1"], new_resvs[0]["b2"])

    # 2. target_member는 급수 미입력으로 대진 제외(excluded)에 들어가야 함
    b = board(conn, mid)
    assert any(x["nickname"] == "m0" or x.get("id") == target_member for x in b["excluded"])
